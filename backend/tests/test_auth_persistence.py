import os
from pathlib import Path
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
        self.tempdir = tempfile.TemporaryDirectory()
        config.set_data_dir(self.tempdir.name)
        init_db()
        with connect_db() as conn:
            conn.execute("delete from session_tokens")
        conn.close()

    def tearDown(self):
        config.set_data_dir(self.original_data_dir)
        self.tempdir.cleanup()

    def test_generated_admin_token_is_private_and_reused(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            auth.initialize_auth()
            generated_token = auth.API_TOKEN
            token_file = Path(self.tempdir.name, "contextbubble.token")

            self.assertTrue(generated_token)
            self.assertEqual(token_file.read_text(encoding="utf-8").strip(), generated_token)
            self.assertEqual(stat.S_IMODE(token_file.stat().st_mode), 0o600)

            auth.initialize_auth()

        self.assertEqual(auth.API_TOKEN, generated_token)

    def test_explicit_admin_token_wins_without_writing_token_file(self):
        configured_token = "configured-admin-token"
        with mock.patch.dict(os.environ, {"CONTEXTBUBBLE_TOKEN": configured_token}, clear=True):
            auth.initialize_auth()

        self.assertEqual(auth.API_TOKEN, configured_token)
        self.assertFalse(Path(self.tempdir.name, "contextbubble.token").exists())

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


if __name__ == "__main__":
    unittest.main()
