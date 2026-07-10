import sqlite3
from contextlib import closing

import config
from migrations import apply_migrations


def connect_db():
    settings = config.get_settings()
    config.ensure_private_dir(settings.data_dir)
    conn = sqlite3.connect(settings.db_file, timeout=5)
    conn.row_factory = sqlite3.Row
    conn.execute("pragma journal_mode=WAL")
    conn.execute("pragma busy_timeout = 5000")
    conn.execute("pragma foreign_keys = ON")
    return conn


def ensure_column(conn, table, column, definition):
    columns = {row["name"] for row in conn.execute(f"pragma table_info({table})")}
    if column not in columns:
        conn.execute(f"alter table {table} add column {column} {definition}")


def table_exists(conn, table):
    return conn.execute("select 1 from sqlite_master where type = 'table' and name = ?", (table,)).fetchone() is not None


def init_db():
    with closing(connect_db()) as conn, conn:
        if table_exists(conn, "preparation_jobs"):
            ensure_column(conn, "preparation_jobs", "source_policy", "text not null default 'live'")
        if table_exists(conn, "transcript_sources"):
            ensure_column(conn, "transcript_sources", "metadata", "text default '{}'")
        apply_migrations(conn, config.now_iso())
