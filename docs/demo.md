# Demo

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

Run the backend in deterministic mode:

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
