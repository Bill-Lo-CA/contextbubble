#!/usr/bin/env sh
set -eu

script_dir=$(CDPATH= cd "$(dirname "$0")" && pwd)
cd "$script_dir/.."

if ! docker compose version >/dev/null 2>&1; then
  echo "error: docker compose is required to validate compose.yaml" >&2
  exit 1
fi

docker compose --env-file /dev/null config --quiet
docker compose --env-file .env.docker.example config --quiet
