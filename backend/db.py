import sqlite3

from config import *


def connect_db():
    DATA_DIR.mkdir(exist_ok=True)
    conn = sqlite3.connect(DB_FILE, timeout=5)
    conn.row_factory = sqlite3.Row
    conn.execute("pragma journal_mode=WAL")
    conn.execute("pragma busy_timeout = 5000")
    conn.execute("pragma foreign_keys = ON")
    return conn
def ensure_column(conn, table, column, definition):
    columns = {row["name"] for row in conn.execute(f"pragma table_info({table})")}
    if column not in columns:
        conn.execute(f"alter table {table} add column {column} {definition}")
def init_db():
    with connect_db() as conn:
        conn.executescript("""
            create table if not exists videos (
                video_id text primary key,
                created_at text not null,
                updated_at text not null
            );
            create table if not exists preparation_jobs (
                job_id text primary key,
                video_id text not null,
                learner_level text not null,
                source_policy text not null default 'live',
                status text not null,
                stage text not null,
                transcript_source text,
                transcript_id text,
                analysis_id text,
                duration_seconds real,
                chunks_total integer default 0,
                chunks_completed integer default 0,
                progress real default 0,
                error_code text,
                message text,
                force_refresh integer default 0,
                created_at text not null,
                updated_at text not null
            );
            create table if not exists transcript_sources (
                transcript_id text primary key,
                video_id text not null,
                filename text not null,
                source text not null,
                content_hash text not null,
                segment_count integer not null,
                created_at text not null
            );
            create table if not exists transcript_segments (
                transcript_id text not null,
                segment_id text not null,
                start_seconds real not null,
                end_seconds real not null,
                text text not null,
                primary key (transcript_id, segment_id)
            );
            create table if not exists asr_chunks (
                job_id text not null,
                chunk_index integer not null,
                start_seconds real not null,
                end_seconds real not null,
                status text not null,
                attempt_count integer default 0,
                segment_count integer default 0,
                error_code text,
                updated_at text not null,
                primary key (job_id, chunk_index)
            );
            create table if not exists asr_chunk_segments (
                job_id text not null,
                chunk_index integer not null,
                segment_index integer not null,
                start_seconds real not null,
                end_seconds real not null,
                text text not null,
                primary key (job_id, chunk_index, segment_index)
            );
            create table if not exists preparation_events (
                event_id integer primary key autoincrement,
                job_id text not null,
                event_type text not null,
                stage text,
                metadata text,
                created_at text not null
            );
            create table if not exists analyses (
                analysis_id text primary key,
                video_id text not null,
                learner_level text not null,
                transcript_id text not null,
                cache_key text not null unique,
                status text not null,
                stage text,
                error_code text,
                message text,
                created_at text not null,
                updated_at text not null
            );
            create table if not exists bubbles (
                analysis_id text not null,
                bubble_id text not null,
                concept text not null,
                anchor_segment_id text not null,
                source_segment_ids text not null,
                start_seconds real not null,
                short_explanation text not null,
                expanded_explanation text not null,
                confidence real not null,
                review_status text not null,
                review_reason text,
                primary key (analysis_id, bubble_id)
            );
            create table if not exists schema_migrations (
                name text primary key,
                applied_at text not null
            );
        """)
        ensure_column(conn, "preparation_jobs", "source_policy", "text not null default 'live'")
        conn.executescript("""
            create index if not exists idx_preparation_jobs_lookup
                on preparation_jobs(video_id, learner_level, source_policy, status, created_at);
            create index if not exists idx_analyses_lookup
                on analyses(video_id, learner_level, status, updated_at);
            create index if not exists idx_transcript_sources_lookup
                on transcript_sources(video_id, source, created_at);
            create index if not exists idx_asr_chunks_status
                on asr_chunks(job_id, status);
            create index if not exists idx_preparation_events_job
                on preparation_events(job_id, created_at);
        """)
        conn.execute(
            "insert or ignore into schema_migrations values (?, ?)",
            ("2026-07-project-review-t002-t005", now_iso()),
        )
