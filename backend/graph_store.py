import json

from config import GRAPH_VERSION, now_iso
from db import connect_db


def graph_extraction_cache_key(video_id, content_hash):
    return f"{video_id}:{content_hash}:{GRAPH_VERSION}"


def latest_ready_extraction_by_cache_key(cache_key):
    # cache_key is a lookup key; force-refresh jobs may share it.
    with connect_db() as conn:
        row = conn.execute(
            "select * from kg_extraction_jobs where cache_key = ? and status = 'ready' order by updated_at desc limit 1",
            (cache_key,),
        ).fetchone()
    return dict(row) if row else None


def upsert_extraction_job(job_id, video_id, transcript_id, cache_key, status, stage, node_count=0, edge_count=0, error_code=None, message=None):
    timestamp = now_iso()
    with connect_db() as conn:
        conn.execute(
            """insert into kg_extraction_jobs (job_id, video_id, transcript_id, cache_key, status, stage, node_count, edge_count, error_code, message, created_at, updated_at)
            values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            on conflict(job_id) do update set
                video_id = excluded.video_id, transcript_id = excluded.transcript_id,
                cache_key = excluded.cache_key, status = excluded.status, stage = excluded.stage,
                node_count = excluded.node_count, edge_count = excluded.edge_count,
                error_code = excluded.error_code, message = excluded.message, updated_at = excluded.updated_at""",
            (job_id, video_id, transcript_id, cache_key, status, stage, node_count, edge_count, error_code, message, timestamp, timestamp),
        )


EXTRACTION_JOB_UPDATE_COLUMNS = {"status", "stage", "node_count", "edge_count", "error_code", "message"}


def update_extraction_job(job_id, **values):
    if not values:
        return
    unknown = set(values) - EXTRACTION_JOB_UPDATE_COLUMNS
    if unknown:
        raise ValueError(f"invalid extraction job update fields: {', '.join(sorted(unknown))}")
    values["updated_at"] = now_iso()
    assignments = ", ".join(f"{key} = ?" for key in values)
    with connect_db() as conn:
        conn.execute(f"update kg_extraction_jobs set {assignments} where job_id = ?", (*values.values(), job_id))


def extraction_job_payload(job_id):
    with connect_db() as conn:
        row = conn.execute(
            """select preparation_jobs.job_id, preparation_jobs.video_id, preparation_jobs.status as job_status,
                      kg_extraction_jobs.status as extraction_status, kg_extraction_jobs.stage, kg_extraction_jobs.node_count,
                      kg_extraction_jobs.edge_count, kg_extraction_jobs.error_code, kg_extraction_jobs.message
               from preparation_jobs left join kg_extraction_jobs on kg_extraction_jobs.job_id = preparation_jobs.job_id
               where preparation_jobs.job_id = ? and preparation_jobs.job_kind = 'graph_extraction'""",
            (job_id,),
        ).fetchone()
    if not row:
        return None
    return {
        "job_id": row["job_id"],
        "video_id": row["video_id"],
        "status": row["extraction_status"] or row["job_status"],
        "stage": row["stage"] or "queued",
        "node_count": row["node_count"] or 0,
        "edge_count": row["edge_count"] or 0,
        "error_code": row["error_code"],
        "message": row["message"],
    }


def ensure_relation_type(conn, relation_type, description=""):
    conn.execute(
        "insert or ignore into kg_relation_types (relation_type, description, status, created_at) values (?, ?, 'approved', ?)",
        (relation_type, description or relation_type, now_iso()),
    )


