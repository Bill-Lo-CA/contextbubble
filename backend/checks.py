import hashlib
from pathlib import Path
import time

from agents import *
import auth
from config import *
from db import connect_db, init_db
from jobs import update_job
from media import *
from transcript_quality import *
from transcripts import *


def self_check_auth():
    with connect_db() as conn:
        conn.execute("delete from session_tokens")
    auth.initialize_auth()
    auth.reset_pairing_for_check()
    token, expires_at = auth.pair_session(auth.PAIRING_CODE)
    assert token
    assert expires_at > time.time()
    assert auth.valid_bearer_token(f"Bearer {token}")
    assert not auth.valid_bearer_token("Bearer " + ("x" * (MAX_BEARER_TOKEN_BYTES + 1)))
    try:
        auth.pair_session(auth.PAIRING_CODE)
        raise AssertionError("used pairing code accepted")
    except ValueError:
        pass
    auth.reset_pairing_for_check()
    try:
        auth.pair_session("000000" if auth.PAIRING_CODE != "000000" else "000001")
        raise AssertionError("wrong pairing code accepted")
    except PermissionError:
        pass
    try:
        for _ in range(auth.PAIRING_LIMIT):
            try:
                auth.pair_session("000000" if auth.PAIRING_CODE != "000000" else "000001")
            except PermissionError:
                pass
        auth.pair_session("000000" if auth.PAIRING_CODE != "000000" else "000001")
        raise AssertionError("pairing rate limit did not trigger")
    except RuntimeError:
        pass
    auth.reset_pairing_for_check()
    assert auth.expired_pairing_rejected()
    auth.reset_pairing_for_check()


def self_check_subtitles():
    vtt, srt, progressive = self_check_subtitle_fixtures()
    assert parse_subtitles(vtt) == [{
        "id": "segment-001",
        "start_seconds": 1.0,
        "end_seconds": 3.5,
        "text": "Embeddings are numeric representations of text.",
    }]
    assert parse_subtitles(srt)[0]["start_seconds"] == 4.0
    assert parse_subtitles(vtt, 10)[0]["start_seconds"] == 11.0
    assert parse_subtitles(srt, 120)[0]["end_seconds"] == 126.25
    assert parse_subtitles(progressive)[0]["text"] == "Embeddings are numeric representations."
    assert len(parse_subtitles(progressive)) == 1
    multiline = """WEBVTT

00:00:01.000 --> 00:00:03.500
Line one
line two
"""
    assert parse_subtitles(multiline)[0]["text"] == "Line one line two"
    assert parse_subtitles(multiline.replace("Line one", "&gt;&gt; Speaker"))[0]["text"] == ">> Speaker line two"


def self_check_subtitle_fixtures():
    return """WEBVTT

00:00:01.000 --> 00:00:03.500
Embeddings are numeric representations of text.
""", """1
00:00:04,000 --> 00:00:06,250
Cosine similarity compares vector direction.
""", """WEBVTT

00:00:01.000 --> 00:00:02.000
Embeddings are

00:00:01.100 --> 00:00:03.000
Embeddings are numeric representations.
"""


def self_check_media_helpers():
    assert create_chunks(65) == [
        {"chunk_index": 0, "start_seconds": 0.0, "end_seconds": 30.0},
        {"chunk_index": 1, "start_seconds": 28.0, "end_seconds": 58.0},
        {"chunk_index": 2, "start_seconds": 56.0, "end_seconds": 65},
    ]
    merged = merge_transcript_segments([
        {"start_seconds": 0, "end_seconds": 5, "text": "hello world from chunk"},
        {"start_seconds": 4, "end_seconds": 8, "text": "from chunk boundary"},
    ])
    assert merged[0]["text"] == "hello world from chunk boundary"
    assert format_section_time(65) == "00:01:05"
    assert parse_duration_output("123.5\n") == 123.5
    try:
        parse_duration_output("not-a-duration\n")
        raise AssertionError("invalid duration accepted")
    except RuntimeError:
        pass
    assert "ollama" in AGENT_MODES
    assert TRANSLATION_MODE == "ollama"
    assert TRANSLATION_MODEL == "qwen3:8b"
    assert TRANSCRIPT_BLOCK_SPLITTER_MODE == "ollama"
    assert TRANSCRIPT_BLOCK_SPLITTER_MODEL == "llama3.2:3b"


