import hashlib
import html
import json
import re

from config import *
from db import connect_db


TRANSCRIPTS = {}


def parse_time(value):
    value = value.replace(",", ".")
    parts = value.split(":")
    seconds = float(parts[-1])
    minutes = int(parts[-2]) if len(parts) > 1 else 0
    hours = int(parts[-3]) if len(parts) > 2 else 0
    return hours * 3600 + minutes * 60 + seconds
def clean_caption_text(lines):
    text = " ".join(line.strip() for line in lines if line.strip())
    text = re.sub(r"<[^>]+>", "", text)
    text = html.unescape(text)
    text = re.sub(r"\s+", " ", text).strip()
    return text
def normalize_caption_segments(segments):
    normalized = []
    for segment in sorted(segments, key=lambda item: (item["start_seconds"], item["end_seconds"])):
        text = re.sub(r"\s+", " ", segment["text"]).strip()
        if not text:
            continue
        item = {**segment, "text": text}
        if not normalized:
            normalized.append(item)
            continue
        previous = normalized[-1]
        overlap = item["start_seconds"] < previous["end_seconds"] + 0.25
        prev_text = previous["text"]
        if overlap and (text.startswith(prev_text) or prev_text in text):
            normalized[-1] = {**item, "start_seconds": previous["start_seconds"]}
            continue
        if text == prev_text and abs(item["start_seconds"] - previous["start_seconds"]) < 2:
            normalized[-1]["end_seconds"] = max(previous["end_seconds"], item["end_seconds"])
            continue
        normalized.append(item)
    return add_segment_ids(normalized)
def add_segment_ids(segments):
    return [
        {"id": f"segment-{index:03d}", **segment}
        for index, segment in enumerate(segments, 1)
    ]
def parse_subtitles(content, offset_seconds=0):
    raw = []
    lines = content.replace("\ufeff", "").splitlines()
    index = 0

    while index < len(lines):
        line = lines[index].strip()
        if not line or line == "WEBVTT" or line.startswith(("NOTE", "STYLE", "REGION")):
            index += 1
            continue
        if "-->" not in line and index + 1 < len(lines) and "-->" in lines[index + 1]:
            index += 1
            line = lines[index].strip()
        if "-->" not in line:
            index += 1
            continue

        start_text, end_text = [part.strip().split()[0] for part in line.split("-->", 1)]
        index += 1
        text_lines = []
        while index < len(lines) and lines[index].strip():
            text_lines.append(lines[index])
            index += 1

        text = clean_caption_text(text_lines)
        if text:
            raw.append({
                "start_seconds": parse_time(start_text) + offset_seconds,
                "end_seconds": parse_time(end_text) + offset_seconds,
                "text": text,
            })

    return normalize_caption_segments(raw)
