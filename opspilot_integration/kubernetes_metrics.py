"""Small read-only Kubernetes exporter; observation is never investigation.

Only one fixed local Deployment is observed. No credentials, Pod names, logs or
LLM data are exposed. A failed/stale observation is unknown, not healthy.
"""
import threading
import time

from ai_service.app import resource_incidents as resource

_lock = threading.Lock()
_values = None
_at = 0.0


def observe():
    deployment, pod, _ = resource.snapshot()
    status = pod.get("status", {})
    ready = any(c.get("type") == "Ready" and c.get("status") == "True"
                for c in status.get("conditions", []))
    rows = status.get("containerStatuses", [])
    row = rows[0] if len(rows) == 1 else {}
    state = row.get("state", {})
    previous = state.get("terminated") or row.get("lastState", {}).get("terminated", {})
    waiting = state.get("waiting", {}).get("reason")
    active = not ready and row.get("ready") is False
    failed = active and (waiting == "CrashLoopBackOff" or "terminated" in state)
    oom = failed and previous.get("reason") == "OOMKilled" and type(previous.get("exitCode")) is int and previous["exitCode"] == 137
    crash = (failed and waiting == "CrashLoopBackOff" and previous.get("reason") == "Error"
             and type(previous.get("exitCode")) is int and previous["exitCode"] != 0)
    scheduling = (not ready and status.get("phase") == "Pending" and not pod["spec"].get("nodeName")
                  and any(c.get("type") == "PodScheduled" and c.get("status") == "False"
                          and c.get("reason") == "Unschedulable" for c in status.get("conditions", [])))
    return {"opspilot_demo_pod_ready": int(ready), "opspilot_demo_oom_active": int(oom),
            "opspilot_demo_crash_active": int(crash), "opspilot_demo_unschedulable": int(scheduling)}


def refresh():
    global _values, _at
    try:
        values = observe()
    except (OSError, ValueError, KeyError, TypeError, resource.subprocess.SubprocessError):
        values = None
    with _lock:
        _values, _at = values, time.monotonic()


def render():
    with _lock:
        values = dict(_values) if _values is not None and 0 <= time.monotonic() - _at <= 30 else None
    valid = values is not None
    rows = ["# HELP opspilot_kubernetes_evidence_available Fresh allowlisted Kubernetes evidence is available.",
            "# TYPE opspilot_kubernetes_evidence_available gauge",
            'opspilot_kubernetes_evidence_available{service="opspilot-demo"} ' + str(int(valid))]
    # Never emit zeros for unknown incident state: that could look like recovery.
    for name, value in (values or {}).items():
        rows += ["# TYPE " + name + " gauge", name + '{service="opspilot-demo"} ' + str(value)]
    return "\n".join(rows) + "\n"
