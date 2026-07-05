import hashlib
import os
import re
import secrets
import sqlite3
import threading
import time
from contextlib import closing

import config
from config import GEMINI_API_KEY, MAX_BEARER_TOKEN_BYTES, now_iso
from db import connect_db


API_TOKEN = ""
PAIRING_CODE = ""
PAIRING_EXPIRES_AT = 0
PAIRING_USED = False
PAIRING_ATTEMPTS = []
SESSION_SECONDS = 8 * 60 * 60
PAIRING_LIMIT = 5
PAIRING_WINDOW_SECONDS = 60
AUTH_LOCK = threading.Lock()


def new_pairing_code():
    return f"{secrets.randbelow(1_000_000):06d}"


def reset_pairing_code():
    global PAIRING_CODE, PAIRING_EXPIRES_AT, PAIRING_USED
    with AUTH_LOCK:
        PAIRING_CODE = new_pairing_code()
        PAIRING_EXPIRES_AT = time.time() + 5 * 60
        PAIRING_USED = False
        PAIRING_ATTEMPTS.clear()
        return PAIRING_CODE, PAIRING_EXPIRES_AT


def token_hash(token):
    return hashlib.sha256(token.encode()).hexdigest()


def _valid_token(value):
    value = value.strip()
    if not value:
        raise ValueError("CONTEXTBUBBLE_TOKEN must not be empty")
    if len(value.encode()) > MAX_BEARER_TOKEN_BYTES:
        raise ValueError("CONTEXTBUBBLE_TOKEN is too large")
    return value


def _write_admin_token(token_file, token):
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    temp_file = token_file.with_name(f".{token_file.name}.{os.getpid()}.{secrets.token_hex(8)}.tmp")
    try:
        descriptor = os.open(temp_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as file:
            file.write(token + "\n")
            file.flush()
            os.fsync(file.fileno())
        os.replace(temp_file, token_file)
        os.chmod(token_file, 0o600)
    finally:
        try:
            temp_file.unlink()
        except FileNotFoundError:
            pass


def load_or_create_admin_token():
    configured_token = os.environ.get("CONTEXTBUBBLE_TOKEN", "").strip()
    if configured_token:
        return _valid_token(configured_token)

    token_file = config.DATA_DIR / "contextbubble.token"
    try:
        token = _valid_token(token_file.read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        token = secrets.token_urlsafe(24)
        _write_admin_token(token_file, token)
        return token

    os.chmod(token_file, 0o600)
    return token


def _prune_sessions(conn, now=None):
    conn.execute("delete from session_tokens where expires_at <= ?", (time.time() if now is None else now,))


def prune_sessions():
    with AUTH_LOCK, closing(connect_db()) as conn, conn:
        _prune_sessions(conn)


def initialize_auth():
    global API_TOKEN, PAIRING_CODE, PAIRING_EXPIRES_AT, PAIRING_USED
    admin_token = load_or_create_admin_token()
    with AUTH_LOCK, closing(connect_db()) as conn, conn:
        API_TOKEN = admin_token
        _prune_sessions(conn)
        PAIRING_CODE = new_pairing_code()
        PAIRING_EXPIRES_AT = time.time() + 5 * 60
        PAIRING_USED = False
        PAIRING_ATTEMPTS.clear()
    return API_TOKEN


def create_session_token():
    token = secrets.token_urlsafe(32)
    expires_at = time.time() + SESSION_SECONDS
    with AUTH_LOCK, closing(connect_db()) as conn, conn:
        conn.execute(
            "insert into session_tokens (token_hash, expires_at, created_at) values (?, ?, ?)",
            (token_hash(token), expires_at, now_iso()),
        )
    return token, expires_at


def valid_bearer_token(header):
    prefix = "Bearer "
    if not header.startswith(prefix):
        return False
    token = header[len(prefix):]
    if not token or len(token.encode()) > MAX_BEARER_TOKEN_BYTES:
        return False
    if secrets.compare_digest(token, API_TOKEN):
        return True
    digest = token_hash(token)
    with AUTH_LOCK, closing(connect_db()) as conn, conn:
        _prune_sessions(conn)
        row = conn.execute(
            "select token_hash from session_tokens where token_hash = ? and expires_at > ?",
            (digest, time.time()),
        ).fetchone()
        return row is not None and secrets.compare_digest(digest, row["token_hash"])
def pair_session(pairing_code):
    global PAIRING_USED
    with AUTH_LOCK:
        now = time.time()
        PAIRING_ATTEMPTS[:] = [item for item in PAIRING_ATTEMPTS if now - item < PAIRING_WINDOW_SECONDS]
        if len(PAIRING_ATTEMPTS) >= PAIRING_LIMIT:
            raise RuntimeError("pairing rate limited")
        if time.time() > PAIRING_EXPIRES_AT:
            raise ValueError("pairing code expired")
        if PAIRING_USED:
            raise ValueError("pairing code already used")
        if not secrets.compare_digest(str(pairing_code), PAIRING_CODE):
            PAIRING_ATTEMPTS.append(now)
            raise PermissionError("invalid pairing code")
        PAIRING_USED = True
        PAIRING_ATTEMPTS.clear()
    return create_session_token()
def expired_pairing_rejected():
    global PAIRING_EXPIRES_AT, PAIRING_USED
    with AUTH_LOCK:
        original_expires = PAIRING_EXPIRES_AT
        original_used = PAIRING_USED
        PAIRING_EXPIRES_AT = time.time() - 1
        PAIRING_USED = False
    try:
        pair_session(PAIRING_CODE)
        return False
    except ValueError:
        return True
    finally:
        with AUTH_LOCK:
            PAIRING_EXPIRES_AT = original_expires
            PAIRING_USED = original_used
def reset_pairing_for_check():
    global PAIRING_USED, PAIRING_EXPIRES_AT
    with AUTH_LOCK:
        PAIRING_USED = False
        PAIRING_EXPIRES_AT = time.time() + 5 * 60
        PAIRING_ATTEMPTS.clear()
def active_session_token_hashes():
    try:
        with AUTH_LOCK, closing(connect_db()) as conn, conn:
            _prune_sessions(conn)
            return [row["token_hash"] for row in conn.execute(
                "select token_hash from session_tokens where expires_at > ?",
                (time.time(),),
            )]
    except sqlite3.OperationalError:
        return []
def redact_secret_text(text):
    text = str(text)
    secrets_to_redact = [API_TOKEN, PAIRING_CODE, GEMINI_API_KEY]
    for secret in secrets_to_redact:
        if secret:
            text = text.replace(secret, "[redacted]")
    for digest in active_session_token_hashes():
        text = text.replace(digest, "[redacted]")
    text = re.sub(r"key=([^&\s]+)", "key=[redacted]", text)
    return text
def allowed_origin(origin, path=""):
    if origin.startswith("chrome-extension://"):
        return True
    if path in ("/api/pair", "/api/pair/resend"):
        return False
    return origin == "https://www.youtube.com"
