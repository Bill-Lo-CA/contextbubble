# Architecture

ContextBubble is a Chromium extension plus a local FastAPI backend.

The extension detects the active YouTube watch page, pairs with the local
backend, starts or resumes a preparation job, polls progress, writes caption and
sentence state scoped by video ID, and renders reviewed bubbles in the YouTube
player.

The backend normalizes transcript sources, persists runtime state in SQLite,
runs concept/review/translation agent workflows, validates bubbles, and exposes
token-protected local HTTP APIs.

Backend modules are split by responsibility:

- `server.py`: FastAPI lifespan, middleware, and routes.
- `config.py`: environment loading, runtime paths, validation, and constants.
- `auth.py`: admin token, pairing code, session tokens, and CORS origin rules.
- `db.py`: SQLite schema and migrations.
- `transcripts.py`: subtitle parsing, transcript storage, sentence entries, and
  deterministic QC helpers.
- `media.py`: external `yt-dlp`, `ffmpeg`, `ffprobe`, and whisper.cpp calls.
- `preparation_jobs.py`: job creation/reuse, payload building, thread startup,
  and resume-on-startup.
- `preparation_runner.py`: preparation job orchestration.
- `caption_pipeline.py`: YouTube caption fetch, caption QC, source routing, and
  demo/ASR fallback selection.
- `asr_pipeline.py`: whole-video ASR, chunk persistence, merging, and media
  cleanup.
- `job_events.py`: preparation event persistence.
- `analysis_agents.py`: concept generation, reviewer logic, and bubble
  validation.
- `analysis_store.py`: analysis cache and bubble persistence.
- `semantic_splitter.py`: transcript sentence/block splitting.
- `translation_agents.py`: translation and translation review.
- `translation_cache.py`: translation cache decisions and persistence.
- `translation_jobs.py`: in-process translation queue and polling state.
- `providers.py`: Gemini and Ollama provider calls.
- `checks.py`: backend self-checks used by validation.

Extension modules are split by responsibility:

- `backendClient.js`: shared backend fetch handling.
- `contentOverlay.js`: bubble overlay rendering.
- `contentOwnerState.js`: analysis owner/session helpers.
- `contentStorage.js`: storage/text helpers.
- `contentTranslations.js`: translation helper logic.
- `contentPreparation.js`: preparation stage display text.
- `contentTimeline.js`: timeline matching helpers.
- `content.js`: content-script orchestration and YouTube page integration.
- `popup.js`: popup pairing, backend checks, analysis requests, and injection
  retry.
- `sidepanel.js`: Side Panel transcript and translation rendering.
