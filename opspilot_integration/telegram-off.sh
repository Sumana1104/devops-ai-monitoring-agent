#!/usr/bin/env bash
set -Eeuo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$HERE/.." && pwd)"
"$REPO/venv/bin/python3" "$HERE/telegram_webhook.py" off
p="$HERE/runtime/tunnel.pid"
if [[ -f "$p" ]]; then
 pid="$(cat "$p")"
 if [[ "$pid" =~ ^[0-9]+$ ]] && kill -0 "$pid" 2>/dev/null; then kill "$pid"; fi
 rm -f "$p"
fi
