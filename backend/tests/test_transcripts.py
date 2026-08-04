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

    def test_sentence_entries_own_their_time_ranges_and_sources(self):
        def segment(identifier, start, end, text):
            return {"id": identifier, "start_seconds": start, "end_seconds": end, "text": text}

        def summary(entries):
            return [(entry["text"], entry["start_seconds"], entry["end_seconds"], entry["source_segment_ids"]) for entry in entries]

        self.assertEqual(summary(sentence_entries([
            segment("one", 0, 1, "First."), segment("two", 2, 3, "Second."),
        ])), [("First.", 0, 1, ["one"]), ("Second.", 2, 3, ["two"])])
        self.assertEqual(summary(sentence_entries([
            segment("three", 4, 5, "Third. Fourth."),
        ])), [("Third.", 4, 5, ["three"]), ("Fourth.", 4, 5, ["three"])])
        self.assertEqual(summary(sentence_entries([
            segment("five", 6, 7, "This starts"), segment("six", 8, 9, "and ends."),
        ])), [("This starts and ends.", 6, 9, ["five", "six"])])
        self.assertEqual(summary(sentence_entries([
            segment("seven", 10, 11, "One two three"), segment("eight", 12, 13, "Four five"),
        ], max_words=3)), [("One two three", 10, 11, ["seven"]), ("Four five", 12, 13, ["eight"])])
        self.assertEqual(summary(sentence_entries([
            segment("nine", 14, 15, "Start of"), segment("ten", 16, 17, "a sentence. Next"), segment("eleven", 18, 19, "sentence ends."),
        ])), [("Start of a sentence.", 14, 17, ["nine", "ten"]), ("Next sentence ends.", 16, 19, ["ten", "eleven"])])


if __name__ == "__main__": unittest.main()
