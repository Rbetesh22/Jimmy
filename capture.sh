#!/bin/bash
# ~/jimmy/capture.sh
# Lightweight capture to Jimmy from the command line or a macOS Quick Action.
#
# Usage:
#   ./capture.sh                        # captures URL from clipboard
#   ./capture.sh https://example.com    # captures a specific URL
#   ./capture.sh --text "some text"     # captures raw text
#   ./capture.sh --youtube URL          # captures a YouTube video transcript
#
# Set JIMMY_HOST to override the server address (default: http://localhost:7700).

JIMMY_HOST="${JIMMY_HOST:-http://localhost:7700}"

notify() {
    local msg="$1"
    osascript -e "display notification \"$msg\" with title \"Jimmy\"" 2>/dev/null || true
}

# ── Check server is up ─────────────────────────────────────────────────────────
if ! curl -s --max-time 3 "${JIMMY_HOST}/health" > /dev/null 2>&1; then
    echo "Error: Jimmy server is not running at ${JIMMY_HOST}" >&2
    echo "Start it with: cd ~/jimmy && jimmy serve" >&2
    notify "Jimmy server not running"
    exit 1
fi

if [[ "$1" == "--text" ]]; then
    # ── Text capture ──────────────────────────────────────────────────────────
    TEXT="${2:-$(pbpaste)}"
    if [[ -z "$TEXT" ]]; then
        echo "Error: no text provided and clipboard is empty." >&2
        exit 1
    fi
    RESULT=$(curl -s -X POST "${JIMMY_HOST}/ingest/text" \
        -H "Content-Type: application/json" \
        -d "{\"text\": $(echo "$TEXT" | python3 -c 'import json,sys; print(json.dumps(sys.stdin.read()))')}")
    if echo "$RESULT" | grep -q '"ok": *true'; then
        CHUNKS=$(echo "$RESULT" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('chunks', '?'))" 2>/dev/null || echo "?")
        notify "Text captured to Jimmy ($CHUNKS chunks)"
        echo "Captured: $CHUNKS chunk(s) stored."
    else
        ERROR=$(echo "$RESULT" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('detail', d))" 2>/dev/null || echo "$RESULT")
        notify "Capture failed: $ERROR"
        echo "Error: $ERROR" >&2
        exit 1
    fi

elif [[ "$1" == "--youtube" ]]; then
    # ── YouTube capture ───────────────────────────────────────────────────────
    URL="${2:-$(pbpaste)}"
    if [[ -z "$URL" ]]; then
        echo "Error: no URL provided and clipboard is empty." >&2
        exit 1
    fi
    if [[ ! "$URL" =~ ^https?:// ]]; then
        echo "Error: not a valid URL: $URL" >&2
        notify "Capture failed — not a URL"
        exit 1
    fi
    echo "Fetching YouTube transcript for: $URL"
    RESULT=$(curl -s --max-time 60 -X POST "${JIMMY_HOST}/ingest/youtube" \
        -H "Content-Type: application/json" \
        -d "{\"url\": \"$URL\"}")
    if echo "$RESULT" | grep -q '"ok": *true'; then
        CHUNKS=$(echo "$RESULT" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('chunks', '?'))" 2>/dev/null || echo "?")
        TITLE=$(echo "$RESULT" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('title', ''))" 2>/dev/null || echo "")
        notify "YouTube captured: $TITLE ($CHUNKS chunks)"
        echo "Captured: \"${TITLE}\" — $CHUNKS chunk(s) stored."
    else
        ERROR=$(echo "$RESULT" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('detail', d))" 2>/dev/null || echo "$RESULT")
        notify "YouTube capture failed"
        echo "Error: $ERROR" >&2
        exit 1
    fi

else
    # ── URL capture ───────────────────────────────────────────────────────────
    URL="${1:-$(pbpaste)}"
    if [[ -z "$URL" ]]; then
        echo "Error: no URL provided and clipboard is empty." >&2
        exit 1
    fi
    # Basic sanity check — must look like a URL
    if [[ ! "$URL" =~ ^https?:// ]]; then
        echo "Error: clipboard content does not look like a URL: $URL" >&2
        notify "Capture failed — not a URL"
        exit 1
    fi
    echo "Ingesting: $URL"
    RESULT=$(curl -s --max-time 30 -X POST "${JIMMY_HOST}/ingest/url" \
        -H "Content-Type: application/json" \
        -d "{\"url\": \"$URL\"}")
    if echo "$RESULT" | grep -q '"ok": *true'; then
        CHUNKS=$(echo "$RESULT" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('chunks', '?'))" 2>/dev/null || echo "?")
        TITLE=$(echo "$RESULT" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('title', ''))" 2>/dev/null || echo "")
        TAG=$(echo "$RESULT" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('tag', ''))" 2>/dev/null || echo "")
        TAG_DISPLAY="${TAG:+ [$TAG]}"
        notify "Captured to Jimmy: $TITLE ($CHUNKS chunks)$TAG_DISPLAY"
        echo "Captured: \"${TITLE}\"${TAG_DISPLAY} — $CHUNKS chunk(s) stored."
    else
        ERROR=$(echo "$RESULT" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('detail', d))" 2>/dev/null || echo "$RESULT")
        notify "Capture failed"
        echo "Error: $ERROR" >&2
        exit 1
    fi
fi
