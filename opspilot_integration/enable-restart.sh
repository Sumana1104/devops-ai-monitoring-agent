#!/usr/bin/env bash
# Explicit opt-in only for the safely allowlisted demo rollout restart.
set -Eeuo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$HERE/.." && pwd)"
cd "$REPO"
[[ "$(kubectl config current-context)" == 'opspilot' ]] || { echo 'STOP: wrong Kubernetes context'; exit 1; }
"$REPO/venv/bin/python3" - <<'PY'
from ai_service.app import remediation_executor as e
from api_service.app import approval_store as s
assert (e.NAMESPACE, e.DEPLOYMENT, e.APP_LABEL) == ('opspilot','opspilot-demo','app=opspilot-demo')
assert (s.NAMESPACE, s.DEPLOYMENT) == ('opspilot','opspilot-demo')
print('PASS: both Python allowlists restrict actions to opspilot-demo')
PY
read -r -p 'Enable REAL Kubernetes rollout restart of ONLY opspilot-demo after Telegram approval? Type ENABLE: ' answer
[[ "$answer" == 'ENABLE' ]] || { echo 'Cancelled'; exit 0; }
PID_FILE="$HERE/runtime/bridge.pid"
[[ -s "$PID_FILE" ]] || { echo 'Start the integration first'; exit 1; }
pid="$(cat "$PID_FILE")"
[[ "$pid" =~ ^[0-9]+$ ]] && kill -0 "$pid" || { echo 'Bridge is not running'; exit 1; }
kill "$pid"
for x in {1..30}; do
 if ! kill -0 "$pid" 2>/dev/null; then break; fi
 sleep 0.2
done
if kill -0 "$pid" 2>/dev/null; then echo 'Old bridge not stopping; refusing to start duplicate'; exit 1; fi
nohup env PYTHONPATH="$REPO" OPSPILOT_APPROVAL_DB="$REPO/opspilot_approvals.db" OPSPILOT_ENABLE_DEMO_RESTART=1 "$REPO/venv/bin/python3" -m uvicorn opspilot_integration.bridge:app --host 0.0.0.0 --port 18008 > "$HERE/runtime/bridge.log" 2>&1 < /dev/null &
echo "$!" > "$PID_FILE"
for x in {1..40}; do
 mode="$(curl -fsS http://127.0.0.1:18008/health 2>/dev/null || true)"
 if echo "$mode" | grep -q '"demo_restart_enabled":true'; then echo 'PASS: Allowlisted demo-only restart gate enabled'; exit 0; fi
 sleep 0.5
done
echo 'ERROR: bridge did not return expected live restart gate; inspect runtime/bridge.log'; exit 1
