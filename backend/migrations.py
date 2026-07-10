MIGRATIONS = (
    (1, "initial_schema", """
        create table if not exists videos (
            video_id text primary key, created_at text not null, updated_at text not null
        );
        create table if not exists preparation_jobs (
            job_id text primary key, video_id text not null references videos(video_id),
            learner_level text not null check (learner_level in ('beginner','intermediate','advanced')),
            source_policy text not null default 'live', status text not null,
            stage text not null, transcript_source text, transcript_id text, analysis_id text,
            duration_seconds real check (duration_seconds is null or duration_seconds >= 0),
            chunks_total integer default 0 check (chunks_total >= 0),
            chunks_completed integer default 0 check (chunks_completed >= 0),
            progress real default 0 check (progress between 0 and 1), error_code text,
            message text, force_refresh integer default 0 check (force_refresh in (0,1)),
            created_at text not null, updated_at text not null
        );
        create table if not exists transcript_sources (
            transcript_id text primary key, video_id text not null references videos(video_id),
            filename text not null, source text not null, content_hash text not null,
            segment_count integer not null check (segment_count >= 0), metadata text default '{}',
            created_at text not null
        );
        create table if not exists transcript_segments (
            transcript_id text not null references transcript_sources(transcript_id) on delete cascade,
            segment_id text not null, start_seconds real not null check (start_seconds >= 0),
            end_seconds real not null check (end_seconds >= start_seconds), text text not null,
            primary key (transcript_id, segment_id)
        );
        create table if not exists asr_chunks (
            job_id text not null references preparation_jobs(job_id) on delete cascade,
            chunk_index integer not null check (chunk_index >= 0), start_seconds real not null,
            end_seconds real not null check (end_seconds >= start_seconds), status text not null,
            attempt_count integer default 0 check (attempt_count >= 0),
            segment_count integer default 0 check (segment_count >= 0), error_code text,
            updated_at text not null, primary key (job_id, chunk_index)
        );
        create table if not exists asr_chunk_segments (
            job_id text not null, chunk_index integer not null, segment_index integer not null,
            start_seconds real not null, end_seconds real not null check (end_seconds >= start_seconds),
            text text not null, primary key (job_id, chunk_index, segment_index),
            foreign key (job_id, chunk_index) references asr_chunks(job_id, chunk_index) on delete cascade
        );
        create table if not exists preparation_events (
            event_id integer primary key autoincrement,
            job_id text not null references preparation_jobs(job_id) on delete cascade,
            event_type text not null, stage text, metadata text, created_at text not null
        );
        create table if not exists analyses (
            analysis_id text primary key, video_id text not null references videos(video_id),
            learner_level text not null check (learner_level in ('beginner','intermediate','advanced')),
            transcript_id text not null references transcript_sources(transcript_id),
            cache_key text not null unique, status text not null, stage text, error_code text,
            message text, created_at text not null, updated_at text not null
        );
        create table if not exists bubbles (
            analysis_id text not null references analyses(analysis_id) on delete cascade,
            bubble_id text not null, concept text not null, anchor_segment_id text not null,
            source_segment_ids text not null, start_seconds real not null check (start_seconds >= 0),
            short_explanation text not null, expanded_explanation text not null,
            confidence real not null check (confidence between 0 and 1), review_status text not null,
            review_reason text, primary key (analysis_id, bubble_id)
        );
        create table if not exists translation_cache (
            cache_key text primary key, segment_id text not null, source_hash text not null,
            context_hash text not null, target_language text not null, provider text not null,
            model text not null, prompt_version text not null, translated_text text,
            confidence real check (confidence is null or confidence between 0 and 1),
            status text not null, decision text not null, reason text,
            created_at text not null, updated_at text not null
        );
        create table if not exists session_tokens (
            token_hash text primary key, expires_at real not null check (expires_at >= 0), created_at text not null
        );
        create index if not exists idx_preparation_jobs_lookup on preparation_jobs(video_id, learner_level, source_policy, status, created_at);
        create index if not exists idx_analyses_lookup on analyses(video_id, learner_level, status, updated_at);
        create index if not exists idx_transcript_sources_lookup on transcript_sources(video_id, source, created_at);
        create index if not exists idx_asr_chunks_status on asr_chunks(job_id, status);
        create index if not exists idx_preparation_events_job on preparation_events(job_id, created_at);
        create index if not exists idx_translation_cache_lookup on translation_cache(segment_id, target_language, provider, model, prompt_version);
        create index if not exists idx_session_tokens_expiry on session_tokens(expires_at);
    """),
    (2, "persisted_translation_jobs", """
        create table if not exists translation_jobs (
            job_id text primary key, job_key text not null, segment_id text not null,
            payload_json text not null, status text not null check (status in ('queued','processing','translated','failed','skipped')),
            result_json text, error_code text, error_message text,
            attempts integer not null default 0 check (attempts >= 0),
            created_at real not null, updated_at real not null
        );
        create index if not exists idx_translation_jobs_status on translation_jobs(status, created_at);
        create index if not exists idx_translation_jobs_key on translation_jobs(job_key, status, created_at);
    """),
)


def apply_migrations(conn, applied_at):
    columns = {row[1] for row in conn.execute("pragma table_info(schema_migrations)")}
    if columns and "version" not in columns:
        conn.execute("alter table schema_migrations rename to schema_migrations_legacy")
    conn.execute("create table if not exists schema_migrations (version integer primary key, name text not null unique, applied_at text not null)")
    applied = {row[0] for row in conn.execute("select version from schema_migrations")}
    for version, name, sql in MIGRATIONS:
        if version in applied:
            continue
        conn.executescript(sql)
        conn.execute("insert into schema_migrations (version, name, applied_at) values (?, ?, ?)", (version, name, applied_at))
