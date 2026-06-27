# ContextBubble Extension

Minimal Chromium extension skeleton for the first vertical slice.

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
6. Click **Analyze Video** in the extension popup.
7. Play past 5 seconds.

Expected result: one backend-provided ContextBubble appears near the lower-right of the page.
