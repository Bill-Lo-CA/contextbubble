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

## Requirements

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
AGENT_MODE="heuristic"
DEMO_VIDEO_IDS=""
```

If those paths exist, the backend uses them automatically. Override them only
when your tools live elsewhere. By default, whisper.cpp is allowed to use GPU
when the binary was built with GPU support. Set `WHISPER_NO_GPU=1` to force CPU
mode. Agent analysis defaults to the local heuristic mode for testing. Set
`AGENT_MODE=gemini` and provide `GEMINI_API_KEY` to use the Gemini Concept Agent
and Gemini Reviewer Agent. Demo fixtures are only used when Demo mode is checked
in the popup or when the current video ID is listed in `DEMO_VIDEO_IDS`.

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

## Run Backend

From the repo root:

```sh
python backend/server.py
```

The backend listens on `127.0.0.1` and prints a bearer token:

```text
http://127.0.0.1:8000
```

## Load Extension

1. Open `chrome://extensions` or `brave://extensions`.
2. Enable Developer mode.
3. Click **Load unpacked**.
4. Select the `extension/` directory.
5. Open a YouTube watch page.
6. Paste the backend API token into the popup.
7. Pick a learner level.
8. Click **Analyze Video**.

The extension starts or resumes a backend preparation job and shows stage
progress such as caption checks, audio download, transcription, merge, concept
generation, review, and ready state. Bubbles display only inside a short timing
window near their timestamp; skipped old bubbles are not replayed after seeking.
If YouTube captions and ASR fallback fail, the extension reports the error
instead of silently using the demo fixture. Use **Demo mode** only for an
explicit fixture-backed demo. Use **Re-analyze** to force a fresh preparation.

## Validate

```sh
python backend/server.py --check
node --check extension/content.js
node --check extension/popup.js
node --check extension/sidepanel.js
```

## Current Limits

- YouTube caption fetch depends on `yt-dlp` and caption availability.
- Whole-video ASR fallback downloads full audio once, then processes local overlapping chunks.
- Only one ASR preparation runs at a time in the local backend process.
- The stored demo transcript fixture is only available through explicit Demo mode or the demo video allowlist.
- Preparation jobs, chunks, transcripts, analyses, and bubbles persist in `backend/.contextbubble/contextbubble.sqlite3`.
- External-tool failures are logged to `backend/.contextbubble/jobs.log` with bounded stderr tails.
- The agent workflow defaults to `AGENT_MODE=heuristic`; set `AGENT_MODE=gemini` to use Gemini.
- The extension does not download media directly; backend `yt-dlp` does.
- YouTube download behavior depends on `yt-dlp` staying current.
