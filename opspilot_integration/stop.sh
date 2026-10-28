#!/usr/bin/env bash
set -Eeuo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
echo 'Stop the demo port-forward and local bridge. Does not stop Docker/Minikube or restore Telegram.'
for name in bridge port-forward; do
  f="$HERE/runtime/$name.pid"
  if [[ -f "$f" ]]; then
    pid="$(cat "$f")"
    if [[ "$pid" =~ ^[0-9]+$ ]] && kill -0 "$pid" 2>/dev/null; then kill "$pid"; echo "Stopped $name ($pid)"; fi
    rm -f "$f"
  fi
done
if [[ -s "$HERE/runtime/telegram_previous_webhook.json" ]]; then
 echo 'IMPORTANT: Telegram webhook backup exists. Run ./opspilot_integration/telegram-off.sh BEFORE closing the tunnel!'
fi
