#!/bin/sh
set -eu

DISPLAY_NUMBER="${DISPLAY:-:99}"
SCREEN_SIZE="${ETSY_SCREEN_SIZE:-1368x900x24}"
PROFILE_DIR="${ETSY_BROWSER_PROFILE_DIR:-/app/data/etsy-browser-profile}"

rm -rf \
    "$PROFILE_DIR/Default/Cache" \
    "$PROFILE_DIR/Default/Code Cache" \
    "$PROFILE_DIR/Default/GPUCache" \
    "$PROFILE_DIR/Crashpad"
Xvfb "$DISPLAY_NUMBER" -screen 0 "$SCREEN_SIZE" -nolisten tcp &

mkdir -p /app/data/vnc
x11vnc \
    -display "$DISPLAY_NUMBER" \
    -nopw \
    -quiet \
    -forever \
    -shared \
    -rfbport 5900 \
    -localhost &
websockify \
    --web /usr/share/novnc \
    0.0.0.0:6080 \
    localhost:5900 &

exec "$@"
