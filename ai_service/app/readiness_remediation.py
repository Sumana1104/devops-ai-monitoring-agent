"""Only the explicitly injected readiness.flag in the local demo is repairable.

No AI text becomes a command. All kubectl commands have a fixed context/target;
all scripts below have a fixed filename, endpoints and operation. Unknown
readiness failures, OOMs, restarts and probe misconfiguration fail closed.
"""
import json
import os
import re
import subprocess
import time

CONTEXT = NAMESPACE = "opspilot"
DEPLOYMENT = CONTAINER = "opspilot-demo"
IMAGE = "opspilot-demo:v1-final-1"
ACTION = "clear_demo_readiness_flag"
INCIDENT_TYPE = "readiness_failure"
FEATURE = "OPSPILOT_ENABLE_DEMO_READINESS_REPAIR"
COMMAND = ["kubectl", "--context", CONTEXT, "-n", NAMESPACE]
BINDING = ("deployment_uid", "deployment_generation", "pod", "pod_uid", "container_id",
           "running_since", "restart_count", "image", "fault_id", "service_uid")

# These strings are constant source code, not shell/LLM/provider output.
PROBE_SCRIPT = '''import json,stat,urllib.request,urllib.error
from pathlib import Path
p=Path("/runtime/readiness.flag")
fault=None
if p.exists() and not p.is_symlink() and stat.S_ISREG(p.stat().st_mode):
    with p.open() as f: fault=f.read(129)
opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
def check(path):
    try:
        with opener.open("http://127.0.0.1:8080"+path,timeout=3) as r:
            return r.status,json.load(r)
    except urllib.error.HTTPError as e:
        return e.code,None
live,body=check("/healthz")
ready,_=check("/readyz")
print(json.dumps({"fault_id":fault,"liveness_status":live,"liveness_ok":body=={"ok":True},"readiness_status":ready}))
'''
REPAIR_SCRIPT = '''import re,stat,sys
from pathlib import Path
expected=sys.argv[1]
if not re.fullmatch(r"[a-f0-9]{32}",expected): raise SystemExit("Invalid fault identity")
p=Path("/runtime/readiness.flag")
if p.is_symlink() or not p.exists() or not stat.S_ISREG(p.stat().st_mode): raise SystemExit("Flag unavailable")
with p.open() as f: actual=f.read(129)
if actual!=expected: raise SystemExit("Readiness incident changed")
p.unlink()
print("Known demo readiness flag cleared")
'''
SERVICE_SCRIPT = '''import json,urllib.request
opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
with opener.open("http://opspilot-demo.opspilot.svc.cluster.local:8080/",timeout=5) as r:
    data=json.load(r)
    assert r.status==200 and data.get("application")=="opspilot-demo" and data.get("status")=="healthy"
print("Service HTTP 200 verified")
'''


def run(*args, timeout=15):
    return subprocess.run(COMMAND + list(args), capture_output=True, text=True,
                          check=True, timeout=timeout).stdout


def get(*args):
    return json.loads(run("get", *args, "-o", "json"))


def require(condition, message):
    if not condition:
        raise ValueError(message)


def single_container(obj, template=False):
    spec = obj["spec"]["template"]["spec"] if template else obj["spec"]
    containers = spec.get("containers", [])
    require(len(containers) == 1 and containers[0].get("name") == CONTAINER,
            "Unexpected demo container layout")
    require(not spec.get("initContainers") and not spec.get("ephemeralContainers"),
            "Additional demo containers require manual review")
    return containers[0]


