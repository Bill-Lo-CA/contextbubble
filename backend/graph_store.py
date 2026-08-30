import hashlib
import json

from config import GEMINI_MODEL, GRAPH_EXTRACTION_MODE, GRAPH_VERSION, OLLAMA_MODEL, now_iso
from db import connect_db
from graph_extraction_agents import RELATION_TYPES


def graph_extraction_pipeline_signature(extraction_mode=None, model=None):
    mode = extraction_mode or GRAPH_EXTRACTION_MODE
    resolved_model = model
    if resolved_model is None:
        resolved_model = {"gemini": GEMINI_MODEL, "ollama": OLLAMA_MODEL}.get(mode, "")
    encoded = json.dumps([GRAPH_VERSION, mode, resolved_model], ensure_ascii=False, separators=(",", ":"))
    digest = hashlib.sha256(encoded.encode()).hexdigest()
    return digest[:16]


def graph_extraction_cache_key(video_id, content_hash, extraction_mode=None, model=None):
    return f"{video_id}:{content_hash}:{graph_extraction_pipeline_signature(extraction_mode, model)}"


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


EXTRACTION_JOB_UPDATE_COLUMNS = {"cache_key", "status", "stage", "node_count", "edge_count", "error_code", "message"}


def update_extraction_job(job_id, **values):
    if not values:
        return
    unknown = set(values) - EXTRACTION_JOB_UPDATE_COLUMNS
    if unknown:
        raise ValueError(f"invalid kg extraction job update fields: {', '.join(sorted(unknown))}")
    values["updated_at"] = now_iso()
    assignments = ", ".join(f"{key} = ?" for key in values)
    with connect_db() as conn:
        conn.execute("update kg_extraction_jobs set " + assignments + " where job_id = ?", (*values.values(), job_id))


def extraction_job_payload(job_id):
    with connect_db() as conn:
        row = conn.execute(
            """select preparation_jobs.job_id, preparation_jobs.video_id,
                      preparation_jobs.status as parent_status, preparation_jobs.stage as parent_stage,
                      preparation_jobs.error_code as parent_error_code, preparation_jobs.message as parent_message,
                      kg_extraction_jobs.status as extraction_status, kg_extraction_jobs.stage as extraction_stage,
                      kg_extraction_jobs.node_count, kg_extraction_jobs.edge_count,
                      kg_extraction_jobs.error_code as extraction_error_code, kg_extraction_jobs.message as extraction_message
               from preparation_jobs left join kg_extraction_jobs on kg_extraction_jobs.job_id = preparation_jobs.job_id
               where preparation_jobs.job_id = ? and preparation_jobs.job_kind = 'graph_extraction'""",
            (job_id,),
        ).fetchone()
    if not row:
        return None
    if row["parent_status"] == "failed":
        status = "failed"
        stage = row["parent_stage"]
        if stage is None:
            stage = "failed"
        error_code = row["parent_error_code"]
        message = row["parent_message"]
    else:
        status = row["extraction_status"]
        if status is None:
            status = row["parent_status"]
        stage = row["extraction_stage"]
        if stage is None:
            stage = row["parent_stage"]
        if stage is None:
            stage = "queued"
        error_code = row["extraction_error_code"]
        if error_code is None:
            error_code = row["parent_error_code"]
        message = row["extraction_message"]
        if message is None:
            message = row["parent_message"]
    return {
        "job_id": row["job_id"],
        "video_id": row["video_id"],
        "status": status,
        "stage": stage,
        "node_count": row["node_count"] or 0,
        "edge_count": row["edge_count"] or 0,
        "error_code": error_code,
        "message": message,
    }


