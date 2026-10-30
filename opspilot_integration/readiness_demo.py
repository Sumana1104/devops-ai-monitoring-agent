"""Prepare or inject the explicitly opt-in LOCAL demo; never approve/repair it.

prepare deploys the new demo image and reloads Prometheus rules. inject creates
one unique pod-scoped readiness flag. Neither command clears an incident flag,
creates an approval, calls /alerts manually or executes remediation.
"""
import argparse
import json
import os
from pathlib import Path
import secrets
import subprocess

import requests
from dotenv import load_dotenv

from ai_service.app import readiness_remediation as readiness

ROOT = Path(__file__).resolve().parent.parent
INJECT_SCRIPT = '''import re,sys
from pathlib import Path
token=sys.argv[1]
if not re.fullmatch(r"[a-f0-9]{32}",token): raise SystemExit("Invalid fault identity")
with Path("/runtime/readiness.flag").open("x") as f: f.write(token)
print("Intentional readiness flag created; no restart or repair performed")
'''


def bridge_health(safe=False):
    response = requests.get("http://127.0.0.1:18008/health", timeout=5)
    response.raise_for_status()
    health = response.json()
    readiness.require(health.get("local_bridge") is True
                      and health.get("runtime_version") == "opspilot-v1-metrics-1"
                      and type(health.get("demo_readiness_repair_enabled")) is bool,
                      "Updated readiness bridge is not running")
    if safe:
        readiness.require(health.get("demo_restart_enabled") is False
                          and health.get("traffic_scaling_enabled", False) is False
                          and health.get("oom_repair_enabled", False) is False
                          and health.get("demo_readiness_repair_enabled") is False,
                          "Prepare requires the bridge in safe mode")
    return health


def prepare(confirmed=False):
    readiness.require(confirmed, "Use --confirm-demo-only")
    bridge_health(safe=True)
    existing = readiness.get("deployment", readiness.DEPLOYMENT)
    image = readiness.single_container(existing, template=True).get("image")
    if image == readiness.IMAGE:
        readiness.snapshot(True)
        print("Readiness demo image/probes are already prepared and Ready; not rebuilding/replacing the Pod.")
    else:
        readiness.require(image in ("opspilot-demo:integrated-v1", "opspilot-demo:readiness-v1"), "Unknown demo image; no replacement permitted")
        old_path = "/healthz" if image == "opspilot-demo:integrated-v1" else "/readyz"
        deployment, _, _, _ = readiness.snapshot(True, image=image, readiness_path=old_path)
    # Validate the NEW host file, not a potentially stale file bind in the old
    # container. Use the same pinned Prometheus tool image as the existing stack.
    subprocess.run(["docker", "run", "--rm", "--network=none", "--entrypoint=/bin/promtool",
                    "--mount", "type=bind,src=" + str(ROOT / "opspilot_integration/observability/alerts.yml") + ",dst=/rules.yml,readonly",
                    "prom/prometheus:v3.5.0", "check", "rules", "/rules.yml"], check=True, timeout=60)
    if image != readiness.IMAGE:
        print("Preparing ONLY opspilot/opspilot-demo; its Pod will be replaced once. No incident is being repaired.", flush=True)
        subprocess.run(["docker", "build", "--pull", "-t", readiness.IMAGE,
                        str(ROOT / "opspilot_integration/demo")], check=True, timeout=600)
        subprocess.run(["minikube", "--profile", "opspilot", "image", "load", readiness.IMAGE], check=True, timeout=180)
        patch = [
            {"op": "test", "path": "/metadata/uid", "value": deployment["metadata"]["uid"]},
            {"op": "test", "path": "/metadata/resourceVersion", "value": deployment["metadata"]["resourceVersion"]},
            {"op": "test", "path": "/spec/template/spec/containers/0/name", "value": readiness.CONTAINER},
            {"op": "test", "path": "/spec/template/spec/containers/0/image", "value": image},
            {"op": "test", "path": "/spec/template/spec/containers/0/readinessProbe/httpGet/path", "value": old_path},
            {"op": "replace", "path": "/spec/template/spec/containers/0/image", "value": readiness.IMAGE},
            {"op": "replace", "path": "/spec/template/spec/containers/0/readinessProbe/httpGet/path", "value": "/readyz"},
        ]
        readiness.run("patch", "deployment", readiness.DEPLOYMENT, "--type=json", "-p", json.dumps(patch))
        readiness.run("rollout", "status", "deployment/opspilot-demo", "--timeout=180s", timeout=190)
        readiness.snapshot(True)
    # Existing mounted rules need a Prometheus reload; only its own service is restarted.
    subprocess.run(["docker", "compose", "-f", str(ROOT / "observability/docker-compose.yml"),
                    "-f", str(ROOT / "opspilot_integration/compose.integration.yml"),
                    "up", "-d", "--no-deps", "--force-recreate", "prometheus"], check=True, timeout=60)
    print("Prepared: readiness /readyz; liveness /healthz; HTTP port 8080 unchanged. Prometheus rule reloaded.")
    print("No readiness fault injected and no approval or remediation executed.")


