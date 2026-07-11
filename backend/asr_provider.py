import os
from pathlib import Path

import config
from media import run_command
from transcripts import parse_subtitles


class WhisperCppProvider:
    name = "whisper.cpp"

    def transcribe(self, audio_path, chunk, directory, job_id):
        settings = config.get_settings()
        chunk_path = os.path.join(directory, f"chunk-{chunk['chunk_index']:04d}.wav")
        run_command([settings.ffmpeg_cmd, "-y", "-ss", str(chunk["start_seconds"]), "-to", str(chunk["end_seconds"]), "-i", audio_path, "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le", chunk_path], job_id, "transcribing", 120, chunk["chunk_index"])
        transcript_base = os.path.join(directory, f"chunk-{chunk['chunk_index']:04d}")
        command = [settings.whisper_cmd, "-m", settings.whisper_model, "-f", chunk_path, "-l", settings.whisper_language, "-ovtt", "-of", transcript_base, "-np"]
        if settings.whisper_no_gpu:
            command.append("-ng")
        run_command(command, job_id, "transcribing", 900, chunk["chunk_index"])
        return parse_subtitles(Path(f"{transcript_base}.vtt").read_text(encoding="utf-8"), chunk["start_seconds"])

    def metadata(self):
        settings = config.get_settings()
        return {"provider": self.name, "model": Path(settings.whisper_model).name, "language": settings.whisper_language, "gpu": not settings.whisper_no_gpu}


whisper_cpp = WhisperCppProvider()
ASR_PROVIDERS = {"whisper_cpp": whisper_cpp}


def get_asr_provider(settings=None):
    settings = settings or config.get_settings()
    return ASR_PROVIDERS[settings.asr_provider]
