from dataclasses import replace
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

import config
from db import connect_db, init_db


class MigrationTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.context = config.settings_override(replace(config.get_settings(), data_dir=Path(self.tempdir.name)))
        self.context.__enter__()

    def tearDown(self):
        self.context.__exit__(None, None, None)
        self.tempdir.cleanup()

    def test_fresh_schema_has_constraints_and_migration_record(self):
        init_db()
        with connect_db() as conn:
            self.assertEqual(conn.execute("select name from schema_migrations where version = 1").fetchone()[0], "initial_schema")
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute("insert into videos values ('video1', 'now', 'now')")
                conn.execute("insert into preparation_jobs (job_id, video_id, learner_level, status, stage, progress, created_at, updated_at) values ('job1', 'video1', 'beginner', 'queued', 'queued', 2, 'now', 'now')")

    def test_legacy_schema_upgrades_without_losing_rows(self):
        with connect_db() as conn:
            conn.executescript("""
                create table videos (video_id text primary key, created_at text not null, updated_at text not null);
                insert into videos values ('legacy', 'then', 'then');
                create table preparation_jobs (job_id text primary key, video_id text not null, learner_level text not null, status text not null, stage text not null, created_at text not null, updated_at text not null);
                create table transcript_sources (transcript_id text primary key, video_id text not null, filename text not null, source text not null, content_hash text not null, segment_count integer not null, created_at text not null);
                create table schema_migrations (name text primary key, applied_at text not null);
            """)
        init_db()
        with connect_db() as conn:
            self.assertEqual(conn.execute("select video_id from videos").fetchone()[0], "legacy")
            self.assertIn("source_policy", {row["name"] for row in conn.execute("pragma table_info(preparation_jobs)")})
            self.assertIn("metadata", {row["name"] for row in conn.execute("pragma table_info(transcript_sources)")})


if __name__ == "__main__":
    unittest.main()
