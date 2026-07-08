# ContextBubble

ContextBubble is a local-first Chromium extension prototype for showing
timestamped learning notes on YouTube videos.

The extension detects the active YouTube video, starts or reuses a persistent
backend preparation job, polls progress until the video is ready, and shows
reviewed timestamped bubbles on the page. The local backend uses YouTube
captions when available, can fall back to whisper.cpp ASR, persists runtime
state in SQLite, and supports concept, review, and translation workflows.

ContextBubble is intended for localhost use. The local pairing/token model is
prototype auth, not production authentication.

## Quick Start

Docker Compose is the supported runtime path for normal local use:

```sh
docker compose up --build
```

In another terminal, read the short pairing code:

```sh
docker compose logs backend
```

No `.env` file is needed for the default Docker setup. If you need Docker
overrides and no private `.env` exists yet:

```sh
cp .env.docker.example .env
chmod 600 .env
```

Then edit only the values you need. See [Docker Setup](docs/setup-docker.md) for
state, model, `.env`, validation, and restart details.

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
generation, review, and ready state. Use **Demo mode** only for an explicit
fixture-backed demo. Use **Re-analyze** to force a fresh preparation.

## Validate

```sh
scripts/check.sh
```

`scripts/check.sh` runs the unit and contract tests, backend self-check, and
extension JavaScript syntax checks without requiring `yt-dlp`, `ffmpeg`,
Whisper, Gemini, or Ollama.

For Docker Compose config validation:

```sh
scripts/check-compose.sh
```

## Documentation

- [Docker Setup](docs/setup-docker.md): official local runtime path.
- [Native Developer Setup](docs/setup-native.md): developer-only host setup.
- [Demo](docs/demo.md): repeatable fixture-backed demo flow.
- [Architecture](docs/architecture.md): backend and extension module map.
- [Limits](docs/limits.md): local prototype limits and security notes.
