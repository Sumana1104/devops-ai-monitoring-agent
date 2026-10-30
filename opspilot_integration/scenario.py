"""One local V1 interface. Status/verify are read-only; faults require opt-in.

No fault command calls an LLM, posts fake alerts, approves an action or performs
remediation. Prometheus must observe the metric change and trigger the agent.
"""
import argparse
import copy
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time

import requests
from dotenv import load_dotenv

from ai_service.app import readiness_remediation as ready, resource_incidents as resource
from opspilot_integration import readiness_demo

ROOT = Path(__file__).resolve().parents[1]
MEMORY_ENV = "OPSPILOT_DEMO_MEMORY_MIB"
RULES = {"crash": "OpsPilotDemoCrashLoopBackOff", "oom": "OpsPilotDemoOOMKilled",
         "readiness": "OpsPilotDemoReadinessFailure"}


def quantity(value):
    match = re.fullmatch(r"([0-9]+)(Ki|Mi|Gi)", str(value))
    ready.require(match is not None, "Unknown memory quantity; refusing to guess")
    return int(match[1]) * {"Ki": 1024, "Mi": 1024**2, "Gi": 1024**3}[match[2]]


def metric(query):
    base = os.getenv("OPSPILOT_PROMETHEUS_URL", "http://127.0.0.1:9090").rstrip("/")
    response = requests.get(base + "/api/v1/query", params={"query": query}, timeout=5)
    response.raise_for_status()
    payload = response.json()
    ready.require(payload.get("status") == "success", "Prometheus query unavailable")
    return payload.get("data", {}).get("result", [])


def metric_one(query):
    rows = metric(query)
    ready.require(len(rows) == 1 and float(rows[0]["value"][1]) == 1,
                  "Expected one healthy metric series; wait for a fresh Prometheus scrape")


def check_routes(kind):
    health = readiness_demo.bridge_health()
    feature = {"oom": "oom_repair_enabled", "crash": "demo_restart_enabled",
               "readiness": "demo_readiness_repair_enabled"}[kind]
    ready.require(health.get(feature) is True, "Enable the scenario-specific execution flag before a live recovery test")
    ready.require(health.get("investigation_trigger") == "verified_prometheus_alert"
                  and health.get("watcher_issues_approvals") is False,
                  "The metrics-first bridge must be running")
    from opspilot_integration import telegram_webhook as webhook
    target = webhook.call("getWebhookInfo").get("url", "")
    suffix = "/telegram/webhook"
    ready.require(isinstance(target, str) and target.endswith(suffix), "Local Telegram callback not configured")
    public = webhook.check_route(target[:-len(suffix)])
    ready.require(public.get("runtime_version") == health.get("runtime_version")
                  and public.get(feature) is True, "Public tunnel does not reach this execution mode")
    response = requests.get(os.getenv("OPSPILOT_PROMETHEUS_URL", "http://127.0.0.1:9090").rstrip("/")
                            + "/api/v1/rules", params={"type": "alert"}, timeout=5)
    response.raise_for_status()
    payload = response.json()
    ready.require(payload.get("status") == "success" and any(
        r.get("name") == RULES[kind] and r.get("health") == "ok" for g in
        payload.get("data", {}).get("groups", []) for r in g.get("rules", [])),
        "Scenario Prometheus rule is not loaded and healthy")
    metric_one('up{job="opspilot-kubernetes"}')
    metric_one('opspilot_kubernetes_evidence_available{job="opspilot-kubernetes"}')
    metric_one('up{job="opspilot-demo"}')


def healthy_demo():
    deployment, pod, status, service = ready.snapshot(True)
    ready.run("exec", pod["metadata"]["name"], "-c", ready.CONTAINER, "--", "python", "-c", ready.SERVICE_SCRIPT)
    return deployment, pod, status, service