def save_nodes_and_edges(job_id, video_id, transcript_id, nodes, edges):
    timestamp = now_iso()
    with connect_db() as conn:
        conn.execute("delete from kg_nodes where extraction_job_id = ?", (job_id,))
        for node in nodes:
            conn.execute(
                """insert into kg_nodes (extraction_job_id, node_id, canonical_name, node_type, short_summary, confidence, created_at, updated_at)
                values (?, ?, ?, ?, ?, ?, ?, ?)
                on conflict(extraction_job_id, node_id) do update set
                    short_summary = excluded.short_summary, confidence = excluded.confidence, updated_at = excluded.updated_at""",
                (job_id, node["node_id"], node["canonical_name"], node["node_type"], node["short_summary"], node["confidence"], timestamp, timestamp),
            )
            for source in node.get("sources", []):
                conn.execute(
                    """insert into kg_node_sources
                    (extraction_job_id, node_id, source_id, video_id, transcript_id, segment_ids, start_seconds, end_seconds, evidence_text, created_at)
                    values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    on conflict(extraction_job_id, node_id, source_id) do update set
                        video_id = excluded.video_id, transcript_id = excluded.transcript_id, segment_ids = excluded.segment_ids,
                        start_seconds = excluded.start_seconds, end_seconds = excluded.end_seconds, evidence_text = excluded.evidence_text""",
                    (
                        job_id, node["node_id"], source["source_id"], video_id, transcript_id, json.dumps(source["segment_ids"]),
                        source["start_seconds"], source["end_seconds"], source.get("evidence_text", ""), timestamp,
                    ),
                )
        for edge in edges:
            ensure_relation_type(conn, edge["relation_type"])
            conn.execute(
                """insert into kg_edges
                (extraction_job_id, edge_id, source_node_id, target_node_id, relation_type, confidence, evidence_source_ids, created_at, updated_at)
                values (?, ?, ?, ?, ?, ?, ?, ?, ?)
                on conflict(extraction_job_id, source_node_id, target_node_id, relation_type) do update set
                    confidence = excluded.confidence, evidence_source_ids = excluded.evidence_source_ids, updated_at = excluded.updated_at""",
                (
                    job_id, edge["edge_id"], edge["source_node_id"], edge["target_node_id"], edge["relation_type"],
                    edge["confidence"], json.dumps(edge.get("evidence_source_ids", [])), timestamp, timestamp,
                ),
            )


def clone_graph_snapshot(source_job_id, target_job_id):
    if source_job_id == target_job_id:
        return
    with connect_db() as conn:
        conn.execute("delete from kg_nodes where extraction_job_id = ?", (target_job_id,))
        conn.execute(
            """insert into kg_nodes
            (extraction_job_id, node_id, canonical_name, node_type, short_summary, detailed_explanation, detail_status,
             confidence, knowledge_status, aliases, created_at, updated_at)
            select ?, node_id, canonical_name, node_type, short_summary, detailed_explanation, detail_status,
                   confidence, knowledge_status, aliases, created_at, updated_at
            from kg_nodes where extraction_job_id = ?""",
            (target_job_id, source_job_id),
        )
        conn.execute(
            """insert into kg_node_sources
            (extraction_job_id, node_id, source_id, video_id, transcript_id, segment_ids, start_seconds, end_seconds, evidence_text, created_at)
            select ?, node_id, source_id, video_id, transcript_id, segment_ids, start_seconds, end_seconds, evidence_text, created_at
            from kg_node_sources where extraction_job_id = ?""",
            (target_job_id, source_job_id),
        )
        conn.execute(
            """insert into kg_edges
            (extraction_job_id, edge_id, source_node_id, target_node_id, relation_type, relation_status, confidence,
             evidence_source_ids, directional, created_at, updated_at)
            select ?, edge_id, source_node_id, target_node_id, relation_type, relation_status, confidence,
                   evidence_source_ids, directional, created_at, updated_at
            from kg_edges where extraction_job_id = ?""",
            (target_job_id, source_job_id),
        )
        conn.execute(
            """insert into kg_node_embeddings (extraction_job_id, node_id, model, dims, vector, created_at)
            select ?, node_id, model, dims, vector, created_at from kg_node_embeddings where extraction_job_id = ?""",
            (target_job_id, source_job_id),
        )
        conn.execute(
            """insert into kg_node_detail_cache
            (extraction_job_id, node_id, cache_key, lens_hash, lens_json, provider, model, prompt_version,
             detail_markdown, evidence_json, confidence, status, created_at, updated_at)
            select ?, node_id, cache_key, lens_hash, lens_json, provider, model, prompt_version,
                   detail_markdown, evidence_json, confidence, status, created_at, updated_at
            from kg_node_detail_cache where extraction_job_id = ?""",
            (target_job_id, source_job_id),
        )
        conn.execute(
            """insert into kg_user_knowledge
            (extraction_job_id, node_id, status, confidence, last_reviewed_at, notes, updated_at)
            select ?, node_id, status, confidence, last_reviewed_at, notes, updated_at
            from kg_user_knowledge where extraction_job_id = ?""",
            (target_job_id, source_job_id),
        )
