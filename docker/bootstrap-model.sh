#!/bin/sh
set -eu

: "${WHISPER_MODEL:?WHISPER_MODEL is required}"
: "${WHISPER_MODEL_URL:?WHISPER_MODEL_URL is required}"
: "${WHISPER_MODEL_SHA256:?WHISPER_MODEL_SHA256 is required}"

case "$WHISPER_MODEL" in
    /models/*|/*/models/*) ;;
    *)
        echo "error: WHISPER_MODEL must be inside a models directory" >&2
        exit 1
        ;;
esac

case "/$WHISPER_MODEL/" in
    */../*|*/./*)
        echo "error: WHISPER_MODEL must not contain relative path components" >&2
        exit 1
        ;;
esac

model_dir=${WHISPER_MODEL%/*}
mkdir -p "$model_dir"

checksum_matches() {
    actual=$(sha256sum "$1")
    actual=${actual%% *}
    [ "$actual" = "$WHISPER_MODEL_SHA256" ]
}

if [ -f "$WHISPER_MODEL" ] && checksum_matches "$WHISPER_MODEL"; then
    echo "model already valid: $WHISPER_MODEL"
    if [ "$#" -gt 0 ]; then
        exec "$@"
    fi
    exit 0
fi

partial=
curl_pid=
cleanup() {
    if [ -n "$partial" ]; then
        rm -f "$partial"
    fi
}

forward_signal() {
    signal_name=$1
    exit_status=$2
    trap - HUP INT TERM
    if [ -n "$curl_pid" ]; then
        kill "-$signal_name" "$curl_pid" 2>/dev/null || :
        wait "$curl_pid" 2>/dev/null || :
        curl_pid=
    fi
    cleanup
    trap - 0
    exit "$exit_status"
}

trap cleanup 0
trap 'forward_signal HUP 129' HUP
trap 'forward_signal INT 130' INT
trap 'forward_signal TERM 143' TERM

partial=$(mktemp "${WHISPER_MODEL}.partial.XXXXXX")

curl --fail --location --retry 3 --retry-delay 2 \
    --connect-timeout 30 --max-time 3600 \
    --output "$partial" "$WHISPER_MODEL_URL" &
curl_pid=$!
curl_status=0
wait "$curl_pid" || curl_status=$?
curl_pid=
if [ "$curl_status" -ne 0 ]; then
    exit "$curl_status"
fi

if ! checksum_matches "$partial"; then
    echo "error: model checksum mismatch" >&2
    exit 1
fi

mv -f "$partial" "$WHISPER_MODEL"
partial=
trap - 0 HUP INT TERM
echo "model downloaded: $WHISPER_MODEL"

if [ "$#" -gt 0 ]; then
    exec "$@"
fi
