# Limits

ContextBubble is a local-first prototype. The backend is intended to bind to
localhost. The local pairing/token model is not production authentication.

- YouTube caption fetch depends on `yt-dlp` and caption availability.
- Whole-video ASR fallback downloads full audio once, then processes local
  overlapping chunks.
- Pairing codes are one-use and expire after five minutes; use **Resend Code**
  in the popup to print a fresh code in the backend terminal.
- Only one ASR preparation runs at a time in the local backend process.
- The stored demo transcript fixture is only available through explicit Demo
  mode or the demo video allowlist.
- Preparation jobs, chunks, transcripts, analyses, and bubbles persist under
  `CONTEXTBUBBLE_DATA_DIR`, defaulting to
  `backend/.contextbubble/contextbubble.sqlite3`.
- The extension stores only a paired session token in `chrome.storage.session`,
  not the admin token in `chrome.storage.local`.
- Transcript, caption, and popup status state is scoped by YouTube video in
  extension local storage.
- External-tool failures are logged to `backend/.contextbubble/jobs.log` with
  bounded stderr tails.
- The bubble workflow defaults to `AGENT_MODE=heuristic`; set
  `AGENT_MODE=gemini` to use Gemini or `AGENT_MODE=ollama` to use Ollama.
- Translation defaults to `TRANSLATION_MODE=ollama` with
  `TRANSLATION_MODEL=qwen3:8b`.
- The Side Panel shows prepared sentence cards after analysis is ready and falls
  back to live caption debug text before then.
- The extension does not download media directly; backend `yt-dlp` does.
- YouTube download behavior depends on `yt-dlp` staying current.
