#!/usr/bin/env sh
set -eu

python backend/server.py --check
node --check extension/backendClient.js
node --check extension/contentOverlay.js
node --check extension/content.js
node --check extension/popup.js
node --check extension/sidepanel.js
