import asyncio
import time
import uuid

from starlette.concurrency import run_in_threadpool

from providers import AgentProviderError
from translation_agents import translate_segment
from auth import redact_secret_text
from config import TRANSLATION_MODEL


TRANSLATION_QUEUE = asyncio.Queue()
TRANSLATION_JOBS = {}
TRANSLATION_WORKER = None
MAX_TRANSLATION_JOBS = 500


def public_translation_job(job):
    payload = {
        "translation_job_id": job["translation_job_id"],
        "id": job["payload"].get("id", ""),
        "status": job["status"],
    }
    if job.get("result"):
        payload.update(job["result"])
        payload["translation_job_id"] = job["translation_job_id"]
        payload["status"] = job["status"]
    if job.get("error"):
        payload["error"] = job["error"]
        payload["error_code"] = job.get("error_code", "TRANSLATION_FAILED")
        payload["reason"] = job["error"]
    return payload


def prune_translation_jobs():
    if len(TRANSLATION_JOBS) <= MAX_TRANSLATION_JOBS:
        return
    finished = sorted(
        (job for job in TRANSLATION_JOBS.values() if job["status"] not in ("queued", "processing")),
        key=lambda job: job["updated_at"],
    )
    for job in finished[:len(TRANSLATION_JOBS) - MAX_TRANSLATION_JOBS]:
        TRANSLATION_JOBS.pop(job["translation_job_id"], None)


async def translation_worker():
    while True:
        job_id = await TRANSLATION_QUEUE.get()
        job = TRANSLATION_JOBS.get(job_id)
        try:
            if not job:
                continue
            job["status"] = "processing"
            job["updated_at"] = time.time()
            body = job["payload"]
            started = time.time()
            print(f"[translation] start job={job_id} id={body.get('id', '')} model={TRANSLATION_MODEL}", flush=True)
            result = await run_in_threadpool(
                translate_segment,
                body.get("id", ""),
                body.get("source_text", ""),
                body.get("context_before", ""),
                body.get("context_after", ""),
                body.get("target_language", "zh-TW"),
                bool(body.get("force_refresh")),
            )
            job["result"] = result
            job["status"] = result.get("status") or "translated"
            job["updated_at"] = time.time()
            print(f"[translation] done job={job_id} id={body.get('id', '')} status={job['status']} seconds={job['updated_at'] - started:.1f}", flush=True)
        except AgentProviderError as exc:
            if job:
                job["status"] = "failed"
                job["error_code"] = exc.error_code
                job["error"] = redact_secret_text(str(exc))
                job["updated_at"] = time.time()
        except Exception as exc:
            if job:
                job["status"] = "failed"
                job["error_code"] = "TRANSLATION_FAILED"
                job["error"] = redact_secret_text(str(exc))
                job["updated_at"] = time.time()
        finally:
            TRANSLATION_QUEUE.task_done()


async def start_translation_worker():
    global TRANSLATION_WORKER
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
    prune_translation_jobs()
    job_id = f"translation-{uuid.uuid4().hex[:12]}"
    job = {
        "translation_job_id": job_id,
        "payload": payload,
        "status": "queued",
        "created_at": time.time(),
        "updated_at": time.time(),
    }
    TRANSLATION_JOBS[job_id] = job
    await TRANSLATION_QUEUE.put(job_id)
    return job


def get_translation_job(job_id):
    return TRANSLATION_JOBS.get(job_id)
