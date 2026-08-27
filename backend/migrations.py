GRAPH_TABLES_SQL = """
    create table if not exists kg_extraction_jobs (
        job_id text primary key references preparation_jobs(job_id) on delete cascade,
        video_id text not null references videos(video_id),
        transcript_id text not null references transcript_sources(transcript_id),
        cache_key text not null,
        status text not null check (status in ('queued','processing','ready','failed')),
        stage text not null,
        node_count integer default 0 check (node_count >= 0),
        edge_count integer default 0 check (edge_count >= 0),
        error_code text, message text,
        created_at text not null, updated_at text not null
    );
    create index if not exists idx_kg_extraction_jobs_lookup on kg_extraction_jobs(video_id, status, updated_at);
    create index if not exists idx_kg_extraction_jobs_cache_key on kg_extraction_jobs(cache_key, status, updated_at);

    create table if not exists kg_extraction_events (
        event_id integer primary key autoincrement,
        job_id text not null references kg_extraction_jobs(job_id) on delete cascade,
        event_type text not null, stage text, metadata text, created_at text not null
    );
    create index if not exists idx_kg_extraction_events_job on kg_extraction_events(job_id, created_at);

    create table if not exists kg_nodes (
        extraction_job_id text not null references kg_extraction_jobs(job_id) on delete cascade,
        node_id text not null,
        canonical_name text not null,
        node_type text not null check (node_type in
            ('concept','tool','technique','vulnerability','entity','event','mitigation','detection','actor','other')),
        short_summary text not null,
        detailed_explanation text,
        detail_status text not null default 'pending' check (detail_status in ('pending','generating','ready','failed')),
        confidence real not null check (confidence between 0 and 1),
        knowledge_status text not null default 'unknown' check (knowledge_status in ('unknown','introduced','understood','mastered')),
        aliases text not null default '[]',
        created_at text not null, updated_at text not null,
        primary key (extraction_job_id, node_id)
    );
    create index if not exists idx_kg_nodes_name on kg_nodes(canonical_name);
    create index if not exists idx_kg_nodes_type on kg_nodes(node_type);

    create table if not exists kg_node_sources (
        extraction_job_id text not null references kg_extraction_jobs(job_id) on delete cascade,
        node_id text not null,
        source_id text not null,
        video_id text references videos(video_id),
        transcript_id text references transcript_sources(transcript_id),
        segment_ids text not null default '[]',
        start_seconds real, end_seconds real,
        evidence_text text,
        created_at text not null,
        primary key (extraction_job_id, node_id, source_id),
        foreign key (extraction_job_id, node_id) references kg_nodes(extraction_job_id, node_id) on delete cascade,
        check (start_seconds is null or end_seconds is null or end_seconds >= start_seconds)
    );
    create index if not exists idx_kg_node_sources_video on kg_node_sources(video_id, start_seconds);

    create table if not exists kg_relation_types (
        relation_type text primary key,
        description text not null,
        status text not null default 'approved' check (status in ('proposed','approved','rejected')),
        proposed_by_job_id text,
        created_at text not null
    );

    create table if not exists kg_edges (
        extraction_job_id text not null references kg_extraction_jobs(job_id) on delete cascade,
        edge_id text not null,
        source_node_id text not null,
        target_node_id text not null,
        relation_type text not null references kg_relation_types(relation_type),
        relation_status text not null default 'accepted' check (relation_status in ('proposed','accepted','rejected')),
        confidence real not null check (confidence between 0 and 1),
        evidence_source_ids text not null default '[]',
        directional integer not null default 1 check (directional in (0,1)),
        created_at text not null, updated_at text not null,
        primary key (extraction_job_id, edge_id),
        foreign key (extraction_job_id, source_node_id) references kg_nodes(extraction_job_id, node_id) on delete cascade,
        foreign key (extraction_job_id, target_node_id) references kg_nodes(extraction_job_id, node_id) on delete cascade
    );
    create unique index if not exists idx_kg_edges_unique on kg_edges(extraction_job_id, source_node_id, target_node_id, relation_type);
    create index if not exists idx_kg_edges_source on kg_edges(extraction_job_id, source_node_id, relation_status);
    create index if not exists idx_kg_edges_target on kg_edges(extraction_job_id, target_node_id, relation_status);

    create table if not exists kg_node_embeddings (
        extraction_job_id text not null,
        node_id text not null,
        model text not null, dims integer not null,
        vector text not null,
        created_at text not null,
        primary key (extraction_job_id, node_id),
        foreign key (extraction_job_id, node_id) references kg_nodes(extraction_job_id, node_id) on delete cascade
    );

    create table if not exists kg_node_detail_cache (
        extraction_job_id text not null,
        node_id text not null,
        cache_key text not null,
        lens_hash text not null, lens_json text not null,
        provider text not null, model text not null, prompt_version text not null,
        detail_markdown text, evidence_json text not null default '[]',
        confidence real, status text not null check (status in ('ready','failed')),
        created_at text not null, updated_at text not null,
        primary key (extraction_job_id, cache_key),
        foreign key (extraction_job_id, node_id) references kg_nodes(extraction_job_id, node_id) on delete cascade
    );
    create index if not exists idx_kg_node_detail_cache_node on kg_node_detail_cache(extraction_job_id, node_id, lens_hash);

    create table if not exists kg_user_knowledge (
        extraction_job_id text not null,
        node_id text not null,
        status text not null default 'unknown' check (status in ('unknown','introduced','understood','mastered')),
        confidence real check (confidence is null or confidence between 0 and 1),
        last_reviewed_at text, notes text,
        updated_at text not null,
        primary key (extraction_job_id, node_id),
        foreign key (extraction_job_id, node_id) references kg_nodes(extraction_job_id, node_id) on delete cascade
    );
"""