def memory_headroom(pod):
    node_name = pod["spec"].get("nodeName")
    ready.require(isinstance(node_name, str) and node_name == "opspilot", "Expected the local opspilot node")
    node = resource.get("node", node_name)
    conditions = {c.get("type"): c.get("status") for c in node.get("status", {}).get("conditions", [])}
    ready.require(conditions.get("Ready") == "True" and conditions.get("MemoryPressure") == "False",
                  "Node is unhealthy or under memory pressure")
    usage = json.loads(resource.run("get", "--raw", "/apis/metrics.k8s.io/v1beta1/nodes/opspilot"))
    ready.require(usage.get("metadata", {}).get("name") == node_name, "Wrong node memory sample")
    stamp = datetime.fromisoformat(usage["timestamp"].replace("Z", "+00:00"))
    ready.require(stamp.tzinfo is not None and -5 <= time.time() - stamp.timestamp() <= 60,
                  "Node memory sample is stale or its clock is inconsistent")
    available = quantity(node["status"]["allocatable"]["memory"]) - quantity(usage["usage"]["memory"])
    ready.require(available >= 1024**3, "Less than 1 GiB node headroom; do not inject the memory test")
    meminfo = Path("/proc/meminfo").read_text()
    host = re.search(r"^MemAvailable:\s+(\d+)\s+kB$", meminfo, re.M)
    ready.require(host is not None and int(host[1]) * 1024 >= 1024**3,
                  "Less than 1 GiB available in WSL; close unrelated workloads first")
    return available // 1024**2


def patch_env(deployment, new_env):
    container = ready.single_container(deployment, template=True)
    path = "/spec/template/spec/containers/0/env"
    changes = [{"op": "test", "path": "/metadata/uid", "value": deployment["metadata"]["uid"]},
               {"op": "test", "path": "/metadata/resourceVersion", "value": deployment["metadata"]["resourceVersion"]},
               {"op": "test", "path": "/spec/template/spec/containers/0/image", "value": ready.IMAGE},
               {"op": "test", "path": "/spec/template/spec/containers/0/resources", "value": container["resources"]}]
    if "env" in container:
        changes.append({"op": "test", "path": path, "value": container["env"]})
    changes.append({"op": "replace" if "env" in container else "add", "path": path, "value": new_env})
    ready.run("patch", "deployment", ready.DEPLOYMENT, "--type=json", "-p", json.dumps(changes))


def inject_oom(confirmed=False):
    ready.require(confirmed, "Use --confirm-demo-only; this intentionally stops ONLY the demo app")
    check_routes("oom")
    deployment, pod, _, _ = healthy_demo()
    container = ready.single_container(deployment, template=True)
    resources = container.get("resources", {})
    ready.require(resources.get("requests", {}).get("memory") == "64Mi"
                  and resources.get("limits", {}).get("memory") == "256Mi",
                  "This fixed test requires request 64Mi / limit 256Mi; no resource settings were changed")
    env = copy.deepcopy(container.get("env", []))
    ready.require(isinstance(env, list) and not any(e.get("name") == MEMORY_ENV for e in env),
                  "A memory test is already present; do not inject twice")
    available = memory_headroom(pod)
    print("Explicit controlled test: fixed 320 MiB main-process demand against the existing 256Mi cap.", flush=True)
    print("Node headroom MiB:", available)
    print("Only opspilot/opspilot-demo will roll out and become unavailable. No approval or repair is performed here.", flush=True)
    patch_env(deployment, env + [{"name": MEMORY_ENV, "value": "320"}])
    print("Test injected. Wait for REAL OOMKilled/137 -> metric -> Prometheus alert -> AI -> Telegram.")
    print("Approve ONLY the fresh increase_demo_memory_limit request, 256Mi -> 512Mi.")
    print("If this test must be cancelled, scenario cleanup-oom --confirm-demo-only is an explicit MANUAL reset, not AI recovery.")


def cleanup_oom(confirmed=False):
    ready.require(confirmed, "Use --confirm-demo-only for an explicit manual test cleanup")
    deployment, _, container = resource.snapshot()
    ready.check_profile(deployment)
    env = container.get("env", [])
    matches = [e for e in env if e.get("name") == MEMORY_ENV]
    ready.require(matches == [{"name": MEMORY_ENV, "value": "320"}], "No exact known memory-test marker found")
    ready.require(container.get("resources", {}).get("limits", {}).get("memory") in {"256Mi", "512Mi"}, "Unexpected memory limit; manual review required")
    patch_env(deployment, [e for e in env if e.get("name") != MEMORY_ENV])
    ready.run("rollout", "status", "deployment/opspilot-demo", "--timeout=180s", timeout=190)
    healthy_demo()
    print("Manual test load removed. Existing memory limit retained; no incident was approved or indexed by this cleanup.")


