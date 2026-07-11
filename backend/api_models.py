from typing import Literal

from pydantic import BaseModel, Field


class PairRequest(BaseModel):
    pairing_code: str = Field(default="", max_length=32)


class PrepareVideoRequest(BaseModel):
    learner_level: Literal["beginner", "intermediate", "advanced"] = "beginner"
    force_refresh: bool = False
    demo_mode: bool = False


class SubtitleUploadRequest(BaseModel):
    video_id: str = Field(max_length=20)
    filename: str = Field(default="", max_length=255)
    content: str


class TranscriptRequest(BaseModel):
    video_id: str = Field(max_length=20)
    demo_mode: bool = False
    current_time: float = 0


class AnalysisRequest(BaseModel):
    video_id: str = Field(max_length=20)
    learner_level: Literal["beginner", "intermediate", "advanced"] = "beginner"
    transcript_id: str = Field(max_length=255)
    force_refresh: bool = False


class TranslationRequest(BaseModel):
    id: str = Field(default="", max_length=255)
    source_text: str = Field(default="", max_length=32_768)
    context_before: str = Field(default="", max_length=32_768)
    context_after: str = Field(default="", max_length=32_768)
    target_language: str = Field(default="zh-TW", max_length=32)
    force_refresh: bool = False
