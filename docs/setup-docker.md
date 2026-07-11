# Docker Setup

Docker Compose is the supported runtime path for normal local use. It avoids
host Python dependency setup, whisper.cpp builds, and platform-specific native
ASR paths.

From the repo root:

```sh
docker compose up --build
```

This command runs attached. In another terminal, read the short pairing code:

```sh
docker compose logs backend
```

The admin token is never written to logs. The extension only needs the pairing
code. For explicit command-line administration, set `CONTEXTBUBBLE_TOKEN` in
`.env` or read the private generated file deliberately:

```sh
docker compose exec backend cat /data/contextbubble.token
```

The API is available only at `http://127.0.0.1:8000`; the Compose port binding
does not expose it to the LAN.

## Configuration

No `.env` file is needed because Compose supplies defaults. Compose reads a
repo-root `.env` only for variable interpolation; it is not copied into the
image because `.dockerignore` excludes it. To override defaults, first preserve
any existing `.env`. Only when no `.env` exists, create one from the Docker
example:

```sh
cp .env.docker.example .env
chmod 600 .env
```

`.env.docker.example` documents these Docker-oriented settings:

- `CONTEXTBUBBLE_TOKEN`: optional fixed admin token; blank generates one.
- `ASR_PROVIDER`: currently `whisper_cpp`; this keeps provider selection explicit
  without adding optional ASR services to the default image.
- `WHISPER_CPP_REF`: whisper.cpp version used when building the image.
- `DOCKER_WHISPER_MODEL`, `DOCKER_WHISPER_MODEL_URL`,
  `DOCKER_WHISPER_MODEL_SHA256`, and `DOCKER_WHISPER_LANGUAGE`: container model
  path, pinned download, integrity hash, and language.
- `AGENT_MODE`: `heuristic`, `gemini`, or `ollama`.
- `GEMINI_API_KEY` and `GEMINI_MODEL`: Gemini credentials and model selection.
- `DOCKER_OLLAMA_BASE_URL` and `OLLAMA_MODEL`: Ollama endpoint and model. The
  default `http://host.docker.internal:11434` reaches Ollama on the host.
- `TRANSLATION_MODE` and `TRANSLATION_MODEL`: provider and model used by the
  translation API.
- `TRANSCRIPT_BLOCK_SPLITTER_MODE` and `TRANSCRIPT_BLOCK_SPLITTER_MODEL`:
  provider and model used for semantic transcript block splitting.
- `DEMO_VIDEO_IDS`: optional comma-separated fixture video IDs.

This Compose service is CPU-only: it fixes `WHISPER_NO_GPU=1`, always forwards
whisper.cpp's `-ng` flag, and does not offer a `.env` override. It also enables
startup ASR validation, so the container fails fast when `yt-dlp`, `ffmpeg`,
`ffprobe`, whisper.cpp, or the model is unavailable, before the backend starts
serving or resumes jobs.

The default Whisper model is English-only. For multilingual transcription, set
all four Docker model override values as one coherent tuple:
`DOCKER_WHISPER_MODEL`, `DOCKER_WHISPER_MODEL_URL`,
`DOCKER_WHISPER_MODEL_SHA256`, and `DOCKER_WHISPER_LANGUAGE`. The commented
example in `.env.docker.example` selects the multilingual base model with
`DOCKER_WHISPER_LANGUAGE=zh`; use `DOCKER_WHISPER_LANGUAGE=auto` with the same
multilingual model tuple for automatic language detection. Do not combine the
English-only model URL or SHA with a multilingual model path.

## State And Restarts

Compose keeps state in two named volumes:

- `contextbubble-data` contains the SQLite database and its
  `contextbubble.sqlite3-wal`/`contextbubble.sqlite3-shm` files, `jobs.log`,
  retained ASR resume media under `/data/media`, the generated
  `contextbubble.token`, and persisted session hashes in the database.
- `contextbubble-models` contains the downloaded Whisper model.

After a restart, the generated admin token is reused and an unexpired browser
session remains valid. Each backend restart still prints a fresh pairing code.
`docker compose stop`, `docker compose start`, `docker compose restart`,
`docker compose down`, and `docker compose up` all preserve named volumes. A
later `docker compose up` therefore reuses backend data and the downloaded
model.

**Destructive:** `docker compose down -v` deletes both named volumes, including
analyses, transcripts, logs, ASR resume files, the generated token, the session
database, and the model. The next start requires the model download and browser
pairing again.

YouTube caption files are temporary under `/tmp`. Interrupted queued or
processing jobs resume after a backend restart. Failed and interrupted jobs may
retain `/data/media/<job-id>` for diagnosis and resume inputs; failed jobs do
not automatically retry. Only successful ASR work removes its media, after the
transcript merge and before downstream analysis. A later analysis-stage failure
does not restore media that ASR already removed.

## Validation

Validate both the default and example Compose configurations with:

```sh
scripts/check-compose.sh
```

This validation requires Docker Compose and a POSIX shell. On Windows, run the
script from Git Bash or WSL. Its `/dev/null` default-config check is not portable
to PowerShell or CMD. As a plain cross-platform fallback, validate defaults in a
checkout where no repo-root `.env` exists, then validate the example:

```sh
docker compose config --quiet
docker compose --env-file .env.docker.example config --quiet
```

These commands check configuration rendering. They do not run the image or
external YouTube/browser smoke tests. Docker image build, backend startup, model
download, and end-to-end behavior remain separate runtime verification steps.
