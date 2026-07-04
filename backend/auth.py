import hashlib
import re
import secrets
import threading
import time

from config import GEMINI_API_KEY, MAX_BEARER_TOKEN_BYTES


API_TOKEN = __import__('os').environ.get("CONTEXTBUBBLE_TOKEN") or secrets.token_urlsafe(24)
PAIRING_CODE = ""
PAIRING_EXPIRES_AT = time.time() + 5 * 60
PAIRING_USED = False
PAIRING_ATTEMPTS = []
SESSION_SECONDS = 8 * 60 * 60
SESSION_TOKENS = {}
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


reset_pairing_code()


def token_hash(token):
    return hashlib.sha256(token.encode()).hexdigest()
def prune_sessions():
    now = time.time()
    expired = [digest for digest, expires_at in SESSION_TOKENS.items() if expires_at <= now]
    for digest in expired:
        SESSION_TOKENS.pop(digest, None)
def create_session_token():
    token = secrets.token_urlsafe(32)
    expires_at = time.time() + SESSION_SECONDS
    with AUTH_LOCK:
        SESSION_TOKENS[token_hash(token)] = expires_at
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
    with AUTH_LOCK:
        prune_sessions()
        return any(secrets.compare_digest(digest, saved) for saved in SESSION_TOKENS)
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
    with AUTH_LOCK:
        return list(SESSION_TOKENS.keys())
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
