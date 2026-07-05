# ContextBubble

ContextBubble is a Chromium extension prototype for showing timestamped learning
notes on YouTube videos.

The current prototype detects the active YouTube video, starts or reuses a
persistent preparation job, polls progress until the video is ready, and shows
reviewed timestamped bubbles on the page. The backend uses complete YouTube
captions when available. If captions are unavailable, it downloads audio once,
normalizes it once, transcribes overlapping local chunks through whisper.cpp,
merges the transcript, then runs concept generation and review. Captions are
logged in the Chrome Side Panel.

## Run Backend with Docker

Docker is the recommended backend setup when the host does not already have
Python, `ffmpeg`, `yt-dlp`, and whisper.cpp. On macOS and Windows, configure
Docker Desktop to use Linux containers. From the repo root, run:

```sh
docker compose up --build
```

This command runs attached. In another terminal, run the following command to
read the admin token and short pairing code from the backend output:

```sh
docker compose logs backend
```

The first startup downloads the default English-only `ggml-base.en.bin` model
into the `contextbubble-models` volume. Later starts reuse that download.

The API is available only at `http://127.0.0.1:8000`; the Compose port binding
does not expose it to the LAN.

No `.env` file is needed because Compose supplies defaults. Compose reads a
repo-root `.env` only for variable interpolation; it is not copied into the
image because `.dockerignore` excludes it. To override defaults, first preserve
any existing `.env`. Only when no `.env` exists, create one from the example:

```sh
cp .env.example .env
```

Then edit only the values that need overrides. `.env.example` documents these
settings:

- `CONTEXTBUBBLE_TOKEN`: optional fixed admin token; blank generates one.
- `WHISPER_MODEL`, `WHISPER_MODEL_URL`, `WHISPER_MODEL_SHA256`, and
  `WHISPER_LANGUAGE`: model path, pinned download, integrity hash, and language.
- `WHISPER_NO_GPU`: defaults to `1`. The image is CPU-only, and the backend
  forwards whisper.cpp's `-ng` flag when this setting is enabled.
- `AGENT_MODE`: `heuristic` (the no-provider default), `gemini`, or `ollama`.
- `GEMINI_API_KEY` and `GEMINI_MODEL`: Gemini credentials and model selection.
- `OLLAMA_BASE_URL` and `OLLAMA_MODEL`: Ollama endpoint and model. The default
  `http://host.docker.internal:11434` reaches Ollama on the host; Compose adds
  the Linux host-gateway mapping while Docker Desktop provides the same name.
- `DEMO_VIDEO_IDS`: optional comma-separated fixture video IDs.

The default Whisper model is English-only. For multilingual transcription, set
all four model values as one coherent tuple: `WHISPER_MODEL`,
`WHISPER_MODEL_URL`, `WHISPER_MODEL_SHA256`, and `WHISPER_LANGUAGE`. The
commented example in `.env.example` selects the multilingual base model with
`WHISPER_LANGUAGE=zh`; use `WHISPER_LANGUAGE=auto` with the same multilingual
model tuple for automatic language detection. Do not combine the English-only
model URL or SHA with a multilingual model path.

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

Validate both the default and example Compose configurations with:

```sh
scripts/check-compose.sh
```

This validation requires Docker Compose and a POSIX shell. On Windows, run the
script from Git Bash or WSL. Its `/dev/null` default-config check is not portable
to PowerShell or CMD. As a plain cross-platform fallback, validate defaults in a
checkout where no repo-root `.env` exists (do not delete an existing `.env`),
then validate the example:

```sh
docker compose config --quiet
docker compose --env-file .env.example config --quiet
```

These commands check configuration rendering. They do not run the image or
external YouTube/browser smoke tests. Docker image build, backend startup, model
download, and end-to-end behavior remain separate runtime verification steps.

## Native Requirements

- Python 3
- Chromium, Chrome, or Brave
- Node.js, only for `node --check` validation
- `ffmpeg`
- A recent `yt-dlp`
- whisper.cpp
- A whisper.cpp model file

