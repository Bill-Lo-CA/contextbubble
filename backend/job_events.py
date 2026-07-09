import json

from auth import redact_secret_text
from config import now_iso
from db import connect_db


def add_preparation_event(job_id, event_type, stage=None, metadata=None):
    safe_metadata = json.loads(redact_secret_text(json.dumps(metadata or {}, sort_keys=True)))
    with connect_db() as conn:
        conn.execute(
            "insert into preparation_events (job_id, event_type, stage, metadata, created_at) values (?, ?, ?, ?, ?)",
            (job_id, event_type, stage, json.dumps(safe_metadata, sort_keys=True), now_iso()),
        )


def preparation_events(job_id):
    with connect_db() as conn:
        rows = conn.execute(
            "select * from preparation_events where job_id = ? order by created_at, event_id",
            (job_id,),
        ).fetchall()
    return [
        {
            "event_id": row["event_id"],
            "job_id": row["job_id"],
            "event_type": row["event_type"],
            "stage": row["stage"],
            "metadata": json.loads(row["metadata"] or "{}"),
            "created_at": row["created_at"],
        }
        for row in rows
    ]
