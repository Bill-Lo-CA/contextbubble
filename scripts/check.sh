#!/usr/bin/env sh
set -eu
export CONTEXTBUBBLE_SKIP_DOTENV=1

if [ -x ".venv/bin/python" ]; then
  PYTHON=".venv/bin/python"
elif [ -n "${PYTHON:-}" ]; then
  :
elif command -v python >/dev/null 2>&1; then
  PYTHON="python"
else
  PYTHON="python3"
fi

"$PYTHON" -m unittest discover -s backend/tests -v
"$PYTHON" backend/server.py --check
node --check extension/backendClient.js
node --check extension/contentOverlay.js
node --check extension/content.js
node --check extension/popup.js
node --check extension/sidepanel.js
