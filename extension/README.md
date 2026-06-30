# ContextBubble Extension

Minimal Chromium extension skeleton for the first vertical slice.

The product direction is extension-first: the browser extension provides the YouTube overlay and user controls, while backend/API processing handles transcripts, ASR, concept detection, explanation generation, and review.

The extension should not automatically download YouTube media. Subtitle or audio processing should use user-uploaded files, stored fixtures, or legally/platform-safe transcript sources.

## Start Backend

```sh
python backend/server.py
```

## Load Locally

1. Open `chrome://extensions` or `brave://extensions`.
2. Enable Developer mode.
3. Click **Load unpacked**.
4. Select this `extension/` directory.
5. Open a YouTube watch page.
6. Choose a learner level.
7. Click **Analyze Video**.
8. Play to a returned bubble timestamp.

Expected result: one backend-provided ContextBubble appears near the lower-right of the page.

## Current Limits

- The live caption panel reads visible `.ytp-caption-segment` text only as a debug preview.
- The content script sends the current playback time to the backend.
- The backend uses `yt-dlp` to download the current 60-second YouTube audio chunk, then runs whisper.cpp to produce timestamped subtitles.
- The backend API currently stores transcripts and analyses in memory.
- Heavy ASR work belongs in the backend, not the extension.