def check_profile(deployment, image=IMAGE, readiness_path="/readyz"):
    metadata, spec = deployment["metadata"], deployment["spec"]
    require(metadata.get("name") == DEPLOYMENT and metadata.get("namespace") == NAMESPACE
            and metadata.get("uid") and not metadata.get("deletionTimestamp"), "Wrong demo deployment")
    require(type(spec.get("replicas")) is int and spec["replicas"] == 1
            and spec.get("strategy", {}).get("type") == "Recreate" and not spec.get("paused"),
            "Expected one-replica Recreate demo")
    require(spec.get("selector", {}).get("matchLabels") == {"app": DEPLOYMENT}
            and not spec.get("selector", {}).get("matchExpressions"), "Unexpected demo selector")
    container = single_container(deployment, template=True)
    require(container.get("image") == image, "Unexpected demo image; do not replace it with Nginx")
    for name, path in (("readinessProbe", readiness_path), ("livenessProbe", "/healthz")):
        probe = container.get(name, {})
        http = probe.get("httpGet", {})
        require(http.get("path") == path and http.get("port") == "http"
                and http.get("scheme", "HTTP") == "HTTP" and not http.get("host")
                and not http.get("httpHeaders") and not probe.get("exec")
                and not probe.get("tcpSocket") and not probe.get("grpc"), "Unexpected " + name)
    require(any(p.get("name") == "http" and p.get("containerPort") == 8080
                for p in container.get("ports", [])), "Expected named HTTP port 8080")
    volumes = {v["name"]: v for v in spec["template"]["spec"].get("volumes", [])}
    mounts = [m for m in container.get("volumeMounts", []) if m.get("mountPath") == "/runtime"]
    require(len(mounts) == 1 and not mounts[0].get("subPath") and not mounts[0].get("readOnly")
            and "emptyDir" in volumes.get(mounts[0].get("name"), {}),
            "Demo flag must live in pod-scoped writable emptyDir")
    return container


def snapshot(ready, image=IMAGE, readiness_path="/readyz"):
    deployment = get("deployment", DEPLOYMENT)
    expected = check_profile(deployment, image, readiness_path)
    pods = get("pods", "-l", "app=" + DEPLOYMENT)["items"]
    require(len(pods) == 1, "Expected exactly one demo Pod; rollout must settle first")
    pod = pods[0]
    require(pod["metadata"].get("namespace") == NAMESPACE and pod["metadata"].get("uid")
            and not pod["metadata"].get("deletionTimestamp"), "Invalid/terminating demo Pod")
    actual = single_container(pod)
    require(all(actual.get(k) == expected.get(k) for k in ("image", "resources", "command", "args", "env", "readinessProbe", "livenessProbe")),
            "Pod and deployment probe/image configuration differ")
    owners = [o for o in pod["metadata"].get("ownerReferences", []) if o.get("controller") is True]
    require(len(owners) == 1 and owners[0].get("kind") == "ReplicaSet", "Unexpected Pod owner")
    rs = get("replicaset", owners[0]["name"])
    require(rs["metadata"]["uid"] == owners[0]["uid"] and any(
        o.get("kind") == "Deployment" and o.get("name") == DEPLOYMENT
        and o.get("uid") == deployment["metadata"]["uid"] and o.get("controller") is True
        for o in rs["metadata"].get("ownerReferences", [])), "Pod is not owned by the demo deployment")
    state = pod.get("status", {})
    containers = state.get("containerStatuses", [])
    require(len(containers) == 1 and containers[0].get("name") == CONTAINER, "Unexpected container status")
    status = containers[0]
    require(state.get("phase") == "Running" and isinstance(status.get("state", {}).get("running"), dict)
            and status.get("ready") is ready and any(
                c.get("type") == "Ready" and c.get("status") == ("True" if ready else "False")
                for c in state.get("conditions", [])), "Pod is not in the expected readiness-only state")
    require(type(status.get("restartCount")) is int and status["restartCount"] >= 0
            and isinstance(status.get("containerID"), str) and status["containerID"]
            and status["state"]["running"].get("startedAt"), "Container identity is unavailable")
    deployment_status = deployment.get("status", {})
    require(deployment_status.get("observedGeneration") == deployment["metadata"]["generation"]
            and deployment_status.get("updatedReplicas") == 1
            and deployment_status.get("readyReplicas", 0) == int(ready)
            and deployment_status.get("availableReplicas", 0) == int(ready), "Deployment state is not settled")
    service = get("service", DEPLOYMENT)
    require(service["metadata"].get("uid") and service["metadata"].get("namespace") == NAMESPACE
            and service["spec"].get("selector") == {"app": DEPLOYMENT}
            and not service["spec"].get("publishNotReadyAddresses")
            and any(p.get("port") == 8080 and p.get("targetPort") == "http"
                    for p in service["spec"].get("ports", [])), "Unexpected demo Service routing")
    slices = get("endpointslices", "-l", "kubernetes.io/service-name=" + DEPLOYMENT)["items"]
    endpoints = []
    for item in slices:
        require(any(o.get("kind") == "Service" and o.get("uid") == service["metadata"]["uid"]
                    for o in item["metadata"].get("ownerReferences", [])), "Unexpected EndpointSlice owner")
        endpoints.extend(item.get("endpoints", []))
    require(bool(endpoints) and all(
        e.get("targetRef", {}).get("uid") == pod["metadata"]["uid"]
        and e.get("targetRef", {}).get("kind") == "Pod"
        and e.get("conditions", {}).get("ready") is ready
        and e.get("conditions", {}).get("terminating", False) is False for e in endpoints),
        "Service endpoints do not confirm the expected demo readiness")
    return deployment, pod, status, service