def migrate_graph_snapshot_schema(conn):
    node_columns = {row[1] for row in conn.execute("pragma table_info(kg_nodes)")}
    if "extraction_job_id" in node_columns:
        return

    for table in (
        "kg_extraction_events", "kg_node_sources", "kg_edges", "kg_node_embeddings",
        "kg_node_detail_cache", "kg_user_knowledge",
    ):
        conn.execute(f"alter table {table} rename to {table}_legacy")
    conn.execute("alter table kg_nodes rename to kg_nodes_legacy")
    conn.execute("alter table kg_extraction_jobs rename to kg_extraction_jobs_legacy")

    for index in (
        "idx_kg_extraction_jobs_lookup", "idx_kg_extraction_jobs_cache_key", "idx_kg_extraction_events_job",
        "idx_kg_nodes_name", "idx_kg_nodes_type", "idx_kg_node_sources_video", "idx_kg_edges_unique",
        "idx_kg_edges_source", "idx_kg_edges_target", "idx_kg_node_detail_cache_node",
    ):
        conn.execute(f"drop index if exists {index}")

    conn.executescript(GRAPH_TABLES_SQL)
    conn.execute(
        """insert into kg_extraction_jobs
        (job_id, video_id, transcript_id, cache_key, status, stage, node_count, edge_count, error_code, message, created_at, updated_at)
        select job_id, video_id, transcript_id, cache_key, status, stage, node_count, edge_count, error_code, message, created_at, updated_at
        from kg_extraction_jobs_legacy"""
    )
    conn.execute(
        """insert into kg_extraction_events (event_id, job_id, event_type, stage, metadata, created_at)
        select event_id, job_id, event_type, stage, metadata, created_at from kg_extraction_events_legacy"""
    )
    conn.execute(
        """insert into kg_nodes
        (extraction_job_id, node_id, canonical_name, node_type, short_summary, detailed_explanation, detail_status,
         confidence, knowledge_status, aliases, created_at, updated_at)
        with node_jobs as (
            select node_id, extraction_job_id from kg_node_sources_legacy where extraction_job_id is not null
            union
            select source_node_id, extraction_job_id from kg_edges_legacy where extraction_job_id is not null
            union
            select target_node_id, extraction_job_id from kg_edges_legacy where extraction_job_id is not null
        )
        select node_jobs.extraction_job_id, nodes.node_id, nodes.canonical_name, nodes.node_type, nodes.short_summary,
               nodes.detailed_explanation, nodes.detail_status, nodes.confidence, nodes.knowledge_status, nodes.aliases,
               nodes.created_at, nodes.updated_at
        from kg_nodes_legacy nodes join node_jobs on node_jobs.node_id = nodes.node_id"""
    )
    conn.execute(
        """insert into kg_node_sources
        (extraction_job_id, node_id, source_id, video_id, transcript_id, segment_ids, start_seconds, end_seconds, evidence_text, created_at)
        select extraction_job_id, node_id, source_id, video_id, transcript_id, segment_ids, start_seconds, end_seconds, evidence_text, created_at
        from kg_node_sources_legacy where extraction_job_id is not null"""
    )
    conn.execute(
        """insert into kg_edges
        (extraction_job_id, edge_id, source_node_id, target_node_id, relation_type, relation_status, confidence,
         evidence_source_ids, directional, created_at, updated_at)
        select extraction_job_id, edge_id, source_node_id, target_node_id, relation_type, relation_status, confidence,
               evidence_source_ids, directional, created_at, updated_at
        from kg_edges_legacy where extraction_job_id is not null"""
    )
    conn.execute(
        """insert into kg_node_embeddings (extraction_job_id, node_id, model, dims, vector, created_at)
        with node_jobs as (
            select node_id, extraction_job_id from kg_node_sources_legacy where extraction_job_id is not null
            union
            select source_node_id, extraction_job_id from kg_edges_legacy where extraction_job_id is not null
            union
            select target_node_id, extraction_job_id from kg_edges_legacy where extraction_job_id is not null
        )
        select node_jobs.extraction_job_id, embeddings.node_id, embeddings.model, embeddings.dims, embeddings.vector, embeddings.created_at
        from kg_node_embeddings_legacy embeddings join node_jobs on node_jobs.node_id = embeddings.node_id"""
    )
    conn.execute(
        """insert into kg_node_detail_cache
        (extraction_job_id, node_id, cache_key, lens_hash, lens_json, provider, model, prompt_version, detail_markdown,
         evidence_json, confidence, status, created_at, updated_at)
        with node_jobs as (
            select node_id, extraction_job_id from kg_node_sources_legacy where extraction_job_id is not null
            union
            select source_node_id, extraction_job_id from kg_edges_legacy where extraction_job_id is not null
            union
            select target_node_id, extraction_job_id from kg_edges_legacy where extraction_job_id is not null
        )
        select node_jobs.extraction_job_id, cache.node_id, cache.cache_key, cache.lens_hash, cache.lens_json, cache.provider,
               cache.model, cache.prompt_version, cache.detail_markdown, cache.evidence_json, cache.confidence, cache.status,
               cache.created_at, cache.updated_at
        from kg_node_detail_cache_legacy cache join node_jobs on node_jobs.node_id = cache.node_id"""
    )
    conn.execute(
        """insert into kg_user_knowledge (extraction_job_id, node_id, status, confidence, last_reviewed_at, notes, updated_at)
        with node_jobs as (
            select node_id, extraction_job_id from kg_node_sources_legacy where extraction_job_id is not null
            union
            select source_node_id, extraction_job_id from kg_edges_legacy where extraction_job_id is not null
            union
            select target_node_id, extraction_job_id from kg_edges_legacy where extraction_job_id is not null
        )
        select node_jobs.extraction_job_id, knowledge.node_id, knowledge.status, knowledge.confidence, knowledge.last_reviewed_at,
               knowledge.notes, knowledge.updated_at
        from kg_user_knowledge_legacy knowledge join node_jobs on node_jobs.node_id = knowledge.node_id"""
    )

    for table in (
        "kg_extraction_events_legacy", "kg_node_sources_legacy", "kg_edges_legacy", "kg_node_embeddings_legacy",
        "kg_node_detail_cache_legacy", "kg_user_knowledge_legacy", "kg_nodes_legacy", "kg_extraction_jobs_legacy",
    ):
        conn.execute(f"drop table {table}")


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
    (3, "knowledge_graph_extraction", f"""
        alter table preparation_jobs add column job_kind text not null default 'bubble_analysis'
            check (job_kind in ('bubble_analysis','graph_extraction'));
        {GRAPH_TABLES_SQL}
    """),
    (4, "knowledge_graph_job_snapshots", migrate_graph_snapshot_schema),
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
        if callable(sql):
            sql(conn)
        else:
            conn.executescript(sql)
        conn.execute("insert into schema_migrations (version, name, applied_at) values (?, ?, ?)", (version, name, applied_at))