def save_nodes_and_edges(job_id, video_id, transcript_id, nodes, edges):
    timestamp = now_iso()
    with connect_db() as conn:
        conn.execute("delete from kg_nodes where extraction_job_id = ?", (job_id,))
        conn.executemany(
            """insert into kg_nodes (extraction_job_id, node_id, canonical_name, node_type, short_summary, confidence, created_at, updated_at)
            values (?, ?, ?, ?, ?, ?, ?, ?)
            on conflict(extraction_job_id, node_id) do update set
                short_summary = excluded.short_summary, confidence = excluded.confidence, updated_at = excluded.updated_at""",
            [
                (job_id, node["node_id"], node["canonical_name"], node["node_type"], node["short_summary"], node["confidence"], timestamp, timestamp)
                for node in nodes
            ],
        )
        conn.executemany(
            """insert into kg_node_sources
            (extraction_job_id, node_id, source_id, video_id, transcript_id, segment_ids, start_seconds, end_seconds, evidence_text, created_at)
            values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            on conflict(extraction_job_id, node_id, source_id) do update set
                video_id = excluded.video_id, transcript_id = excluded.transcript_id, segment_ids = excluded.segment_ids,
                start_seconds = excluded.start_seconds, end_seconds = excluded.end_seconds, evidence_text = excluded.evidence_text""",
            [
                (
                    job_id, node["node_id"], source["source_id"], video_id, transcript_id, json.dumps(source["segment_ids"]),
                    source["start_seconds"], source["end_seconds"], source.get("evidence_text", ""), timestamp,
                )
                for node in nodes for source in node.get("sources", [])
            ],
        )
        # Reconcile every used type, not only tentative proposals. This also
        # repairs legacy rows where a built-in type accidentally entered review.
        used_relation_types = {edge["relation_type"] for edge in edges}
        existing_status = {}
        if used_relation_types:
            placeholders = ",".join("?" for _ in used_relation_types)
            existing_status = {
                row["relation_type"]: row["status"]
                for row in conn.execute(
                    f"select relation_type, status from kg_relation_types where relation_type in ({placeholders})",
                    list(used_relation_types),
                )
            }
        built_in_types = used_relation_types & set(RELATION_TYPES)
        existing_built_in_types = built_in_types & existing_status.keys()
        conn.executemany(
            "update kg_relation_types set status = 'approved', proposed_by_job_id = null where relation_type = ? and status != 'approved'",
            [(relation_type,) for relation_type in existing_built_in_types],
        )
        existing_status.update({relation_type: "approved" for relation_type in existing_built_in_types})

        final_edges = []
        for edge in edges:
            relation_status = edge.get("relation_status", "accepted")
            current = existing_status.get(edge["relation_type"])
            if current == "rejected":
                continue
            if current == "approved":
                relation_status = "accepted"
            elif current == "proposed":
                relation_status = "proposed"
            final_edges.append((edge, relation_status))

        # insert-or-ignore only relation_types with no existing row - insert-or-ignore
        # otherwise leaves a prior reviewer decision (status/proposed_by_job_id)
        # untouched, which is intentional: this batch never overrides a past review.
        new_relation_types = {}
        for edge, relation_status in final_edges:
            relation_type = edge["relation_type"]
            if relation_type in existing_status or relation_type in new_relation_types:
                continue
            status = "proposed" if relation_status == "proposed" else "approved"
            description = edge.get("proposed_relation_description") or relation_type
            new_relation_types[relation_type] = (status, description, job_id if status == "proposed" else None)
        conn.executemany(
            "insert or ignore into kg_relation_types (relation_type, description, status, proposed_by_job_id, created_at) values (?, ?, ?, ?, ?)",
            [
                (relation_type, description, status, proposed_by_job_id, timestamp)
                for relation_type, (status, description, proposed_by_job_id) in new_relation_types.items()
            ],
        )
        conn.executemany(
            """insert into kg_edges
            (extraction_job_id, edge_id, source_node_id, target_node_id, relation_type, relation_status,
             confidence, evidence_source_ids, directional, created_at, updated_at)
            values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            on conflict(extraction_job_id, source_node_id, target_node_id, relation_type) do update set
                relation_status = excluded.relation_status, confidence = excluded.confidence,
                evidence_source_ids = excluded.evidence_source_ids, directional = excluded.directional,
                updated_at = excluded.updated_at""",
            [
                (
                    job_id, edge["edge_id"], edge["source_node_id"], edge["target_node_id"], edge["relation_type"], relation_status,
                    edge["confidence"], json.dumps(edge.get("evidence_source_ids", [])), edge.get("directional", 1), timestamp, timestamp,
                )
                for edge, relation_status in final_edges
            ],
        )
        return conn.execute("select count(*) from kg_edges where extraction_job_id = ?", (job_id,)).fetchone()[0]


