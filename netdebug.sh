#!/bin/bash
# One-shot network diagnostics bundle (no secrets). Writes /tmp/netdebug.txt and
# prints it. Prefers the running panel's /api/debug endpoint (identical report);
# falls back to running diag.py directly if the panel itself is down.
set -u
OUT=/tmp/netdebug.txt
PORT="${PORT:-8080}"
APP_DIR=/opt/networkswitcher

if curl -fsS "http://127.0.0.1:${PORT}/api/debug" -o "$OUT" 2>/dev/null; then
  :
elif [[ -x "$APP_DIR/venv/bin/python" ]]; then
  ( cd "$APP_DIR" && "$APP_DIR/venv/bin/python" diag.py ) > "$OUT" 2>&1
else
  echo "could not reach the panel and no app venv found at $APP_DIR" > "$OUT"
fi

cat "$OUT"
echo
echo "(saved to $OUT)"