def self_check_sentence_qc():
    vtt, srt, _ = self_check_subtitle_fixtures()
    segments = parse_subtitles(vtt + "\n" + srt)
    two_sentences = sentence_entries([{
        "id": "segment-001",
        "start_seconds": 1,
        "end_seconds": 3,
        "text": "One sentence. Another sentence.全形句子。下一句。",
    }])
    assert len(two_sentences) == 4
    split_sentence = sentence_entries([
        {"id": "segment-001", "start_seconds": 1, "end_seconds": 2, "text": "Embeddings are"},
        {"id": "segment-002", "start_seconds": 2, "end_seconds": 3, "text": "numeric representations."},
    ])
    assert split_sentence == [{
        "id": "sentence-001",
        "start_seconds": 1,
        "end_seconds": 3,
        "text": "Embeddings are numeric representations.",
        "source_segment_ids": ["segment-001", "segment-002"],
        "qc": {"status": "accepted", "issues": [], "revised_source_text": None, "confidence": 0.93},
    }]
    assert subtitle_qc("um embeddings matter")["status"] == "revised"
    assert translation_qc("cosine similarity", "相似度", glossary_terms=["cosine"])["status"] == "needs_review"
    assert valid_concept_candidate({
        "concept": "embeddings",
        "anchor_segment_id": "segment-001",
        "source_segment_ids": ["segment-001"],
        "start_seconds": 1.0,
        "short_explanation": "short",
        "expanded_explanation": "long",
        "confidence": 0.9,
    })
    assert not valid_concept_candidate({"concept": "bad"})
    assert valid_reviewer_result({"review_status": "accepted", "candidate": {}})
    assert not valid_reviewer_result({"review_status": "surprise"})
    assert needs_translation_review("hello world", "", 0.9)
    assert needs_translation_review("hello world", "哈囉世界", 0.5)
    assert not needs_translation_review("hello world", "哈囉世界", 0.9)
    assert len(transcript_windows([{"id": str(index)} for index in range(90)], size=50, overlap=5)) == 2
    assert len(time_windows([
        {"start_seconds": 0, "end_seconds": 1, "text": "a"},
        {"start_seconds": 31, "end_seconds": 32, "text": "b"},
    ])) == 2
    return segments


def self_check_transcript_source_quality():
    clean = [
        {"id": "segment-001", "start_seconds": 0, "end_seconds": 3, "text": "Embeddings represent text."},
        {"id": "segment-002", "start_seconds": 3, "end_seconds": 6, "text": "Vector search compares meaning."},
        {"id": "segment-003", "start_seconds": 6, "end_seconds": 9, "text": "The reviewer checks grounding."},
    ]
    clean_qc = caption_source_qc(clean, 9)
    assert clean_qc["source_quality"] == "good"
    assert clean_qc["recommended_action"] == "use_cc"
    assert clean_qc["metrics"]["segment_count"] == 3

    repeated = [
        {"id": f"segment-{index:03d}", "start_seconds": index, "end_seconds": index + 1, "text": "embeddings embeddings"}
        for index in range(1, 8)
    ]
    repeated_qc = caption_source_qc(repeated, 7, "auto")
    assert repeated_qc["source_quality"] in ("questionable", "poor")
    assert "auto_caption_detected" in repeated_qc["issues"]

    low_coverage_qc = caption_source_qc(clean, 120)
    assert low_coverage_qc["source_quality"] == "poor"
    assert "low_coverage" in low_coverage_qc["issues"]

    poor_route = route_transcript_source("demoid", 120, "youtube_caption", "en", "unknown", low_coverage_qc, clean[:1], False)
    assert poor_route["decision"] in ("manual_review_recommended", "use_cc_with_warnings")
    good_route = route_transcript_source("demoid", 9, "youtube_caption", "en", "unknown", clean_qc, clean[:1], True)
    assert good_route["decision"] == "use_cc"


