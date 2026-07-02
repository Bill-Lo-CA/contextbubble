# ContextBubble

ContextBubble is a Chromium extension prototype for showing timestamped learning
notes on YouTube videos.

The current prototype detects the active YouTube video, sends the current
playback time to a local backend, downloads the matching 30-second audio chunk
with `yt-dlp`, transcribes it with whisper.cpp, and shows timestamped subtitles
and placeholder bubbles on the page.

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
WHISPER_CMD="$HOME/tools/whisper.cpp/build/bin/whisper-cli"
WHISPER_MODEL="$HOME/tools/whisper.cpp/models/ggml-base.en.bin"
```

If those paths exist, the backend uses them automatically. Override them only
when your tools live elsewhere.

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

The backend listens on:

```text
http://127.0.0.1:8000
```

## Load Extension

1. Open `chrome://extensions` or `brave://extensions`.
2. Enable Developer mode.
3. Click **Load unpacked**.
4. Select the `extension/` directory.
5. Open a YouTube watch page.
6. Choose a learner level.
7. Click **Analyze Video**.

The extension asks the backend to process the 30-second chunk around the current
playback time, then immediately processes the next 30-second chunk. The backend
offsets chunk-local transcript timestamps back into the full video timeline.
When the result returns, the extension reads the current `video.currentTime`
again and displays the matching absolute-timestamp subtitle, so playback can
stay synchronized even if processing takes a while.

## Validate

```sh
python backend/server.py --check
node --check extension/content.js
node --check extension/popup.js
```

## Current Limits

- Processes only the current 30-second chunk and one follow-up chunk.
- No background queue or prefetch yet.
- Transcript and analysis cache are in memory.
- Bubble content is placeholder text derived from transcript segments.
- The extension does not download media directly; backend `yt-dlp` does.
- YouTube download behavior depends on `yt-dlp` staying current.
