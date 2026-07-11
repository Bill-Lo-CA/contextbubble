from config import validate_config
from db import connect_db, init_db


def self_check():
    validate_config()
    init_db()
    with connect_db() as conn:
        conn.execute("select 1").fetchone()
