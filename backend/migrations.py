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
    -- directional=1 edges are order-sensitive ("A prerequisite_for B" != "B prerequisite_for A") and are
    -- covered by idx_kg_edges_unique above; directional=0 edges represent a symmetric relationship, so
    -- (A,B,type) and (B,A,type) must not both be insertable - normalize the pair order in this index.
    create unique index if not exists idx_kg_edges_undirected_unique
        on kg_edges(extraction_job_id, relation_type, min(source_node_id, target_node_id), max(source_node_id, target_node_id))
        where directional = 0;
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


def migrate_add_job_kind_and_graph_tables(conn):
    # Guard the ALTER so an interrupted-then-retried migration (process killed after
    # this statement but before the migration is recorded in schema_migrations) sees
    # the column already present instead of failing with "duplicate column name".
    # GRAPH_TABLES_SQL is entirely `if not exists` DDL, so it's already retry-safe.
    columns = {row[1] for row in conn.execute("pragma table_info(preparation_jobs)")}
    if "job_kind" not in columns:
        conn.execute(
            "alter table preparation_jobs add column job_kind text not null default 'bubble_analysis' "
            "check (job_kind in ('bubble_analysis','graph_extraction'))"
        )
    conn.executescript(GRAPH_TABLES_SQL)


def migrate_graph_snapshot_schema(conn):
    # conn.executescript() implicitly commits any pending transaction before running,
    # which breaks atomicity with the surrounding migration transaction: if this
    # function is interrupted after the executescript() below but before the copy+drop
    # steps finish, the rename+rebuild is already durable on disk even though the
    # migration was never recorded as applied. So "already fully migrated" and
    # "partially migrated, resume the copy" have to be distinguished and both handled -
    # see rebuild_done/legacy_present below - rather than a single early-return check.
    node_columns = {row[1] for row in conn.execute("pragma table_info(kg_nodes)")}
    rebuild_done = "extraction_job_id" in node_columns
    legacy_present = conn.execute(
        "select 1 from sqlite_master where type = 'table' and name = 'kg_nodes_legacy'"
    ).fetchone() is not None
    if rebuild_done:
        conn.execute("drop index if exists idx_kg_extraction_events_job")
        conn.execute("drop table if exists kg_extraction_events")
        if not legacy_present:
            return

    if not legacy_present:
        if conn.execute(
            "select 1 from sqlite_master where type = 'table' and name = 'kg_extraction_events'"
        ).fetchone() is not None:
            conn.execute("alter table kg_extraction_events rename to kg_extraction_events_legacy")
        for table in (
            "kg_node_sources", "kg_edges", "kg_node_embeddings",
            "kg_node_detail_cache", "kg_user_knowledge",
        ):
            conn.execute(f"alter table {table} rename to {table}_legacy")
        conn.execute("alter table kg_nodes rename to kg_nodes_legacy")
        conn.execute("alter table kg_extraction_jobs rename to kg_extraction_jobs_legacy")

        for index in (
            "idx_kg_extraction_jobs_lookup", "idx_kg_extraction_jobs_cache_key", "idx_kg_extraction_events_job",
            "idx_kg_nodes_name", "idx_kg_nodes_type", "idx_kg_node_sources_video", "idx_kg_edges_unique",
            "idx_kg_edges_undirected_unique", "idx_kg_edges_source", "idx_kg_edges_target", "idx_kg_node_detail_cache_node",
        ):
            conn.execute(f"drop index if exists {index}")

    # Runs every time we reach here, not only on the first (not-yet-renamed) pass:
    # if the process died partway through this executescript() on a prior attempt,
    # rebuild_done/legacy_present can both already be true on resume, with some
    # v4 tables still missing. GRAPH_TABLES_SQL is entirely `if not exists`, so
    # re-running it here just fills in whatever didn't get created yet.
    conn.executescript(GRAPH_TABLES_SQL)

    legacy_tables = (
        "kg_extraction_events_legacy", "kg_node_sources_legacy", "kg_edges_legacy", "kg_node_embeddings_legacy",
        "kg_node_detail_cache_legacy", "kg_user_knowledge_legacy", "kg_nodes_legacy", "kg_extraction_jobs_legacy",
    )

    # Structurally-broken or incompletely-migrated jobs are discarded individually -
    # one bad legacy row must not sink every other, unrelated job in the batch.
    # kg_nodes/kg_extraction_jobs cascade-delete to every other kg_* table, so
    # deleting from kg_extraction_jobs alone is enough to fully unwind a job.
    conn.execute("create temporary table if not exists _kg_migration_bad_jobs (job_id text primary key)")
    conn.execute("delete from _kg_migration_bad_jobs")

    def discard_jobs(job_ids):
        if not job_ids:
            return
        conn.executemany("insert or ignore into _kg_migration_bad_jobs (job_id) values (?)", [(j,) for j in job_ids])
        conn.execute("delete from kg_extraction_jobs where job_id in (select job_id from _kg_migration_bad_jobs)")
        conn.execute(
            "update preparation_jobs set status = 'failed', stage = 'failed' "
            "where job_id in (select job_id from _kg_migration_bad_jobs)"
        )

    # A node id was global in v3, so it can be copied into every known extraction
    # job that references it. A dangling reference or missing parent makes the
    # *owning job* unmigratable - collect the specific job ids, not a yes/no flag.
    bad_job_ids = [
        row["job_id"] for row in conn.execute(
            """
            select distinct job_id from (
                select jobs.job_id from kg_extraction_jobs_legacy jobs
                left join preparation_jobs parents on parents.job_id = jobs.job_id
                left join videos on videos.video_id = jobs.video_id
                left join transcript_sources on transcript_sources.transcript_id = jobs.transcript_id
                where parents.job_id is null or videos.video_id is null or transcript_sources.transcript_id is null
                union all
                select sources.extraction_job_id as job_id from kg_node_sources_legacy sources
                left join kg_extraction_jobs_legacy jobs on jobs.job_id = sources.extraction_job_id
                left join kg_nodes_legacy nodes on nodes.node_id = sources.node_id
                left join videos on videos.video_id = sources.video_id
                left join transcript_sources on transcript_sources.transcript_id = sources.transcript_id
                where sources.extraction_job_id is null or jobs.job_id is null or nodes.node_id is null
                    or (sources.video_id is not null and videos.video_id is null)
                    or (sources.transcript_id is not null and transcript_sources.transcript_id is null)
                union all
                select edges.extraction_job_id as job_id from kg_edges_legacy edges
                left join kg_extraction_jobs_legacy jobs on jobs.job_id = edges.extraction_job_id
                left join kg_nodes_legacy source_nodes on source_nodes.node_id = edges.source_node_id
                left join kg_nodes_legacy target_nodes on target_nodes.node_id = edges.target_node_id
                left join kg_relation_types on kg_relation_types.relation_type = edges.relation_type
                where edges.extraction_job_id is null or jobs.job_id is null
                    or source_nodes.node_id is null or target_nodes.node_id is null
                    or kg_relation_types.relation_type is null
            ) where job_id is not null
            """
        ).fetchall()
    ]
    discard_jobs(bad_job_ids)
    # Nodes/embeddings/detail-cache/user-knowledge with no surviving job reference at
    # all (orphaned in v3, or only ever referenced by a now-discarded bad job) simply
    # have nothing to attribute them to - the joins below already drop them silently.

    # Every copy below is `insert or ignore` and excludes _kg_migration_bad_jobs, so
    # re-running after a prior partial copy (rebuild committed, but the process died
    # before the drop-legacy-tables step) just skips rows already copied instead of
    # failing on the primary key, and never re-admits a job just discarded above.
    conn.execute(
        """insert or ignore into kg_extraction_jobs
        (job_id, video_id, transcript_id, cache_key, status, stage, node_count, edge_count, error_code, message, created_at, updated_at)
        select job_id, video_id, transcript_id, cache_key, status, stage, node_count, edge_count, error_code, message, created_at, updated_at
        from kg_extraction_jobs_legacy where job_id not in (select job_id from _kg_migration_bad_jobs)"""
    )
    conn.execute(
        """insert or ignore into kg_nodes
        (extraction_job_id, node_id, canonical_name, node_type, short_summary, detailed_explanation, detail_status,
         confidence, aliases, created_at, updated_at)
        with node_jobs as (
            select node_id, extraction_job_id from kg_node_sources_legacy
                where extraction_job_id is not null and extraction_job_id not in (select job_id from _kg_migration_bad_jobs)
            union
            select source_node_id, extraction_job_id from kg_edges_legacy
                where extraction_job_id is not null and extraction_job_id not in (select job_id from _kg_migration_bad_jobs)
            union
            select target_node_id, extraction_job_id from kg_edges_legacy
                where extraction_job_id is not null and extraction_job_id not in (select job_id from _kg_migration_bad_jobs)
        )
        select node_jobs.extraction_job_id, nodes.node_id, nodes.canonical_name, nodes.node_type, nodes.short_summary,
               nodes.detailed_explanation, nodes.detail_status, nodes.confidence, nodes.aliases,
               nodes.created_at, nodes.updated_at
        from kg_nodes_legacy nodes join node_jobs on node_jobs.node_id = nodes.node_id"""
    )
    conn.execute(
        # The v3 kg_node_sources had no start/end ordering check, so a historical row
        # could have end_seconds < start_seconds; the v4 table enforces one. Swap
        # the pair back into order (only when both are present and reversed) instead
        # of letting the insert fail and strand the row in _legacy indefinitely.
        """insert or ignore into kg_node_sources
        (extraction_job_id, node_id, source_id, video_id, transcript_id, segment_ids, start_seconds, end_seconds, evidence_text, created_at)
        select extraction_job_id, node_id, source_id, video_id, transcript_id, segment_ids,
               case when start_seconds is not null and end_seconds is not null and end_seconds < start_seconds
                    then end_seconds else start_seconds end,
               case when start_seconds is not null and end_seconds is not null and end_seconds < start_seconds
                    then start_seconds else end_seconds end,
               evidence_text, created_at
        from kg_node_sources_legacy
        where extraction_job_id is not null and extraction_job_id not in (select job_id from _kg_migration_bad_jobs)"""
    )
    conn.execute(
        """insert or ignore into kg_edges
        (extraction_job_id, edge_id, source_node_id, target_node_id, relation_type, relation_status, confidence,
         evidence_source_ids, directional, created_at, updated_at)
        select extraction_job_id, edge_id, source_node_id, target_node_id, relation_type, relation_status, confidence,
               evidence_source_ids, directional, created_at, updated_at
        from kg_edges_legacy
        where extraction_job_id is not null and extraction_job_id not in (select job_id from _kg_migration_bad_jobs)"""
    )
    conn.execute(
        """insert or ignore into kg_node_embeddings (extraction_job_id, node_id, model, dims, vector, created_at)
        with node_jobs as (
            select node_id, extraction_job_id from kg_node_sources_legacy
                where extraction_job_id is not null and extraction_job_id not in (select job_id from _kg_migration_bad_jobs)
            union
            select source_node_id, extraction_job_id from kg_edges_legacy
                where extraction_job_id is not null and extraction_job_id not in (select job_id from _kg_migration_bad_jobs)
            union
            select target_node_id, extraction_job_id from kg_edges_legacy
                where extraction_job_id is not null and extraction_job_id not in (select job_id from _kg_migration_bad_jobs)
        )
        select node_jobs.extraction_job_id, embeddings.node_id, embeddings.model, embeddings.dims, embeddings.vector, embeddings.created_at
        from kg_node_embeddings_legacy embeddings join node_jobs on node_jobs.node_id = embeddings.node_id"""
    )
    conn.execute(
        """insert or ignore into kg_node_detail_cache
        (extraction_job_id, node_id, cache_key, lens_hash, lens_json, provider, model, prompt_version, detail_markdown,
         evidence_json, confidence, status, created_at, updated_at)
        with node_jobs as (
            select node_id, extraction_job_id from kg_node_sources_legacy
                where extraction_job_id is not null and extraction_job_id not in (select job_id from _kg_migration_bad_jobs)
            union
            select source_node_id, extraction_job_id from kg_edges_legacy
                where extraction_job_id is not null and extraction_job_id not in (select job_id from _kg_migration_bad_jobs)
            union
            select target_node_id, extraction_job_id from kg_edges_legacy
                where extraction_job_id is not null and extraction_job_id not in (select job_id from _kg_migration_bad_jobs)
        )
        select node_jobs.extraction_job_id, cache.node_id, cache.cache_key, cache.lens_hash, cache.lens_json, cache.provider,
               cache.model, cache.prompt_version, cache.detail_markdown, cache.evidence_json, cache.confidence, cache.status,
               cache.created_at, cache.updated_at
        from kg_node_detail_cache_legacy cache join node_jobs on node_jobs.node_id = cache.node_id"""
    )
    conn.execute(
        """insert or ignore into kg_user_knowledge (extraction_job_id, node_id, status, confidence, last_reviewed_at, notes, updated_at)
        with node_jobs as (
            select node_id, extraction_job_id from kg_node_sources_legacy
                where extraction_job_id is not null and extraction_job_id not in (select job_id from _kg_migration_bad_jobs)
            union
            select source_node_id, extraction_job_id from kg_edges_legacy
                where extraction_job_id is not null and extraction_job_id not in (select job_id from _kg_migration_bad_jobs)
            union
            select target_node_id, extraction_job_id from kg_edges_legacy
                where extraction_job_id is not null and extraction_job_id not in (select job_id from _kg_migration_bad_jobs)
        )
        select node_jobs.extraction_job_id, knowledge.node_id, knowledge.status, knowledge.confidence, knowledge.last_reviewed_at,
               knowledge.notes, knowledge.updated_at
        from kg_user_knowledge_legacy knowledge join node_jobs on node_jobs.node_id = knowledge.node_id"""
    )

    conn.execute(
        """update kg_extraction_jobs
        set node_count = (select count(*) from kg_nodes where extraction_job_id = kg_extraction_jobs.job_id),
            edge_count = (select count(*) from kg_edges where extraction_job_id = kg_extraction_jobs.job_id)
        where job_id in (select job_id from kg_extraction_jobs_legacy)"""
    )
    incomplete_job_ids = [
        row["job_id"] for row in conn.execute(
            """with node_jobs as (
                select node_id, extraction_job_id from kg_node_sources_legacy where extraction_job_id is not null
                union
                select source_node_id, extraction_job_id from kg_edges_legacy where extraction_job_id is not null
                union
                select target_node_id, extraction_job_id from kg_edges_legacy where extraction_job_id is not null
            ), expected as (
                select jobs.job_id,
                    (select count(*) from node_jobs where extraction_job_id = jobs.job_id) as node_count,
                    (select count(*) from kg_node_sources_legacy where extraction_job_id = jobs.job_id) as source_count,
                    (select count(*) from kg_edges_legacy where extraction_job_id = jobs.job_id) as edge_count,
                    (select count(*) from kg_node_embeddings_legacy ancillary
                     where exists (select 1 from node_jobs where node_jobs.node_id = ancillary.node_id)
                       and exists (select 1 from node_jobs where node_jobs.node_id = ancillary.node_id
                                   and node_jobs.extraction_job_id = jobs.job_id)) as embedding_count,
                    (select count(*) from kg_node_detail_cache_legacy ancillary
                     where exists (select 1 from node_jobs where node_jobs.node_id = ancillary.node_id)
                       and exists (select 1 from node_jobs where node_jobs.node_id = ancillary.node_id
                                   and node_jobs.extraction_job_id = jobs.job_id)) as detail_count,
                    (select count(*) from kg_user_knowledge_legacy ancillary
                     where exists (select 1 from node_jobs where node_jobs.node_id = ancillary.node_id)
                       and exists (select 1 from node_jobs where node_jobs.node_id = ancillary.node_id
                                   and node_jobs.extraction_job_id = jobs.job_id)) as knowledge_count
                from kg_extraction_jobs_legacy jobs
                where jobs.job_id not in (select job_id from _kg_migration_bad_jobs)
            )
            select expected.job_id from expected
            left join kg_extraction_jobs active on active.job_id = expected.job_id
            where active.job_id is null
                or coalesce(active.node_count, -1) != expected.node_count
                or coalesce(active.edge_count, -1) != expected.edge_count
                or (select count(*) from kg_nodes where extraction_job_id = expected.job_id) != expected.node_count
                or (select count(*) from kg_node_sources where extraction_job_id = expected.job_id) != expected.source_count
                or (select count(*) from kg_edges where extraction_job_id = expected.job_id) != expected.edge_count
                or (select count(*) from kg_node_embeddings where extraction_job_id = expected.job_id) != expected.embedding_count
                or (select count(*) from kg_node_detail_cache where extraction_job_id = expected.job_id) != expected.detail_count
                or (select count(*) from kg_user_knowledge where extraction_job_id = expected.job_id) != expected.knowledge_count
            """
        ).fetchall()
    ]
    # A job that's still incomplete after a full, uninterrupted copy pass (as opposed
    # to one merely resumed mid-way, which insert-or-ignore already handles above)
    # signals a real mismatch for that job specifically - discard just that job and
    # let every unrelated job in the same legacy batch finish migrating normally.
    discard_jobs(incomplete_job_ids)

    conn.execute("drop table if exists _kg_migration_bad_jobs")
    for table in legacy_tables:
        conn.execute(f"drop table if exists {table}")


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
    (3, "knowledge_graph_extraction", migrate_add_job_kind_and_graph_tables),
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
