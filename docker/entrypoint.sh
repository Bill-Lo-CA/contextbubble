#!/bin/sh
set -eu

data_dir=${CONTEXTBUBBLE_DATA_DIR:-/data}
media_dir=${CONTEXTBUBBLE_MEDIA_DIR:-/data/media}
models_dir=${CONTEXTBUBBLE_MODELS_DIR:-/models}
tmp_dir=${CONTEXTBUBBLE_TMP_DIR:-/tmp/contextbubble}

if [ "$(id -u)" -eq 0 ]; then
    install -d -o contextbubble -g contextbubble -m 0750 \
        "$data_dir" "$media_dir" "$models_dir" "$tmp_dir"
    chown -R contextbubble:contextbubble "$data_dir" "$models_dir" "$tmp_dir"
    exec gosu contextbubble "$0" "$@"
fi

for directory in "$data_dir" "$models_dir" "$tmp_dir"; do
    probe="$directory/.contextbubble-write-test.$$"
    if [ ! -d "$directory" ] || ! (umask 077; : > "$probe") 2>/dev/null; then
        echo "error: $directory is not writable by contextbubble" >&2
        exit 1
    fi
    if ! rm -f "$probe"; then
        echo "error: cannot remove write probe from $directory" >&2
        exit 1
    fi
done

CONTEXTBUBBLE_BOOTSTRAP=${CONTEXTBUBBLE_BOOTSTRAP:-/usr/local/bin/contextbubble-bootstrap-model}
exec "$CONTEXTBUBBLE_BOOTSTRAP" "$@"
