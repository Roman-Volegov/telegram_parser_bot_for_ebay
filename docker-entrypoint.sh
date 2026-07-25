#!/bin/sh
set -eu

DISPLAY_NUMBER="${DISPLAY:-:99}"
SCREEN_SIZE="${ETSY_SCREEN_SIZE:-1366x900x24}"

Xvfb "$DISPLAY_NUMBER" -screen 0 "$SCREEN_SIZE" -nolisten tcp &

mkdir -p /app/data/vnc
x11vnc \
    -display "$DISPLAY_NUMBER" \
    -nopw \
    -forever \
    -shared \
    -rfbport 5900 \
    -localhost \
    -o /app/data/vnc/x11vnc.log &
websockify \
    --web /usr/share/novnc \
    0.0.0.0:6080 \
    localhost:5900 &

exec "$@"
