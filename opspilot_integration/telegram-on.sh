#!/usr/bin/env bash
set -Eeuo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$HERE/.." && pwd)"
cd "$REPO"
command -v cloudflared >/dev/null || { echo 'cloudflared is required for Telegram to reach local Ubuntu'; exit 1; }
curl -fsS 'http://127.0.0.1:18008/health' >/dev/null || { echo 'Bridge unavailable. First run ./opspilot_integration/start.sh'; exit 1; }
if [[ ! -s "$HERE/runtime/tunnel.pid" ]] || ! kill -0 "$(cat "$HERE/runtime/tunnel.pid")" 2>/dev/null; then
  : > "$HERE/runtime/tunnel.log"
  nohup cloudflared tunnel --url http://127.0.0.1:18008 > "$HERE/runtime/tunnel.log" 2>&1 < /dev/null &
  echo "$!" > "$HERE/runtime/tunnel.pid"
fi
TUNNEL=''
for attempt in {1..45}; do
 TUNNEL="$(grep -Eo 'https://[a-zA-Z0-9-]+\.trycloudflare\.com' "$HERE/runtime/tunnel.log" | head -1 || true)"
 [[ -n "$TUNNEL" ]] && break
 sleep 2
done
[[ -n "$TUNNEL" ]] || { echo 'Could not find Cloudflare temporary tunnel URL. Inspect integration/runtime/tunnel.log'; exit 1; }
echo "Tunnel established: $TUNNEL"
"$REPO/venv/bin/python3" "$HERE/telegram_webhook.py" on "$TUNNEL"