def self_check_analysis_and_storage():
    vtt, _, _ = self_check_subtitle_fixtures()
    segments = parse_subtitles(vtt)
    reviewed = [{
        "concept": "embeddings",
        "anchor_segment_id": "segment-001",
        "source_segment_ids": ["segment-001"],
        "start_seconds": 1.0,
        "short_explanation": "Embeddings are numeric representations of text.",
        "expanded_explanation": "They let software compare meaning using vector math.",
        "confidence": 0.9,
        "review_status": "accepted",
    }]
    assert validate_bubbles(reviewed, segments)
    repeated = merge_transcript_segments([
        {"start_seconds": 0, "end_seconds": 5, "text": "repeat phrase"},
        {"start_seconds": 4, "end_seconds": 6, "text": "repeat phrase"},
        {"start_seconds": 300, "end_seconds": 305, "text": "repeat phrase"},
    ])
    assert len(repeated) == 2
    stored = store_transcript("demo", "demo.vtt", vtt, "demo_fixture")
    assert stored["segment_count"] == 1
    assert load_transcript(stored["transcript_id"])["segments"][0]["id"] == "segment-001"
    stored_with_metadata = store_transcript("demo", "demo.vtt", vtt, "manual_upload", metadata={"caption_qc": {"source_quality": "good"}})
    assert load_transcript(stored_with_metadata["transcript_id"])["metadata"]["caption_qc"]["source_quality"] == "good"
    analysis = run_analysis_for_transcript("demo", "beginner", stored["transcript_id"], True)
    assert analysis["status"] == "completed"
    assert analysis["analysis_metrics"]["accepted_bubble_count"] >= 0
    with open(Path(__file__).resolve().parent / "fixtures/demo.vtt", encoding="utf-8") as file:
        demo_segments = parse_subtitles(file.read())
    assert len(demo_segments) >= 6
    assert demo_segments[1]["start_seconds"] - demo_segments[0]["start_seconds"] >= 30
    try:
        validate_video_id("../../bad")
        raise AssertionError("invalid video id accepted")
    except ValueError:
        pass
    assert hashlib.sha256(b"demo").hexdigest()


def self_check_security_helpers():
    secret = f"{auth.API_TOKEN} {auth.PAIRING_CODE} key={GEMINI_API_KEY or 'demo'}"
    redacted = auth.redact_secret_text(secret)
    assert auth.API_TOKEN not in redacted
    assert auth.PAIRING_CODE not in redacted
    assert "key=[redacted]" in redacted
    try:
        update_job("missing", not_a_column=True)
        raise AssertionError("invalid update field accepted")
    except ValueError:
        pass


def self_check_translation_decisions():
    skipped = translate_segment("segment-empty", "", "", "", "zh-TW")
    assert skipped["status"] == "skipped"
    assert skipped["decision"] == "skip"
    decision = translation_decision("segment-001", "hello world", "", "", "zh-TW")
    result = {
        "translated_text": "哈囉世界",
        "confidence": 0.91,
        "status": "translated",
        "decision": "translate",
        "reason": "",
    }
    save_translation_cache(
        decision["cache_key"],
        "segment-001",
        decision["source_hash"],
        decision["context_hash"],
        "zh-TW",
        decision["provider"],
        decision["model"],
        result,
    )
    cached = translate_segment("segment-001", "hello world", "", "", "zh-TW")
    assert cached["decision"] == "use_cache"
    assert cached["translated_text"] == "哈囉世界"
    assert translation_decision("segment-001", "hello world changed", "", "", "zh-TW")["decision"] == "retranslate"
    assert translation_decision("segment-001", "hello world", "", "", "zh-TW", True)["decision"] == "retranslate"
    retryable = translation_decision("segment-provider-down", "retry me", "", "", "zh-TW")
    save_translation_cache(
        retryable["cache_key"],
        "segment-provider-down",
        retryable["source_hash"],
        retryable["context_hash"],
        "zh-TW",
        retryable["provider"],
        retryable["model"],
        {"translated_text": "", "confidence": 0, "status": "skipped", "decision": "translate", "reason": "provider unavailable"},
    )
    assert load_translation_cache(retryable["cache_key"]) is None
    with connect_db() as conn:
        conn.execute(
            "insert into translation_cache values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                retryable["cache_key"],
                "segment-provider-down",
                retryable["source_hash"],
                retryable["context_hash"],
                "zh-TW",
                retryable["provider"],
                retryable["model"],
                TRANSLATION_PROMPT_VERSION,
                "",
                0.0,
                "skipped",
                "translate",
                "provider unavailable",
                now_iso(),
                now_iso(),
            ),
        )
    assert translation_decision("segment-provider-down", "retry me", "", "", "zh-TW")["decision"] == "retranslate"


def self_check():
    validate_config()
    init_db()
    self_check_auth()
    self_check_subtitles()
    self_check_media_helpers()
    self_check_sentence_qc()
    self_check_transcript_source_quality()
    self_check_analysis_and_storage()
    self_check_security_helpers()
    self_check_translation_decisions()
