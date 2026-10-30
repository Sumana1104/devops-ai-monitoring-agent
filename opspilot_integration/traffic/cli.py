"""Explicit operator commands; no automatic fault flags or replica reset."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sqlite3
import requests
from opspilot_integration.traffic import evidence as e

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]


def bridge(scaling=None):
    r = requests.get("http://127.0.0.1:18008/health", timeout=5)
    r.raise_for_status()
    h = r.json()
    e.require(h.get("local_bridge") is True and h.get("traffic_extension_version") == "locust-capacity-1"
              and h.get("traffic_monitoring_enabled") is True, "Start updated traffic.run bridge first")
    e.require(all(h.get(k) is False for k in ("demo_restart_enabled", "oom_repair_enabled", "demo_readiness_repair_enabled")), "Original repair gates must be off")
    if scaling is not None:
        e.require(h.get("traffic_scaling_enabled") is scaling, "Wrong traffic scaling mode")
    return h


def apply(value):
    e.run("apply", "-f", "-", input=json.dumps(value))


def owned_or_absent(kind, name):
    raw = e.run("get", kind, name, "--ignore-not-found", "-o", "json")
    if raw.strip():
        obj = json.loads(raw)
        e.require(obj["metadata"].get("labels", {}).get("opspilot-experiment") == "traffic-v1", "Existing unowned object")
        return obj
    return None


def prepare():
    bridge(False)
    e.capacity()
    e.require(not owned_or_absent("deployment", e.TARGET) and not owned_or_absent("service", e.TARGET), "Traffic resources already exist; inspect, do not overwrite")
    subprocess.run(["docker", "build", "--pull", "-t", e.IMAGE, str(HERE)], check=True, timeout=300)
    subprocess.run(["minikube", "--profile", "opspilot", "image", "load", e.IMAGE], check=True, timeout=180)
    e.run("apply", "-f", str(HERE / "workload.yaml"))
    e.run("rollout", "status", "deployment/" + e.TARGET, "--timeout=120s", timeout=130)
    print("Traffic application prepared. Original demo untouched. No load or repair started.")


def load():
    bridge(True)
    e.deployment(1)
    e.capacity()
    from opspilot_integration import telegram_webhook as webhook
    target = webhook.call("getWebhookInfo").get("url", "")
    e.require(target.endswith("/telegram/webhook"), "Telegram local callback missing")
    webhook.check_route(target[:-len("/telegram/webhook")])
    e.require(not owned_or_absent("job", e.JOB) and not owned_or_absent("configmap", e.JOB), "Prior load objects exist; explicit cleanup required")
    labels = {"opspilot-experiment": "traffic-v1"}
    apply({"apiVersion": "v1", "kind": "ConfigMap", "metadata": {"name": e.JOB, "namespace": "opspilot", "labels": labels},
           "data": {"locustfile.py": (HERE / "locustfile.py").read_text()}})
    apply({"apiVersion": "batch/v1", "kind": "Job", "metadata": {"name": e.JOB, "namespace": "opspilot", "labels": labels},
           "spec": {"backoffLimit": 0, "activeDeadlineSeconds": 1260, "template": {"metadata": {"labels": labels}, "spec": {
               "automountServiceAccountToken": False, "restartPolicy": "Never",
               "containers": [{"name": "locust", "image": "locustio/locust:2.32.1",
                   "args": ["-f", "/load/locustfile.py", "--headless", "--host", "http://opspilot-traffic.opspilot.svc.cluster.local:8080", "--only-summary"],
                   "resources": {"requests": {"cpu": "100m", "memory": "128Mi"}, "limits": {"cpu": "500m", "memory": "256Mi"}},
                   "volumeMounts": [{"name": "load", "mountPath": "/load", "readOnly": True}]}],
               "volumes": [{"name": "load", "configMap": {"name": e.JOB}}]}}}})
    print("Locust: 1 user for 90 seconds, then 8 users; maximum 20 minutes. Real Service requests, no forced faults.")
    print("No alert is guaranteed. Never fabricate evidence if your laptop does not cross the measured thresholds.")


def stop_load():
    bridge()  # Emergency stopping of test traffic must not need an enabled gate.
    if owned_or_absent("job", e.JOB):
        e.run("delete", "job", e.JOB, "--wait=true", "--timeout=60s", timeout=70)
    if owned_or_absent("configmap", e.JOB):
        e.run("delete", "configmap", e.JOB)
    print("Removed only the labelled load Job/ConfigMap. Replica count and original demos unchanged.")


def history():
    from opspilot_integration.traffic.control import path
    p = path().resolve()
    e.require(p.is_file(), "No traffic journal exists yet")
    conn = sqlite3.connect(p.as_uri()+"?mode=ro", uri=True)
    try:
        rows = conn.execute("SELECT id,status,result,created FROM traffic_incidents ORDER BY created DESC LIMIT 10").fetchall()
        print(json.dumps([{"incident": r[0], "status": r[1], "result": json.loads(r[2] or "null"), "created": r[3]} for r in rows], indent=2))
    finally:
        conn.close()


def main():
    os.chdir(ROOT)
    os.environ["PATH"] = str(Path.home()/".local/bin") + os.pathsep + os.environ.get("PATH", "")
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("command", choices=("prepare", "load", "status", "stop-load", "history"))
    p.add_argument("--confirm-traffic-only", action="store_true")
    args = p.parse_args()
    if args.command not in ("status", "history") and not args.confirm_traffic_only:
        p.error("Mutations require --confirm-traffic-only")
    try:
        if args.command == "prepare": prepare()
        elif args.command == "load": load()
        elif args.command == "stop-load": stop_load()
        elif args.command == "history": history()
        else:
            print(json.dumps({"bridge": bridge(), "snapshot": e.snapshot(), "metrics": e.measurements()}, indent=2))
    except (ValueError, KeyError, TypeError, OSError, subprocess.SubprocessError, requests.RequestException) as error:
        raise SystemExit("STOP: " + (str(error) if isinstance(error, ValueError) else type(error).__name__) + ". No automatic retry.") from None


if __name__ == "__main__":
    main()