def get_live_readiness_evidence():
    """Read only. NotReady alone, an AI hypothesis or an old Event is NOT enough."""
    try:
        deployment, pod, status, service = snapshot(False)
        checks = json.loads(run("exec", pod["metadata"]["name"], "-c", CONTAINER,
                                "--", "python", "-c", PROBE_SCRIPT))
        require(checks.get("liveness_status") == 200 and checks.get("liveness_ok") is True
                and checks.get("readiness_status") == 503
                and re.fullmatch(r"[a-f0-9]{32}", checks.get("fault_id") or ""),
                "Known readiness flag plus live HTTP 503/liveness 200 were not confirmed")
        return {"confirmed": True, "reason": "Controlled readiness flag; live readiness 503, liveness 200 and no ready Service endpoint",
                "deployment_uid": deployment["metadata"]["uid"],
                "deployment_generation": deployment["metadata"]["generation"],
                "pod": pod["metadata"]["name"], "pod_uid": pod["metadata"]["uid"],
                "container_id": status["containerID"], "running_since": status["state"]["running"]["startedAt"],
                "restart_count": status["restartCount"], "image": IMAGE,
                "fault_id": checks["fault_id"], "service_uid": service["metadata"]["uid"],
                "readiness_status": 503, "liveness_status": 200, "ready_service_endpoints": 0}
    except (subprocess.SubprocessError, OSError, ValueError, KeyError, TypeError, AttributeError) as error:
        return {"confirmed": False, "reason": "Readiness evidence unavailable or outside demo policy (" + type(error).__name__ + ")"}


def evidence_matches(stored, live):
    return (isinstance(stored, dict) and live.get("confirmed") is True
            and type(stored.get("restart_count")) is int and stored["restart_count"] >= 0
            and type(live.get("restart_count")) is int
            and all(stored.get(k) is not None and stored.get(k) == live.get(k) for k in BINDING))


def claim_notification(fault_id):
    """One automatic approval per injected fault, including uncertain delivery."""
    from api_service.app import approval_store
    require(isinstance(fault_id, str) and re.fullmatch(r"[a-f0-9]{32}", fault_id), "Invalid fault identity")
    with approval_store.connect() as db:
        db.execute("CREATE TABLE IF NOT EXISTS opspilot_readiness_notifications (fault_id TEXT PRIMARY KEY, created_at INTEGER NOT NULL)")
        result = db.execute("INSERT OR IGNORE INTO opspilot_readiness_notifications VALUES (?, ?)",
                            (fault_id, int(time.time())))
        return result.rowcount == 1