The backend defaults to tools under your home directory:

```sh
YTDLP_CMD="$HOME/.local/bin/yt-dlp"
FFMPEG_CMD="ffmpeg"
FFPROBE_CMD="ffprobe"
WHISPER_CMD="$HOME/tools/whisper.cpp/build/bin/whisper-cli"
WHISPER_MODEL="$HOME/tools/whisper.cpp/models/ggml-base.en.bin"
WHISPER_NO_GPU=0
GEMINI_API_KEY=""
GEMINI_MODEL="gemini-2.5-flash"
OLLAMA_BASE_URL="http://127.0.0.1:11434"
OLLAMA_MODEL="qwen3:8b"
AGENT_MODE="heuristic"
TRANSLATION_MODE="ollama"
TRANSLATION_MODEL="qwen3:8b"
DEMO_VIDEO_IDS=""
```

If those paths exist, the backend uses them automatically. Override them only
when your tools live elsewhere. By default, whisper.cpp is allowed to use GPU
when the binary was built with GPU support. Set `WHISPER_NO_GPU=1` to force CPU
mode. Agent analysis defaults to the local heuristic mode for testing. Set
`AGENT_MODE=gemini` and provide `GEMINI_API_KEY` to use Gemini, or set
`AGENT_MODE=ollama` to use a local Ollama model. Translation is configured
separately and defaults to local Ollama with `qwen3:8b`, so the bubble workflow
can stay deterministic while English-to-Traditional-Chinese translation uses a
real multilingual model. Demo fixtures are only used when Demo mode is checked
in the popup or when the current video ID is listed in `DEMO_VIDEO_IDS`.

For file-based local config, copy the example file and keep the real file
private:

```sh
cp .env.example .env
chmod 600 .env
```

The backend loads `.env` from the repository root and expands paths like
`${HOME}/.local/share/contextbubble`. Values already exported in the shell win
over `.env`. Runtime state goes under `CONTEXTBUBBLE_DATA_DIR`; that directory
is kept at mode `0700`, and token/environment files are kept at `0600`.

## Repeatable Final-Project Demo

Use this path for recording when live YouTube captions, ASR speed, or model
availability should not decide whether the demo works.

Selected demo video:

```text
https://www.youtube.com/watch?v=fNk_zzaMoSs
```

The matching stable transcript fixture is:

```text
backend/fixtures/fNk_zzaMoSs.vtt
```

Run the backend in local deterministic mode:

```sh
AGENT_MODE=heuristic TRANSLATION_MODE=ollama TRANSLATION_MODEL=qwen3:8b python backend/server.py
```

Then load the extension, pair with the printed code, open the selected YouTube
URL, enable **Demo mode**, pick a learner level, and click **Analyze Video**.
Demo mode uses the bundled transcript fixture for that video and still runs the
normal preparation, Concept Agent, Reviewer Agent, validator, cache, bubbles,
and Side Panel path.

For a Gemini-backed recording, use the same fixture path but start the backend
with `GEMINI_API_KEY` set in `.env` or in the shell:

```sh
AGENT_MODE=gemini GEMINI_API_KEY="..." python backend/server.py
```

The fixture keeps the recorded demo independent from live caption availability,
`yt-dlp`, and whisper.cpp runtime. Live caption retrieval and ASR fallback remain
available when Demo mode is off.

## Install Tools

Install or update `yt-dlp` from the release binary:

```sh
mkdir -p "$HOME/.local/bin"
curl -L https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp \
  -o "$HOME/.local/bin/yt-dlp"
chmod a+rx "$HOME/.local/bin/yt-dlp"
"$HOME/.local/bin/yt-dlp" --version
```

Install `ffmpeg` with your system package manager if it is missing:

```sh
ffmpeg -version
```

Build whisper.cpp and download a model. For example, this repo expects a working
binary and model at:

```text
$HOME/tools/whisper.cpp/build/bin/whisper-cli
$HOME/tools/whisper.cpp/models/ggml-base.en.bin
```

