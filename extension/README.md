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
6. Paste the backend API token into the popup.
7. Click **Analyze Video**.
8. Play to a returned bubble timestamp.

Expected result: reviewed ContextBubble bubbles appear near their configured timestamps. Captions appear in the Chrome Side Panel when **Open Captions** is clicked.

## Current Limits

- The Side Panel caption log reads visible `.ytp-caption-segment` text only as a debug preview until transcript segments are available.
- The extension starts an analysis job and polls until it completes.
- The backend can use `yt-dlp` and whisper.cpp for the current 30-second YouTube audio chunk as a prototype fallback.
- The backend persists analysis cache to a local JSON file.
- Heavy ASR work belongs in the backend, not the extension.