def clone_graph_snapshot(source_job_id, target_job_id, video_id, transcript_id, cache_key):
    with connect_db() as conn:
        conn.execute("begin immediate")
        source = conn.execute(
            "select node_count, edge_count from kg_extraction_jobs where job_id = ?",
            (source_job_id,),
        ).fetchone()
        if not source:
            raise ValueError("source extraction job not found")
        timestamp = now_iso()
        conn.execute(
            """insert into kg_extraction_jobs
            (job_id, video_id, transcript_id, cache_key, status, stage, node_count, edge_count, error_code, message, created_at, updated_at)
            values (?, ?, ?, ?, 'ready', 'ready', ?, ?, null, null, ?, ?)
            on conflict(job_id) do update set
                video_id = excluded.video_id, transcript_id = excluded.transcript_id,
                cache_key = excluded.cache_key, status = 'ready', stage = 'ready',
                node_count = excluded.node_count, edge_count = excluded.edge_count,
                error_code = null, message = null, updated_at = excluded.updated_at""",
            (target_job_id, video_id, transcript_id, cache_key,
             source["node_count"] or 0, source["edge_count"] or 0, timestamp, timestamp),
        )
        if source_job_id == target_job_id:
            return
        conn.execute("delete from kg_nodes where extraction_job_id = ?", (target_job_id,))
        conn.execute(
            """insert into kg_nodes
            (extraction_job_id, node_id, canonical_name, node_type, short_summary, detailed_explanation, detail_status,
             confidence, aliases, created_at, updated_at)
            select ?, node_id, canonical_name, node_type, short_summary, detailed_explanation, detail_status,
                   confidence, aliases, created_at, updated_at
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


def list_relation_types(status):
    with connect_db() as conn:
        rows = conn.execute(
            """with latest_ready_jobs as (
                   select jobs.job_id
                   from kg_extraction_jobs jobs
                   where jobs.status = 'ready'
                     and not exists (
                         select 1 from kg_extraction_jobs newer
                         where newer.video_id = jobs.video_id and newer.status = 'ready'
                           and (newer.updated_at > jobs.updated_at
                                or (newer.updated_at = jobs.updated_at and newer.job_id > jobs.job_id))
                     )
               )
               select rt.relation_type, rt.description, rt.status, rt.proposed_by_job_id,
                      count(e.edge_id) as snapshot_edge_count,
                      count(distinct e.extraction_job_id) as snapshot_job_count,
                      count(case when latest.job_id is not null then e.edge_id end) as current_edge_count,
                      count(distinct case when latest.job_id is not null then e.extraction_job_id end) as current_job_count
               from kg_relation_types rt
               left join kg_edges e on e.relation_type = rt.relation_type
               left join latest_ready_jobs latest on latest.job_id = e.extraction_job_id
               where rt.status = ?
               group by rt.relation_type
               order by rt.created_at""",
            (status,),
        ).fetchall()
    return [dict(row) for row in rows]


def review_relation_type(relation_type, decision, description=None):
    # Returns None (relation_type not found -> caller maps to 404), the string
    # "conflict" (reversing an already-finalized opposite decision -> 409), or
    # a result dict (success, decision applied or already in that state -> 200).
    target_status = "approved" if decision == "approve" else "rejected"
    if description is not None:
        description = description.strip()
        if not description:
            raise ValueError("description must not be blank")
    with connect_db() as conn:
        conn.execute("begin immediate")
        row = conn.execute("select status from kg_relation_types where relation_type = ?", (relation_type,)).fetchone()
        if not row:
            return None
        current_status = row["status"]
        if decision == "reject" and relation_type in RELATION_TYPES:
            return "conflict"
        if current_status not in ("proposed", target_status):
            return "conflict"
        if description is not None:
            conn.execute(
                "update kg_relation_types set status = ?, description = ? where relation_type = ?",
                (target_status, description, relation_type),
            )
        else:
            conn.execute("update kg_relation_types set status = ? where relation_type = ?", (target_status, relation_type))
        if current_status == target_status:
            # Idempotent repeat of a decision already applied - no edges to touch.
            return {
                "scope": "global", "relation_type": relation_type, "status": target_status,
                "affected_edge_count": 0, "affected_job_count": 0,
            }
        affected_job_count = conn.execute(
            "select count(distinct extraction_job_id) from kg_edges where relation_type = ? and relation_status = 'proposed'",
            (relation_type,),
        ).fetchone()[0]
        edge_relation_status = "accepted" if decision == "approve" else "rejected"
        cursor = conn.execute(
            "update kg_edges set relation_status = ?, updated_at = ? where relation_type = ? and relation_status = 'proposed'",
            (edge_relation_status, now_iso(), relation_type),
        )
        affected = cursor.rowcount
    return {
        "scope": "global", "relation_type": relation_type, "status": target_status,
        "affected_edge_count": affected, "affected_job_count": affected_job_count,
    }