def verify_readiness_recovery(expected=None):
    from ai_service.app.remediation_executor import verify_recovery
    rollout = verify_recovery(timeout_seconds=120)
    if not rollout.get("recovered"):
        return {**rollout, "service_http_verified": False}
    try:
        _, pod, _, _ = snapshot(True)
        if expected is not None:
            status = pod["status"]["containerStatuses"][0]
            require(pod["metadata"]["uid"] == expected.get("pod_uid")
                    and status.get("containerID") == expected.get("container_id")
                    and status.get("restartCount") == expected.get("restart_count"),
                    "A replacement/restarted Pod cannot prove this flag repair caused recovery")
        checks = json.loads(run("exec", pod["metadata"]["name"], "-c", CONTAINER,
                                "--", "python", "-c", PROBE_SCRIPT))
        require(checks.get("fault_id") is None and checks.get("readiness_status") == 200
                and checks.get("liveness_status") == 200 and checks.get("liveness_ok") is True,
                "Readiness flag or endpoint did not recover")
        run("exec", pod["metadata"]["name"], "-c", CONTAINER, "--", "python", "-c", SERVICE_SCRIPT)
        return {**rollout, "service_http_verified": True, "readiness_http_status": 200, "ready_service_endpoint_verified": True}
    except (subprocess.SubprocessError, OSError, ValueError, KeyError, TypeError, AttributeError) as error:
        return {"recovered": False, "service_http_verified": False, "reason": "Service/readiness recovery not verified (" + type(error).__name__ + ")"}


def execute_readiness_remediation(incident_id, execute=False):
    from api_service.app.approval_store import get_approval, claim_approved_action
    approval = get_approval(incident_id)
    target = NAMESPACE + "/" + DEPLOYMENT
    def blocked(reason):
        return {"status": "blocked", "reason": reason, "target": target, "action": ACTION}
    if (CONTEXT, NAMESPACE, DEPLOYMENT, CONTAINER, IMAGE) != (
            "opspilot", "opspilot", "opspilot-demo", "opspilot-demo", "opspilot-demo:v1-final-1"):
        return blocked("Readiness target is outside the fixed local demo allowlist")
    if not approval or (approval.get("action"), approval.get("namespace"), approval.get("deployment"),
                        approval.get("incident_type")) != (ACTION, NAMESPACE, DEPLOYMENT, INCIDENT_TYPE):
        return blocked("Readiness action or target not allowlisted")
    if approval.get("status") != "approved" or approval.get("expires_at", 0) <= int(time.time()):
        return blocked("Approval is not approved or has expired")
    try:
        stored = json.loads(approval.get("incident_evidence") or "null")
    except (ValueError, TypeError):
        return blocked("Stored readiness evidence is invalid")
    if not evidence_matches(stored, get_live_readiness_evidence()):
        return blocked("Readiness incident changed after approval")
    if not execute:
        return {"status": "dry_run_passed", "reason": "Approval and live readiness evidence match; no flag cleared",
                "action": ACTION, "target": target}
    if os.getenv(FEATURE) != "1":
        return blocked("Demo readiness repair feature is disabled")
    if claim_approved_action(incident_id) is None:
        return blocked("Approval already used, expired, or invalid")
    if not evidence_matches(stored, get_live_readiness_evidence()):
        return blocked("Readiness incident changed before execution")
    try:
        run("exec", stored["pod"], "-c", CONTAINER, "--", "python", "-c", REPAIR_SCRIPT, stored["fault_id"])
    except (subprocess.SubprocessError, OSError) as error:
        return {"status": "execution_failed", "reason": type(error).__name__, "execution_attempted": True,
                "repair_command_succeeded": False, "restart_command_succeeded": False, "action": ACTION, "target": target}
    recovery = verify_readiness_recovery(expected=stored)
    return {"status": "recovered" if recovery.get("recovered") else "recovery_not_verified",
            "action": ACTION, "target": target, "execution_attempted": True,
            "repair_command_succeeded": True, "restart_command_succeeded": False, "recovery": recovery}
