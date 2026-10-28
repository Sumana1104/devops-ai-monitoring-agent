#!/usr/bin/env bash
set -Eeuo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$HERE/.." && pwd)"
command -v curl >/dev/null || { echo 'curl missing'; exit 1; }
curl -fsS 'http://127.0.0.1:19110/healthz' >/dev/null || { echo 'Start integration first: ./opspilot_integration/start.sh'; exit 1; }
[[ -x "$REPO/venv/bin/locust" ]] || { echo 'Locust missing from existing venv. Install in venv: ./venv/bin/python3 -m pip install locust'; exit 1; }
echo 'Locust: 15 virtual users, 2 minutes; /error produces expected 500s.'
echo 'Watch Grafana: http://127.0.0.1:3000'
"$REPO/venv/bin/locust" -f "$HERE/locustfile.py" --headless --users 15 --spawn-rate 5 --run-time 2m --host http://127.0.0.1:19110 --only-summary || true
echo 'Load complete. Expected 500 responses are intentional; check Prometheus alerts and Telegram.'