def verify():
    import sqlite3
    from urllib.parse import quote
    from ai_service.app.incident_history import history_path, decode
    from api_service.app.approval_store import get_approval, DB_PATH
    ready.require(history_path().is_file() and Path(DB_PATH).is_file(), "Existing incident journals are required; no empty database will be created")
    db = sqlite3.connect("file:" + quote(str(history_path().resolve()), safe="/") + "?mode=ro", uri=True)
    db.row_factory = sqlite3.Row
    try:
        rows = [decode(row) for row in db.execute("SELECT * FROM ai_investigations WHERE stage='recovered' ORDER BY created_at DESC LIMIT 1000").fetchall()]
    finally:
        db.close()
    found = {}
    required = {"crash": "restart_deployment", "oom": resource.ACTION, "readiness": ready.ACTION}
    for kind, action in required.items():
        for row in rows:
            outcome = row.get("outcome") or {}
            approval = get_approval(row["approval_id"]) if row.get("approval_id") else None
            proof = outcome.get("recovery") or {}
            if (not approval or approval.get("action") != action or approval.get("status") != "claimed"
                    or row.get("stage") != "recovered" or outcome.get("status") != "recovered"
                    or not outcome.get("execution_attempted") or proof.get("recovered") is not True):
                continue
            if kind == "crash" and outcome.get("restart_command_succeeded") is not True:
                continue
            if kind != "crash" and (outcome.get("repair_command_succeeded") is not True
                    or proof.get("service_http_verified") is not True):
                continue
            found[kind] = {"approval": row["approval_id"], "investigation": row["investigation_id"],
                           "indexed": bool(row.get("indexed")), "verified_recovery": True}
            break
    healthy_demo()
    print(json.dumps({"current_demo": "healthy_and_service_http_200", "verified_scenarios": found,
                      "missing_scenarios": [k for k in required if k not in found]}, indent=2))
    return len(found) == 3


def main():
    load_dotenv(ROOT / ".env", override=False)
    load_dotenv(ROOT / "ai_service/.env", override=False)
    os.environ["PATH"] = str(Path.home() / ".local/bin") + os.pathsep + os.environ.get("PATH", "")
    os.environ["OPSPILOT_APPROVAL_DB"] = str(ROOT / "opspilot_approvals.db")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("prepare", "oom", "cleanup-oom", "readiness", "crash", "verify"))
    parser.add_argument("--confirm-demo-only", action="store_true")
    args = parser.parse_args()
    if args.command == "prepare":
        readiness_demo.prepare(args.confirm_demo_only)
    elif args.command == "oom":
        inject_oom(args.confirm_demo_only)
    elif args.command == "cleanup-oom":
        cleanup_oom(args.confirm_demo_only)
    elif args.command == "verify":
        if not verify():
            raise SystemExit(2)
    else:
        ready.require(args.confirm_demo_only, "Use --confirm-demo-only")
        check_routes(args.command)
        deployment, _, _, _ = healthy_demo()
        ready.require(not any(e.get("name") == MEMORY_ENV for e in ready.single_container(deployment, template=True).get("env", [])),
                      "Remove the controlled OOM test load before another scenario")
        if args.command == "readiness":
            readiness_demo.inject(True)
        else:
            subprocess.run([sys.executable, "-m", "opspilot_integration.demo_failure", "--confirm-demo-only"], check=True)


if __name__ == "__main__":
    try:
        main()
    except ValueError as error:
        raise SystemExit("STOP: " + str(error)) from None
    except Exception as error:
        raise SystemExit("STOP: scenario operation failed (" + type(error).__name__ + "). No recovery is implied; do not repeat an uncertain mutation.") from None
