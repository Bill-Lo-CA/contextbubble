import os
from contextlib import closing
from pathlib import Path
import sqlite3
import stat
import sys
import tempfile
import time
import unittest
from unittest import mock


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

import auth
import config
from db import connect_db, init_db


class AuthPersistenceTests(unittest.TestCase):
    def setUp(self):
        self.original_data_dir = config.DATA_DIR
        self.original_auth_state = (
            auth.API_TOKEN,
            auth.PAIRING_CODE,
            auth.PAIRING_EXPIRES_AT,
            auth.PAIRING_USED,
            list(auth.PAIRING_ATTEMPTS),
        )
        self.tempdir = tempfile.TemporaryDirectory()
        config.set_data_dir(self.tempdir.name)
        init_db()
        with connect_db() as conn:
            conn.execute("delete from session_tokens")
        conn.close()

    def tearDown(self):
        (
            auth.API_TOKEN,
            auth.PAIRING_CODE,
            auth.PAIRING_EXPIRES_AT,
            auth.PAIRING_USED,
            attempts,
        ) = self.original_auth_state
        auth.PAIRING_ATTEMPTS[:] = attempts
        config.set_data_dir(self.original_data_dir)
        self.tempdir.cleanup()

    def test_generated_admin_token_is_private_and_reused(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            auth.initialize_auth()
            generated_token = auth.API_TOKEN
            token_file = Path(self.tempdir.name, "contextbubble.token")

            self.assertTrue(generated_token)
            self.assertEqual(token_file.read_text(encoding="utf-8").strip(), generated_token)
            self.assertEqual(stat.S_IMODE(Path(self.tempdir.name).stat().st_mode), 0o700)
            self.assertEqual(stat.S_IMODE(token_file.stat().st_mode), 0o600)

            auth.initialize_auth()

        self.assertEqual(auth.API_TOKEN, generated_token)

    def test_explicit_admin_token_wins_without_writing_token_file(self):
        configured_token = "configured-admin-token"
        with mock.patch.dict(os.environ, {"CONTEXTBUBBLE_TOKEN": configured_token}, clear=True):
            auth.initialize_auth()

        self.assertEqual(auth.API_TOKEN, configured_token)
        self.assertFalse(Path(self.tempdir.name, "contextbubble.token").exists())

    def test_explicit_admin_token_does_not_overwrite_generated_token_file(self):
        token_file = Path(self.tempdir.name, "contextbubble.token")
        with mock.patch.dict(os.environ, {}, clear=True):
            auth.initialize_auth()
        generated_token = token_file.read_text(encoding="utf-8")

        with mock.patch.dict(os.environ, {"CONTEXTBUBBLE_TOKEN": "configured-token"}, clear=True):
            auth.initialize_auth()

        self.assertEqual(auth.API_TOKEN, "configured-token")
        self.assertEqual(token_file.read_text(encoding="utf-8"), generated_token)

    def test_empty_admin_token_setting_generates_persisted_token(self):
        with mock.patch.dict(os.environ, {"CONTEXTBUBBLE_TOKEN": "   "}, clear=True):
            auth.initialize_auth()

        self.assertTrue(auth.API_TOKEN)
        self.assertTrue(Path(self.tempdir.name, "contextbubble.token").exists())

    def test_session_token_survives_auth_reinitialization_with_new_pairing_code(self):
        with mock.patch.dict(os.environ, {"CONTEXTBUBBLE_TOKEN": "admin-token"}, clear=True):
            auth.initialize_auth()
            original_pairing_code = auth.PAIRING_CODE
            session_token, _ = auth.create_session_token()

            replacement_code = "000000" if original_pairing_code != "000000" else "000001"
            with mock.patch.object(auth, "new_pairing_code", return_value=replacement_code):
                auth.initialize_auth()

        self.assertNotEqual(auth.PAIRING_CODE, original_pairing_code)
        self.assertTrue(auth.valid_bearer_token(f"Bearer {session_token}"))
        with connect_db() as conn:
            row = conn.execute("select token_hash from session_tokens").fetchone()
        conn.close()
        self.assertEqual(row["token_hash"], auth.token_hash(session_token))
        self.assertNotEqual(row["token_hash"], session_token)

    def test_initialize_auth_prunes_expired_sessions(self):
        with connect_db() as conn:
            conn.execute(
                "insert into session_tokens (token_hash, expires_at, created_at) values (?, ?, ?)",
                (auth.token_hash("expired-token"), time.time() - 1, config.now_iso()),
            )
        conn.close()

        with mock.patch.dict(os.environ, {"CONTEXTBUBBLE_TOKEN": "admin-token"}, clear=True):
            auth.initialize_auth()

        with connect_db() as conn:
            count = conn.execute("select count(*) from session_tokens").fetchone()[0]
        conn.close()
        self.assertEqual(count, 0)

    def test_failed_session_insert_leaves_pairing_code_usable(self):
        with mock.patch.dict(os.environ, {"CONTEXTBUBBLE_TOKEN": "admin-token"}, clear=True):
            auth.initialize_auth()
        pairing_code = auth.PAIRING_CODE
        with connect_db() as conn:
            conn.execute("""
                create trigger fail_session_insert before insert on session_tokens
                begin
                    select raise(abort, 'forced session insert failure');
                end
            """)
        conn.close()

        with self.assertRaisesRegex(sqlite3.IntegrityError, "forced session insert failure"):
            auth.pair_session(pairing_code)

        self.assertFalse(auth.PAIRING_USED)
        with connect_db() as conn:
            conn.execute("drop trigger fail_session_insert")
        conn.close()
        session_token, _ = auth.pair_session(pairing_code)
        self.assertTrue(auth.valid_bearer_token(f"Bearer {session_token}"))

    def test_session_validation_is_read_only(self):
        with mock.patch.dict(os.environ, {"CONTEXTBUBBLE_TOKEN": "admin-token"}, clear=True):
            auth.initialize_auth()
        session_token, _ = auth.create_session_token()
        database_actions = []
        validation_conn = connect_db()
        validation_conn.set_authorizer(
            lambda action, *_: database_actions.append(action) or sqlite3.SQLITE_OK
        )

        with mock.patch.object(auth, "connect_db", return_value=validation_conn):
            self.assertTrue(auth.valid_bearer_token(f"Bearer {session_token}"))

        self.assertNotIn(sqlite3.SQLITE_DELETE, database_actions)

    def test_init_db_preserves_translation_cache_lookup_index(self):
        with closing(connect_db()) as conn:
            columns = [
                row["name"]
                for row in conn.execute("pragma index_info(idx_translation_cache_lookup)")
            ]

        self.assertEqual(
            columns,
            [
                "segment_id",
                "target_language",
                "provider",
                "model",
                "prompt_version",
            ],
        )


if __name__ == "__main__":
    unittest.main()
