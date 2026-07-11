#!/usr/bin/env sh
set -eu
export CONTEXTBUBBLE_SKIP_DOTENV=1

uv run --locked ruff check backend
uv run --locked python -m unittest discover -s backend/tests -v
uv run --locked python backend/server.py --check
node --check extension/backendClient.js
node --check extension/contentOverlay.js
node --check extension/contentOwnerState.js
node --check extension/contentStorage.js
node --check extension/contentTranslations.js
node --check extension/contentPreparation.js
node --check extension/contentTimeline.js
node --check extension/content.js
node --check extension/popup.js
node --check extension/sidepanel.js
node -e 'JSON.parse(require("fs").readFileSync("extension/manifest.json", "utf8"))'
