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
    exit 0
fi

partial=$(mktemp "${WHISPER_MODEL}.partial.XXXXXX")
cleanup() {
    rm -f "$partial"
}
trap cleanup 0 HUP INT TERM

curl --fail --location --retry 3 --retry-delay 2 \
    --output "$partial" "$WHISPER_MODEL_URL"

if ! checksum_matches "$partial"; then
    echo "error: model checksum mismatch" >&2
    exit 1
fi

mv -f "$partial" "$WHISPER_MODEL"
partial=
trap - 0 HUP INT TERM
echo "model downloaded: $WHISPER_MODEL"
