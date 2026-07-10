import asyncio
import hashlib
import json
import time
import uuid

from starlette.concurrency import run_in_threadpool

from auth import redact_secret_text
from config import TRANSLATION_MODEL
from db import connect_db
from providers import AgentProviderError
from translation_agents import translate_segment


QUEUE_CAPACITY = 100
MAX_ATTEMPTS = 2
TRANSLATION_QUEUE = asyncio.Queue(maxsize=QUEUE_CAPACITY)
TRANSLATION_WORKER = None


class TranslationQueueFull(RuntimeError):
    pass


def job_key(payload):
    effective = {key: payload.get(key, "") for key in ("id", "source_text", "context_before", "context_after", "target_language", "force_refresh")}
    return hashlib.sha256(json.dumps(effective, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def row_to_job(row):
    if not row:
        return None
    job = {"translation_job_id": row["job_id"], "payload": json.loads(row["payload_json"]), "status": row["status"]}
    if row["result_json"]:
        job["result"] = json.loads(row["result_json"])
    if row["error_message"]:
        job.update(error=row["error_message"], error_code=row["error_code"] or "TRANSLATION_FAILED")
    return job


def public_translation_job(job):
    payload = {"translation_job_id": job["translation_job_id"], "id": job["payload"].get("id", ""), "status": job["status"]}
    if job.get("result"):
        payload.update(job["result"])
        payload.update(translation_job_id=job["translation_job_id"], status=job["status"])
    if job.get("error"):
        payload.update(error=job["error"], error_code=job.get("error_code", "TRANSLATION_FAILED"), reason=job["error"])
    return payload


def get_translation_job(job_id):
    with connect_db() as conn:
        return row_to_job(conn.execute("select * from translation_jobs where job_id = ?", (job_id,)).fetchone())


def update_job(job_id, **values):
    allowed = {"status", "result_json", "error_code", "error_message", "attempts", "updated_at"}
    if not values.keys() <= allowed:
        raise ValueError("invalid translation job field")
    with connect_db() as conn:
        conn.execute(f"update translation_jobs set {', '.join(f'{key} = ?' for key in values)} where job_id = ?", (*values.values(), job_id))


async def translation_worker():
    while True:
        job_id = await TRANSLATION_QUEUE.get()
        attempts = MAX_ATTEMPTS
        try:
            job = get_translation_job(job_id)
            if not job or job["status"] != "queued":
                continue
            with connect_db() as conn:
                row = conn.execute("select attempts from translation_jobs where job_id = ?", (job_id,)).fetchone()
            attempts = row["attempts"] + 1
            update_job(job_id, status="processing", attempts=attempts, updated_at=time.time(), error_code=None, error_message=None)
            body = job["payload"]
            result = await run_in_threadpool(translate_segment, body.get("id", ""), body.get("source_text", ""), body.get("context_before", ""), body.get("context_after", ""), body.get("target_language", "zh-TW"), bool(body.get("force_refresh")))
            update_job(job_id, status=result.get("status") or "translated", result_json=json.dumps(result, ensure_ascii=False), updated_at=time.time())
        except AgentProviderError as exc:
            if attempts < MAX_ATTEMPTS:
                update_job(job_id, status="queued", error_code=exc.error_code, error_message=redact_secret_text(str(exc)), updated_at=time.time())
                await asyncio.sleep(0.2)
                await TRANSLATION_QUEUE.put(job_id)
            else:
                update_job(job_id, status="failed", error_code=exc.error_code, error_message=redact_secret_text(str(exc)), updated_at=time.time())
        except Exception as exc:
            update_job(job_id, status="failed", error_code="TRANSLATION_FAILED", error_message=redact_secret_text(str(exc)), updated_at=time.time())
        finally:
            TRANSLATION_QUEUE.task_done()


async def start_translation_worker():
    global TRANSLATION_WORKER
    now = time.time()
    with connect_db() as conn:
        conn.execute("update translation_jobs set status = 'queued', error_code = 'STALE_PROCESSING_RESET', updated_at = ? where status = 'processing'", (now,))
        queued = conn.execute("select job_id from translation_jobs where status = 'queued' order by created_at limit ?", (QUEUE_CAPACITY,)).fetchall()
    for row in queued:
        if not TRANSLATION_QUEUE.full():
            TRANSLATION_QUEUE.put_nowait(row["job_id"])
    if not TRANSLATION_WORKER or TRANSLATION_WORKER.done():
        TRANSLATION_WORKER = asyncio.create_task(translation_worker())


async def stop_translation_worker():
    global TRANSLATION_WORKER
    if not TRANSLATION_WORKER:
        return
    TRANSLATION_WORKER.cancel()
    try:
        await TRANSLATION_WORKER
    except asyncio.CancelledError:
        pass
    TRANSLATION_WORKER = None


async def create_translation_job(payload):
    key = job_key(payload)
    with connect_db() as conn:
        existing = conn.execute("select * from translation_jobs where job_key = ? and status in ('queued','processing') order by created_at limit 1", (key,)).fetchone()
        if existing:
            return row_to_job(existing)
        if TRANSLATION_QUEUE.full():
            raise TranslationQueueFull("translation queue is full")
        now = time.time()
        job_id = f"translation-{uuid.uuid4().hex[:12]}"
        conn.execute("insert into translation_jobs (job_id, job_key, segment_id, payload_json, status, attempts, created_at, updated_at) values (?, ?, ?, ?, 'queued', 0, ?, ?)", (job_id, key, payload.get("id", ""), json.dumps(payload, ensure_ascii=False), now, now))
    await TRANSLATION_QUEUE.put(job_id)
    return get_translation_job(job_id)
