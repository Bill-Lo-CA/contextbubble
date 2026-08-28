import hashlib
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


def update_allowed_columns(table, id_column, id_value, allowed_columns, **values):
    """Allow-listed dynamic `update {table} set ...`, stamping updated_at.

    Shared by every job-like table that supports partial status/stage updates
    (preparation_jobs, kg_extraction_jobs) so the allow-list-and-validate
    pattern isn't hand-copied per table.
    """
    if not values:
        return
    unknown = set(values) - allowed_columns
    if unknown:
        raise ValueError(f"invalid {table} update fields: {', '.join(sorted(unknown))}")
    values["updated_at"] = config.now_iso()
    assignments = ", ".join(f"{key} = ?" for key in values)
    with connect_db() as conn:
        conn.execute(f"update {table} set {assignments} where {id_column} = ?", (*values.values(), id_value))


def short_hash_id(prefix, *parts):
    digest = hashlib.sha256(":".join(str(part) for part in parts).encode()).hexdigest()
    return f"{prefix}-{digest[:12]}"


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
