#!/bin/sh
set -eu

DISPLAY_NUMBER="${DISPLAY:-:99}"
DISPLAY_ID="${DISPLAY_NUMBER#:}"
SCREEN_SIZE="${ETSY_SCREEN_SIZE:-1368x900x24}"
PROFILE_DIR="${ETSY_BROWSER_PROFILE_DIR:-/app/data/etsy-browser-profile}"

if [ "${ETSY_BROWSER_RUNTIME:-local}" = "disabled" ]; then
    exec "$@"
fi

case "$DISPLAY_ID" in
    *[!0-9]*|'')
        echo "Invalid DISPLAY value: $DISPLAY_NUMBER" >&2
        exit 1
        ;;
esac

rm -rf \
    "$PROFILE_DIR/Default/Cache" \
    "$PROFILE_DIR/Default/Code Cache" \
    "$PROFILE_DIR/Default/GPUCache" \
    "$PROFILE_DIR/Crashpad"
# Снимаем stale-блокировки профиля после аварийного рестарта,
# иначе Playwright падает с "Opening in existing browser session"
# и Etsy CAPTCHA-ссылка не уходит в Telegram.
rm -f \
    "$PROFILE_DIR/SingletonLock" \
    "$PROFILE_DIR/SingletonCookie" \
    "$PROFILE_DIR/SingletonSocket" \
    "$PROFILE_DIR/lockfile"

# Docker restart сохраняет writable layer контейнера. После аварийного
# завершения Xvfb его lock/socket остаются и новый X-сервер не запускается.
rm -f \
    "/tmp/.X${DISPLAY_ID}-lock" \
    "/tmp/.X11-unix/X${DISPLAY_ID}"

Xvfb "$DISPLAY_NUMBER" -screen 0 "$SCREEN_SIZE" -nolisten tcp &
XVFB_PID=$!

# x11vnc должен запускаться только после готовности X11 socket.
attempt=0
while [ ! -S "/tmp/.X11-unix/X${DISPLAY_ID}" ]; do
    if ! kill -0 "$XVFB_PID" 2>/dev/null; then
        wait "$XVFB_PID" || true
        echo "Xvfb failed to start on $DISPLAY_NUMBER" >&2
        exit 1
    fi
    attempt=$((attempt + 1))
    if [ "$attempt" -ge 50 ]; then
        echo "Timed out waiting for Xvfb on $DISPLAY_NUMBER" >&2
        exit 1
    fi
    sleep 0.1
done

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