def inject(confirmed=False):
    readiness.require(confirmed, "Use --confirm-demo-only")
    health = bridge_health()
    from opspilot_integration import telegram_webhook as webhook
    url = webhook.call("getWebhookInfo").get("url", "")
    suffix = "/telegram/webhook"
    readiness.require(isinstance(url, str) and url.endswith(suffix), "Local Telegram callback is not configured")
    webhook.check_route(url[:-len(suffix)])
    base = os.getenv("OPSPILOT_PROMETHEUS_URL", "http://127.0.0.1:9090").rstrip("/")
    response = requests.get(base + "/api/v1/rules", params={"type": "alert"}, timeout=5)
    response.raise_for_status()
    rules = response.json()
    readiness.require(rules.get("status") == "success" and any(
        rule.get("name") == "OpsPilotDemoReadinessFailure" and rule.get("health") == "ok"
        for group in rules.get("data", {}).get("groups", []) for rule in group.get("rules", [])),
        "The readiness alert rule is not loaded/healthy")
    response = requests.get(base + "/api/v1/query", params={
        "query": '(opspilot_demo_ready{job="opspilot-demo"} == 1) and on(job, instance) (up{job="opspilot-demo"} == 1)'}, timeout=5)
    response.raise_for_status()
    payload = response.json()
    readiness.require(payload.get("status") == "success" and len(payload.get("data", {}).get("result", [])) == 1,
                      "Wait for a healthy demo readiness metric before injection")
    _, pod, _, _ = readiness.snapshot(True)
    checks = json.loads(readiness.run("exec", pod["metadata"]["name"], "-c", readiness.CONTAINER,
                                     "--", "python", "-c", readiness.PROBE_SCRIPT))
    readiness.require(checks.get("fault_id") is None and checks.get("readiness_status") == 200
                      and checks.get("liveness_status") == 200 and checks.get("liveness_ok") is True,
                      "Demo is not healthy or a readiness flag already exists")
    readiness.run("exec", pod["metadata"]["name"], "-c", readiness.CONTAINER,
                  "--", "python", "-c", readiness.SERVICE_SCRIPT)
    fault_id = secrets.token_hex(16)
    print("Injecting readiness failure ONLY into opspilot/" + pod["metadata"]["name"], flush=True)
    readiness.run("exec", pod["metadata"]["name"], "-c", readiness.CONTAINER,
                  "--", "python", "-c", INJECT_SCRIPT, fault_id)
    print("Fault ID:", fault_id)
    print("Expected: /readyz 503, /healthz 200, Pod Running 0/1, no ready Service endpoint; metrics stay up.")
    print("Wait for real Prometheus -> Alertmanager -> LangGraph / RAG / Groq -> Telegram.")
    print("Readiness repair execution enabled:", health["demo_readiness_repair_enabled"])
    print("This command created NO approval and performed NO repair. Approve only a fresh readiness request.")


def main():
    load_dotenv(ROOT / ".env", override=False)
    load_dotenv(ROOT / "ai_service/.env", override=False)
    os.environ["PATH"] = str(Path.home() / ".local/bin") + os.pathsep + os.environ.get("PATH", "")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("prepare", "inject", "status"))
    parser.add_argument("--confirm-demo-only", action="store_true")
    args = parser.parse_args()
    if args.command == "status":
        print(json.dumps(readiness.get_live_readiness_evidence(), indent=2))
    elif args.command == "prepare":
        prepare(args.confirm_demo_only)
    else:
        inject(args.confirm_demo_only)


if __name__ == "__main__":
    try:
        main()
    except ValueError as error:
        raise SystemExit("STOP: " + str(error)) from None
    except Exception as error:
        raise SystemExit("STOP: readiness demo command failed (" + type(error).__name__ + "). No recovery confirmed.") from None