def normalized_hash(segments):
    body = json.dumps([
        {
            "start_seconds": round(segment["start_seconds"], 3),
            "end_seconds": round(segment["end_seconds"], 3),
            "text": segment["text"],
        }
        for segment in segments
    ], ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(body.encode()).hexdigest()
def store_transcript(video_id, filename, content="", source="upload", segments=None):
    segments = segments if segments is not None else parse_subtitles(content)
    if not segments:
        return None
    content_hash = normalized_hash(segments)
    transcript_id = f"transcript-{video_id}-{content_hash[:12]}"
    created_at = now_iso()
    with connect_db() as conn:
        conn.execute(
            "insert or replace into videos (video_id, created_at, updated_at) values (?, coalesce((select created_at from videos where video_id = ?), ?), ?)",
            (video_id, video_id, created_at, created_at),
        )
        conn.execute(
            "insert or replace into transcript_sources values (?, ?, ?, ?, ?, ?, ?)",
            (transcript_id, video_id, filename, source, content_hash, len(segments), created_at),
        )
        conn.execute("delete from transcript_segments where transcript_id = ?", (transcript_id,))
        conn.executemany(
            "insert into transcript_segments values (?, ?, ?, ?, ?)",
            [
                (transcript_id, segment["id"], segment["start_seconds"], segment["end_seconds"], segment["text"])
                for segment in segments
            ],
        )
    TRANSCRIPTS[transcript_id] = {
        "video_id": video_id,
        "filename": filename,
        "source": source,
        "segments": segments,
        "content_hash": content_hash,
    }
    return {
        "transcript_id": transcript_id,
        "video_id": video_id,
        "segment_count": len(segments),
        "content_hash": content_hash,
    }
def load_transcript(transcript_id):
    if transcript_id in TRANSCRIPTS:
        return TRANSCRIPTS[transcript_id]
    with connect_db() as conn:
        source = conn.execute("select * from transcript_sources where transcript_id = ?", (transcript_id,)).fetchone()
        if not source:
            return None
        rows = conn.execute(
            "select * from transcript_segments where transcript_id = ? order by start_seconds, segment_id",
            (transcript_id,),
        ).fetchall()
    segments = [
        {
            "id": row["segment_id"],
            "start_seconds": row["start_seconds"],
            "end_seconds": row["end_seconds"],
            "text": row["text"],
        }
        for row in rows
    ]
    transcript = {
        "video_id": source["video_id"],
        "filename": source["filename"],
        "source": source["source"],
        "segments": segments,
        "content_hash": source["content_hash"],
    }
    TRANSCRIPTS[transcript_id] = transcript
    return transcript
def word_count(text):
    return len((text or "").split())
def truncate_words(text, limit):
    words = (text or "").split()
    return " ".join(words[:limit])
def sentence_entries(segments, max_words=40):
    entries = []
    buffer = ""
    source_ids = []
    start_seconds = None
    end_seconds = None

    def emit(text):
        text = re.sub(r"\s+", " ", text).strip()
        if not text:
            return
        entries.append({
            "id": f"sentence-{len(entries) + 1:03d}",
            "start_seconds": start_seconds,
            "end_seconds": end_seconds,
            "text": text,
            "source_segment_ids": list(dict.fromkeys(source_ids)),
            "qc": subtitle_qc_agent(text),
        })

    for segment in segments:
        if start_seconds is None:
            start_seconds = segment["start_seconds"]
        end_seconds = segment["end_seconds"]
        source_ids.append(segment["id"])
        buffer = f"{buffer} {segment['text']}".strip()
        parts = re.findall(r"[^.!?。？！]+[.!?。？！]+|[^.!?。？！]+$", buffer)
        complete_count = len(parts) if buffer.rstrip().endswith((".", "!", "?", "。", "？", "！")) else max(0, len(parts) - 1)
        for sentence in parts[:complete_count]:
            emit(sentence)
            start_seconds = segment["start_seconds"]
            source_ids = [segment["id"]]
        buffer = "" if complete_count == len(parts) else parts[-1]
        if word_count(buffer) >= max_words:
            emit(buffer)
            buffer = ""
            start_seconds = None
            source_ids = []

    if buffer:
        emit(buffer)
    return entries
def subtitle_qc(text):
    clean = re.sub(r"\s+", " ", text or "").strip()
    issues = []
    status = "accepted"
    revised = None
    confidence = 0.93
    if not clean:
        return {"status": "needs_review", "issues": ["empty"], "revised_source_text": None, "confidence": 0.0}
    if len(clean.split()) <= 2:
        issues.append("too_short")
        status = "needs_review"
        confidence = 0.55
    if re.search(r"\b(um+|uh+)\b", clean, re.I):
        issues.append("filler_words")
        revised = re.sub(r"\b(um+|uh+)\b", "", clean, flags=re.I)
        revised = re.sub(r"\s+", " ", revised).strip()
        status = "revised"
        confidence = min(confidence, 0.76)
    if len(re.findall(r"[A-Za-z]", clean)) < max(1, len(clean) // 3):
        issues.append("low_text_confidence")
        status = "needs_review" if status == "accepted" else status
        confidence = min(confidence, 0.62)
    return {"status": status, "issues": issues, "revised_source_text": revised, "confidence": confidence}
def subtitle_qc_agent(text):
    return subtitle_qc(text)
def translation_qc(source_text, translation_text, context_sentences=None, glossary_terms=None):
    del context_sentences
    source = source_text or ""
    translation = translation_text or ""
    glossary_terms = glossary_terms or []
    issues = []
    revised = None
    status = "accepted"
    confidence = 0.9
    if not translation.strip():
        issues.append("missing_translation")
        status = "needs_review"
        confidence = 0.2
    for term in glossary_terms:
        if term and term.lower() in source.lower() and term.lower() not in translation.lower():
            issues.append(f"missing_term:{term}")
            status = "needs_review"
            confidence = min(confidence, 0.55)
    if len(translation.split()) > max(8, len(source.split()) * 3):
        issues.append("unsupported_expansion")
        revised = truncate_words(translation, max(8, len(source.split()) * 2))
        status = "revised"
        confidence = min(confidence, 0.65)
    return {"status": status, "issues": issues, "revised_translation_text": revised, "confidence": confidence}
def translation_qc_agent(source_text, translation_text, context_sentences=None, glossary_terms=None):
    return translation_qc(source_text, translation_text, context_sentences, glossary_terms)
