# Native Developer Setup

Native mode is intended for backend development, debugging, and running tests
without Docker. For normal local use, use Docker Compose.

## Requirements

- Python 3.12
- [uv](https://docs.astral.sh/uv/)
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
mode.

## Environment

For file-based local config, copy the native example file and keep the real file
private:

```sh
cp .env.example .env
chmod 600 .env
```

The backend loads `.env` from the repository root and expands paths like
`${HOME}/.local/share/contextbubble`. Values already exported in the shell win
over `.env`. Runtime state goes under `CONTEXTBUBBLE_DATA_DIR`; that directory
is kept at mode `0700`, and token/environment files are kept at `0600`.

`uv` manages the project virtual environment automatically:

| Use case | `uv` needed? |
|---|---:|
| Normal Docker runtime | No |
| Native backend runtime | Yes |
| Local tests/checks | Yes |
| VS Code autocomplete/debugging | Recommended |
| Formal user install | No |

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

Build whisper.cpp and download a model. This repo expects a working binary and
model at:

```text
$HOME/tools/whisper.cpp/build/bin/whisper-cli
$HOME/tools/whisper.cpp/models/ggml-base.en.bin
```

## Run Natively

From the repo root:

```sh
uv sync --locked
uv run python backend/server.py
```

The backend listens on `127.0.0.1` and prints a short pairing code. A generated
admin bearer token is stored privately at
`backend/.contextbubble/contextbubble.token` instead of being written to logs.

Native startup uses lazy ASR validation by default, so caption-only work can
run without the ASR toolchain. Missing ASR tools are validated on demand when
fallback transcription is needed. Set `CONTEXTBUBBLE_VALIDATE_ASR_ON_START=1`
to opt into the same fail-fast startup behavior as the container.
