import re
import shutil
from pathlib import Path

from config import FFMPEG_CMD, FFPROBE_CMD, WHISPER_CMD, WHISPER_MODEL, YTDLP_CMD


def _ratio(count, total):
    return count / total if total else 0.0


def _dedupe_key(text):
    return re.sub(r"\W+", " ", (text or "").lower()).strip()


def caption_source_qc(segments, duration_seconds=None, caption_kind="unknown"):
    count = len(segments)
    texts = [segment.get("text", "") for segment in segments]
    empty_count = sum(1 for text in texts if not text.strip())
    keys = [_dedupe_key(text) for text in texts if text.strip()]
    duplicate_count = len(keys) - len(set(keys))
    overlap_count = 0
    short_count = 0
    long_count = 0
    low_punctuation_count = 0
    total_chars = 0
    total_seconds = 0.0

    sorted_segments = sorted(segments, key=lambda item: (item.get("start_seconds", 0), item.get("end_seconds", 0)))
    for index, segment in enumerate(sorted_segments):
        text = segment.get("text", "")
        start = float(segment.get("start_seconds") or 0)
        end = float(segment.get("end_seconds") or start)
        seconds = max(0.001, end - start)
        words = text.split()
        total_chars += len(text)
        total_seconds += seconds
        if index and start < float(sorted_segments[index - 1].get("end_seconds") or 0) - 0.05:
            overlap_count += 1
        if len(words) <= 2:
            short_count += 1
        if len(words) >= 24 or seconds >= 12:
            long_count += 1
        if text and not re.search(r"[.!?。？！,;:]", text):
            low_punctuation_count += 1

    caption_span = 0.0
    if sorted_segments:
        caption_span = max(segment.get("end_seconds", 0) for segment in sorted_segments) - min(segment.get("start_seconds", 0) for segment in sorted_segments)
    coverage_ratio = min(1.0, caption_span / duration_seconds) if duration_seconds else None
    avg_chars_per_second = total_chars / total_seconds if total_seconds else 0.0
    metrics = {
        "coverage_ratio": coverage_ratio,
        "empty_ratio": _ratio(empty_count, count),
        "duplicate_ratio": _ratio(duplicate_count, count),
        "overlap_ratio": _ratio(overlap_count, count),
        "low_punctuation_ratio": _ratio(low_punctuation_count, count),
        "avg_chars_per_second": avg_chars_per_second,
        "very_short_segment_ratio": _ratio(short_count, count),
        "very_long_segment_ratio": _ratio(long_count, count),
        "segment_count": count,
    }

    issues = []
    if coverage_ratio is None:
        issues.append("unknown_duration")
    elif coverage_ratio < 0.35:
        issues.append("low_coverage")
    if metrics["empty_ratio"] > 0.05:
        issues.append("empty_segments")
    if metrics["duplicate_ratio"] > 0.25:
        issues.append("high_duplicate_ratio")
    if metrics["overlap_ratio"] > 0.20:
        issues.append("high_overlap_ratio")
    if metrics["low_punctuation_ratio"] > 0.75 and count >= 4:
        issues.append("low_punctuation")
    if avg_chars_per_second > 28:
        issues.append("too_fast_text_rate")
    if 0 < avg_chars_per_second < 2:
        issues.append("too_slow_text_rate")
    if metrics["very_short_segment_ratio"] > 0.45:
        issues.append("very_short_segments")
    if metrics["very_long_segment_ratio"] > 0.25:
        issues.append("very_long_segments")
    if caption_kind == "auto":
        issues.append("auto_caption_detected")

    poor_issues = {"low_coverage", "empty_segments", "high_duplicate_ratio", "high_overlap_ratio", "too_fast_text_rate", "too_slow_text_rate"}
    if not count or any(issue in issues for issue in poor_issues):
        quality = "poor"
        action = "consider_asr"
    elif issues and issues != ["unknown_duration"]:
        quality = "questionable"
        action = "use_cc_with_warnings"
    else:
        quality = "good"
        action = "use_cc"
    return {
        "source_quality": quality,
        "issues": issues,
        "recommended_action": action,
        "metrics": metrics,
    }


def asr_tools_available():
    return (
        (shutil.which(YTDLP_CMD) or Path(YTDLP_CMD).exists())
        and shutil.which(FFMPEG_CMD)
        and shutil.which(FFPROBE_CMD)
        and (shutil.which(WHISPER_CMD) or Path(WHISPER_CMD).exists())
        and Path(WHISPER_MODEL).exists()
    )


def route_transcript_source(video_id, duration_seconds, caption_source, caption_language, caption_kind, qc_result, sample_segments, asr_available):
    del video_id, duration_seconds, caption_source, caption_language, sample_segments
    quality = qc_result["source_quality"]
    issues = qc_result.get("issues", [])
    if quality == "good":
        return {"decision": "use_cc", "reason": "Caption QC passed.", "windows_to_check": [], "confidence": 0.92}
    if quality == "questionable":
        return {
            "decision": "use_cc_with_warnings",
            "reason": "Captions are usable with quality warnings.",
            "windows_to_check": [],
            "confidence": 0.75 if caption_kind != "auto" else 0.68,
        }
    if asr_available:
        return {
            "decision": "run_whole_video_whisper",
            "reason": "Caption QC is poor and local ASR tools are available.",
            "windows_to_check": [],
            "confidence": 0.78,
        }
    decision = "manual_review_recommended" if "low_coverage" in issues or "high_duplicate_ratio" in issues else "use_cc_with_warnings"
    return {
        "decision": decision,
        "reason": "Caption QC is poor but local ASR tools are unavailable.",
        "windows_to_check": [],
        "confidence": 0.55,
    }
