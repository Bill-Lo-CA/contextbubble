# ContextBubble Extension

Minimal Chromium extension skeleton for the first vertical slice.

The product direction is extension-first: the browser extension provides the YouTube overlay and user controls, while backend/API processing handles transcripts, ASR, concept detection, explanation generation, and review.

The extension should not download YouTube media directly. In local-dev mode, the backend may use `yt-dlp` for captions or ASR audio fallback; lawful and platform-safe use is the user's responsibility.

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
6. Enter the backend pairing code and click **Pair Backend**.
7. Optionally click **Check Backend** to validate the paired session.
8. Optionally enable **Demo mode** for the local fixture.
9. Pick a learner level.
10. Click **Analyze Video**.
11. Play to a returned bubble timestamp.

Expected result: reviewed ContextBubble bubbles appear near their configured timestamps. Captions appear in the Chrome Side Panel when **Open Captions** is clicked.

## Current Limits

- The Side Panel shows prepared sentence cards after analysis is ready and uses visible `.ytp-caption-segment` text only as a pre-ready debug fallback.
- The extension starts or resumes a persistent backend preparation job and polls stage progress until ready.
- The extension stores paired backend sessions in `chrome.storage.session`, not the admin token in `chrome.storage.local`.
- The backend tries `yt-dlp` YouTube captions first, then falls back to whole-video whisper.cpp chunks from one downloaded audio file.
- Demo fixture fallback is explicit only; arbitrary videos do not silently receive the demo transcript.
- Bubbles render in safe slots inside the YouTube video player, with at most two visible at once.
- The backend persists preparation jobs, chunks, transcripts, analyses, and bubbles in local SQLite.
- Heavy ASR work belongs in the backend, not the extension.