## Run Backend Natively

From the repo root:

```sh
python -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements.txt
python backend/server.py
```

The backend listens on `127.0.0.1` and prints a short pairing code. A generated
admin bearer token is stored privately at
`backend/.contextbubble/contextbubble.token` instead of being written to logs:

```text
http://127.0.0.1:8000
```

## Load Extension

1. Open `chrome://extensions` or `brave://extensions`.
2. Enable Developer mode.
3. Click **Load unpacked**.
4. Select the `extension/` directory.
5. Open a YouTube watch page.
6. Enter the backend pairing code in the popup and click **Pair Backend**.
7. Optionally click **Check Backend** to validate the paired session.
8. Pick a learner level.
9. Click **Analyze Video**.

The extension starts or resumes a backend preparation job and shows stage
progress such as caption checks, audio download, transcription, merge, concept
generation, review, and ready state. Bubbles display only inside a short timing
window near their timestamp; skipped old bubbles are not replayed after seeking.
If YouTube captions and ASR fallback fail, the extension reports the error
instead of silently using the demo fixture. Use **Demo mode** only for an
explicit fixture-backed demo. Use **Re-analyze** to force a fresh preparation.

## Validate

```sh
scripts/check.sh
```

`scripts/check.sh` runs the backend self-check and extension JavaScript syntax
checks without requiring `yt-dlp`, `ffmpeg`, Whisper, Gemini, or Ollama.

## Implementation Notes

Backend code is split by responsibility across small modules:
`server.py`, `config.py`, `auth.py`, `db.py`, `transcripts.py`, `media.py`,
`agents.py`, `jobs.py`, `checks.py`, and `providers.py`. The extension keeps
shared backend fetch handling in `extension/backendClient.js` and bubble
rendering in `extension/contentOverlay.js`.

## Local Auth Model

The backend is a local-dev service bound to `127.0.0.1`. It loads the admin
bearer token from `CONTEXTBUBBLE_TOKEN` or a private generated token file, and
does not print that token. It prints a one-use pairing code to the terminal at
startup. The popup uses six single-digit inputs for the pairing code. If the
code expires, the popup can request a new code; the backend prints that code to
the terminal and does not return it to the browser. The extension stores only
the resulting session token in `chrome.storage.session`.

This is prototype local auth, not production auth. CORS is intentionally narrow:
pairing is for extension origins, and protected routes still require an admin or
session bearer token. Local non-browser processes are not constrained by CORS and
must still know a valid token for protected routes.

## Current Limits

- YouTube caption fetch depends on `yt-dlp` and caption availability.
- Whole-video ASR fallback downloads full audio once, then processes local overlapping chunks.
- Pairing codes are one-use and expire after five minutes; use **Resend Code** in the popup to print a fresh code in the backend terminal.
- Only one ASR preparation runs at a time in the local backend process.
- The stored demo transcript fixture is only available through explicit Demo mode or the demo video allowlist.
- Preparation jobs, chunks, transcripts, analyses, and bubbles persist under `CONTEXTBUBBLE_DATA_DIR`, defaulting to `backend/.contextbubble/contextbubble.sqlite3`.
- The extension stores only a paired session token in `chrome.storage.session`, not the admin token in `chrome.storage.local`.
- Transcript, caption, and popup status state is scoped by YouTube video in extension local storage.
- External-tool failures are logged to `backend/.contextbubble/jobs.log` with bounded stderr tails.
- The bubble workflow defaults to `AGENT_MODE=heuristic`; set `AGENT_MODE=gemini` to use Gemini or `AGENT_MODE=ollama` to use Ollama.
- Translation defaults to `TRANSLATION_MODE=ollama` with `TRANSLATION_MODEL=qwen3:8b`.
- The Side Panel shows prepared sentence cards after analysis is ready and falls back to live caption debug text before then.
- The extension does not download media directly; backend `yt-dlp` does.
- YouTube download behavior depends on `yt-dlp` staying current.
