#!/usr/bin/env sh
set -eu

PYTHON="${PYTHON:-python}"
if [ -x ".venv/bin/python" ]; then
  PYTHON=".venv/bin/python"
fi

"$PYTHON" backend/server.py --check
node --check extension/backendClient.js
node --check extension/contentOverlay.js
node --check extension/content.js
node --check extension/popup.js
node --check extension/sidepanel.js
