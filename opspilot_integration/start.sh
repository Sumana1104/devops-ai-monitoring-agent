#!/usr/bin/env bash
# Start the entire local OpsPilot demonstration without changing existing app source.
set -Eeuo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$HERE/.." && pwd)"
cd "$REPO"
PY="$REPO/venv/bin/python3"
DC=(docker compose -f "$REPO/observability/docker-compose.yml" -f "$HERE/compose.integration.yml")
echo '=== OpsPilot integration preflight ==='
[[ -f "$REPO/observability/docker-compose.yml" && -x "$PY" ]] || { echo 'Expected your existing repo and venv. No changes made.'; exit 1; }
command -v kubectl >/dev/null && command -v minikube >/dev/null && command -v docker >/dev/null || { echo 'kubectl, minikube, and docker required.'; exit 1; }
"$PY" - <<'PY'
import importlib
for name in ('fastapi','uvicorn','dotenv','requests','groq','langgraph','chromadb','sentence_transformers'):
    try: importlib.import_module(name)
    except Exception as exc: raise SystemExit('Missing local venv dependency '+name+': '+str(exc))
from ai_service.app import remediation_executor as executor
from api_service.app import approval_store as store
assert (executor.NAMESPACE, executor.DEPLOYMENT, executor.APP_LABEL) == ('opspilot','opspilot-demo','app=opspilot-demo'), 'STOP: local remediation executor is not allowlisted to opspilot-demo'
assert (store.NAMESPACE, store.DEPLOYMENT) == ('opspilot','opspilot-demo'), 'STOP: local approval store is not allowlisted to opspilot-demo'
print('PASS: dependencies and local approval target are correct')
PY
CONTEXT="$(kubectl config current-context)"
[[ "$CONTEXT" == 'opspilot' ]] || { echo "STOP: kubectl context is '$CONTEXT', expected 'opspilot'. No Kubernetes changes made."; exit 1; }
minikube -p opspilot status >/dev/null || { echo 'Minikube profile opspilot is not running'; exit 1; }
mkdir -p "$HERE/runtime"
if [[ ! -s "$HERE/runtime/alert_token" ]]; then
  "$PY" - "$HERE/runtime/alert_token" <<'PY'
from pathlib import Path
import secrets, os, sys
p=Path(sys.argv[1]); p.write_text(secrets.token_urlsafe(32));os.chmod(p,0o600)
PY
fi
chmod 600 "$HERE/runtime/alert_token"
echo 'This updates ONLY deployment/opspilot-demo, not the main opspilot deployment.'
if [[ "${OPSPILOT_SKIP_CONFIRM:-}" != "1" ]]; then
  read -r -p 'Replace the demo pod with the instrumented FastAPI test target? [yes/NO] ' reply
  [[ "$reply" == 'yes' ]] || { echo 'Stopped without replacing demo.'; exit 0; }
fi
echo '=== Build image into the existing Minikube profile ==='
minikube -p opspilot image build -t opspilot-demo:v1-final-1 -f "$HERE/demo/Dockerfile" "$HERE/demo"
kubectl --context opspilot apply -f "$HERE/k8s/demo.yaml"
kubectl --context opspilot -n opspilot rollout status deployment/opspilot-demo --timeout=180s

function alive() { curl --noproxy '*' --silent --fail --max-time 2 "$1" >/dev/null 2>&1; }
function wait_http() { local tries=0; until alive "$1"; do (( tries += 1 )); [[ "$tries" -lt 40 ]] || { echo "TIMEOUT: $1"; return 1; }; sleep 2; done; }

echo '=== Start Kubernetes service port-forward ==='
if ! alive 'http://127.0.0.1:19110/healthz'; then
  if ss -ltn 2>/dev/null | grep -q ':19110 '; then echo 'STOP: port 19110 belongs to another process'; exit 1; fi
  nohup "$HERE/portforward-loop.sh" > "$HERE/runtime/port-forward.log" 2>&1 < /dev/null &
  echo "$!" > "$HERE/runtime/port-forward.pid"
fi
wait_http 'http://127.0.0.1:19110/healthz'
echo 'PASS: localhost:19110 -> real Minikube pod. (Local demo port bound to all host interfaces for Docker access.)'

echo '=== Start local incident + Telegram callback bridge ==='
if ! alive 'http://127.0.0.1:18008/health'; then
  if ss -ltn 2>/dev/null | grep -q ':18008 '; then echo 'STOP: port 18008 belongs to another process'; exit 1; fi
  nohup env PYTHONPATH="$REPO" OPSPILOT_APPROVAL_DB="$REPO/opspilot_approvals.db" OPSPILOT_ENABLE_DEMO_RESTART=0 OPSPILOT_ENABLE_DEMO_READINESS_REPAIR=0 "$PY" -m uvicorn opspilot_integration.bridge:app --host 0.0.0.0 --port 18008 > "$HERE/runtime/bridge.log" 2>&1 < /dev/null &
  echo "$!" > "$HERE/runtime/bridge.pid"
fi
wait_http 'http://127.0.0.1:18008/health'
echo 'PASS: local incident bridge is answering health checks.'

echo '=== Connect existing Prometheus + Grafana + Alertmanager ==='
"${DC[@]}" config --quiet
"${DC[@]}" up -d --no-deps prometheus alertmanager grafana
wait_http 'http://127.0.0.1:9090/-/ready'
wait_http 'http://127.0.0.1:19093/-/ready'
echo '=== Run verification ==='
"$PY" "$HERE/verify.py"
echo
printf 'NEXT: ./opspilot_integration/load-test.sh\n'
printf 'To route Telegram Approve/Reject to THIS laptop, run: ./opspilot_integration/telegram-on.sh\n'
printf 'Grafana: http://127.0.0.1:3000 (Dashboard: OpsPilot — Live Incidents)\n'
