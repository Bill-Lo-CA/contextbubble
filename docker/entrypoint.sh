#!/bin/sh
set -eu

if [ "$(id -u)" -eq 0 ]; then
    install -d -o contextbubble -g contextbubble -m 0750 \
        /data /data/media /models /tmp/contextbubble
    chown -R contextbubble:contextbubble /data /models /tmp/contextbubble
    exec gosu contextbubble "$0" "$@"
fi

for directory in /data /models /tmp/contextbubble; do
    if [ ! -w "$directory" ]; then
        echo "error: $directory is not writable by contextbubble" >&2
        exit 1
    fi
done

/usr/local/bin/contextbubble-bootstrap-model
exec "$@"
