from pathlib import Path
import sys
import unittest

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path: sys.path.insert(0, str(BACKEND_DIR))

from media import create_chunks, merge_transcript_segments, parse_duration_output
from transcripts import parse_subtitles, sentence_entries, subtitle_qc, translation_qc


class TranscriptTests(unittest.TestCase):
    def test_subtitles_chunks_and_overlap_merge(self):
        vtt = "WEBVTT\n\n00:00:01.000 --> 00:00:03.500\nLine one\nline two\n"
        self.assertEqual(parse_subtitles(vtt)[0]["text"], "Line one line two")
        self.assertEqual(len(create_chunks(65)), 3)
        merged = merge_transcript_segments([
            {"start_seconds": 0, "end_seconds": 5, "text": "hello world from chunk"},
            {"start_seconds": 4, "end_seconds": 8, "text": "from chunk boundary"},
        ])
        self.assertEqual(merged[0]["text"], "hello world from chunk boundary")
        with self.assertRaises(RuntimeError):
            parse_duration_output("invalid")

    def test_sentence_and_translation_quality(self):
        entries = sentence_entries([{"id": "segment-1", "start_seconds": 1, "end_seconds": 3, "text": "One sentence. Another sentence."}])
        self.assertEqual(len(entries), 2)
        self.assertEqual(subtitle_qc("um embeddings matter")["status"], "revised")
        self.assertEqual(translation_qc("cosine similarity", "相似度", glossary_terms=["cosine"])["status"], "needs_review")


if __name__ == "__main__": unittest.main()
