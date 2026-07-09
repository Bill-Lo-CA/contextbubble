import hashlib
import re

from config import GEMINI_MODEL, TRANSLATION_MODE, TRANSLATION_MODEL, TRANSLATION_PROMPT_VERSION, now_iso
from db import connect_db


def active_translation_model():
    if TRANSLATION_MODE == "gemini":
        return "gemini", GEMINI_MODEL
    return "ollama", TRANSLATION_MODEL

def text_hash(*values):
    return hashlib.sha256("\n".join(str(value or "") for value in values).encode()).hexdigest()

def translation_cache_key(segment_id, source_hash, context_hash, target_language, provider, model):
    raw = f"{segment_id}:{source_hash}:{context_hash}:{target_language}:{provider}:{model}:{TRANSLATION_PROMPT_VERSION}"
    return hashlib.sha256(raw.encode()).hexdigest()

def load_translation_cache(cache_key):
    with connect_db() as conn:
        row = conn.execute("select * from translation_cache where cache_key = ?", (cache_key,)).fetchone()
    return dict(row) if row else None

def load_latest_translation_cache(segment_id, target_language, provider, model):
    with connect_db() as conn:
        row = conn.execute(
            """
            select * from translation_cache
            where segment_id = ? and target_language = ? and provider = ? and model = ? and prompt_version = ?
            order by updated_at desc limit 1
            """,
            (segment_id, target_language, provider, model, TRANSLATION_PROMPT_VERSION),
        ).fetchone()
    return dict(row) if row else None

def save_translation_cache(cache_key, segment_id, source_hash, context_hash, target_language, provider, model, result):
    if result.get("status") == "skipped" and result.get("decision") != "skip":
        return
    timestamp = now_iso()
    with connect_db() as conn:
        conn.execute(
            """
            insert or replace into translation_cache values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, coalesce((select created_at from translation_cache where cache_key = ?), ?), ?)
            """,
            (
                cache_key,
                segment_id,
                source_hash,
                context_hash,
                target_language,
                provider,
                model,
                TRANSLATION_PROMPT_VERSION,
                result.get("translated_text", ""),
                float(result.get("confidence", 0.0) or 0.0),
                result.get("status", "failed"),
                result.get("decision", "translate"),
                result.get("reason", ""),
                cache_key,
                timestamp,
                timestamp,
            ),
        )

def translation_decision(segment_id, source_text, context_before="", context_after="", target_language="zh-TW", force_refresh=False):
    provider, model = active_translation_model()
    source_hash = text_hash(source_text)
    context_hash = text_hash(context_before, context_after)
    cache_key = translation_cache_key(segment_id, source_hash, context_hash, target_language, provider, model)
    cached = load_translation_cache(cache_key)
    latest = cached or load_latest_translation_cache(segment_id, target_language, provider, model)
    text = (source_text or "").strip()
    filler = re.fullmatch(r"(?i)(um+|uh+|ah+|hmm+|yeah|okay|ok|right)[\s,.!?]*", text or "")
    metadata = {
        "source_hash": source_hash,
        "context_hash": context_hash,
        "target_language": target_language,
        "provider": provider,
        "model": model,
        "prompt_version": TRANSLATION_PROMPT_VERSION,
    }
    if not text or filler:
        return {**metadata, "cache_key": cache_key, "decision": "skip", "reason": "Empty or filler-only segment.", "cached": cached}
    if force_refresh and latest:
        return {**metadata, "cache_key": cache_key, "decision": "retranslate", "reason": "Force refresh requested.", "cached": latest}
    if cached and cached.get("status") == "translated" and float(cached.get("confidence") or 0) >= 0.75:
        return {**metadata, "cache_key": cache_key, "decision": "use_cache", "reason": "Source, context, target language, model, and prompt version are unchanged.", "cached": cached}
    if cached and cached.get("status") == "skipped" and cached.get("decision") == "skip":
        return {**metadata, "cache_key": cache_key, "decision": "use_cache", "reason": "Cached skipped translation is still current.", "cached": cached}
    if cached and cached.get("status") == "skipped":
        return {**metadata, "cache_key": cache_key, "decision": "retranslate", "reason": "Cached skipped translation is retryable.", "cached": cached}
    if cached:
        return {**metadata, "cache_key": cache_key, "decision": "review", "reason": "Cached translation needs review.", "cached": cached}
    if latest:
        return {**metadata, "cache_key": cache_key, "decision": "retranslate", "reason": "Source or context changed.", "cached": latest}
    return {**metadata, "cache_key": cache_key, "decision": "translate", "reason": "No cached translation is available.", "cached": None}
