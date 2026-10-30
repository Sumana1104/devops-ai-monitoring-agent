"""Real OOMKilled/FailedScheduling evidence for the existing local app.

Read-only detection does not fabricate a fault. Only an explicitly approved,
bounded memory-limit increase is executable. Scheduling causes require manual
review; placement rules, requests, taints and nodes are never changed here.
"""
import json
import os
import subprocess
import time

CONTEXT = NAMESPACE = "opspilot"
DEPLOYMENT = CONTAINER = "opspilot-demo"
ACTION = "increase_demo_memory_limit"
FEATURE = "OPSPILOT_ENABLE_OOM_REPAIR"
LIMIT_STEPS = {"64Mi": "128Mi", "128Mi": "256Mi", "256Mi": "512Mi"}
COMMAND = ["kubectl", "--context", CONTEXT, "-n", NAMESPACE]
IDENTITY = ("kind", "deployment_uid", "generation", "pod", "pod_uid", "image", "memory_limit", "proposed_memory_limit", "node", "service_uid")
SERVICE_SCRIPT = '''import json,urllib.request
opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
with opener.open("http://opspilot-demo.opspilot.svc.cluster.local:8080/",timeout=5) as r:
    body=json.load(r)
    assert r.status==200 and body.get("application")=="opspilot-demo" and body.get("status")=="healthy"
print(json.dumps({"service_http_status":200}))
'''


def require(condition, message="Recovery conditions not confirmed"):
    if not condition:
        raise ValueError(message)


def run(*args, timeout=20):
    return subprocess.run(COMMAND + list(args), capture_output=True, text=True,
                          check=True, timeout=timeout).stdout


def get(*args):
    return json.loads(run("get", *args, "-o", "json"))


def snapshot():
    deployment = get("deployment", DEPLOYMENT)
    meta, spec = deployment["metadata"], deployment["spec"]
    require(meta.get("name") == DEPLOYMENT and meta.get("namespace") == NAMESPACE
            and meta.get("uid") and not meta.get("deletionTimestamp"), "Unexpected deployment")
    require(type(spec.get("replicas")) is int and spec["replicas"] == 1
            and spec.get("selector", {}).get("matchLabels") == {"app": DEPLOYMENT}
            and not spec.get("selector", {}).get("matchExpressions")
            and spec.get("strategy", {}).get("type") == "Recreate" and not spec.get("paused"), "Unsupported workload layout")
    require(deployment.get("status", {}).get("observedGeneration") == meta.get("generation"), "Deployment update not observed")
    containers = spec["template"]["spec"].get("containers", [])
    require(len(containers) == 1 and containers[0].get("name") == CONTAINER
            and not spec["template"]["spec"].get("initContainers"), "Unsupported container layout")
    expected = containers[0]
    require(expected.get("image") in ("opspilot-demo:integrated-v1", "opspilot-demo:readiness-v1", "opspilot-demo:v1-final-1"), "Image outside local app allowlist")
    pods = get("pods", "-l", "app=" + DEPLOYMENT)["items"]
    require(len(pods) == 1, "Wait for one settled app Pod")
    pod = pods[0]
    require(pod["metadata"].get("namespace") == NAMESPACE and pod["metadata"].get("uid")
            and not pod["metadata"].get("deletionTimestamp"), "Invalid or terminating Pod")
    actual = pod["spec"].get("containers", [])
    require(len(actual) == 1 and not pod["spec"].get("initContainers") and not pod["spec"].get("ephemeralContainers")
            and all(actual[0].get(k) == expected.get(k) for k in ("name", "image", "resources", "command", "args")), "Pod/template mismatch")
    owners = [o for o in pod["metadata"].get("ownerReferences", []) if o.get("controller") is True]
    require(len(owners) == 1 and owners[0].get("kind") == "ReplicaSet", "Unknown Pod owner")
    rs = get("replicaset", owners[0]["name"])
    require(rs["metadata"].get("uid") == owners[0].get("uid") and any(
        o.get("kind") == "Deployment" and o.get("name") == DEPLOYMENT
        and o.get("uid") == meta["uid"] and o.get("controller") is True
        for o in rs["metadata"].get("ownerReferences", [])), "Pod is not owned by the allowlisted deployment")
    return deployment, pod, expected


def inspect_incident():
    """Real current Pod state and UID-bound Events; never an injected flag."""
    try:
        deployment, pod, container = snapshot()
        status = pod.get("status", {})
        if any(c.get("type") == "Ready" and c.get("status") == "True" for c in status.get("conditions", [])):
            return {"confirmed": False, "reason": "Current Pod is Ready; old errors do not establish an active incident"}
        uid = pod["metadata"]["uid"]
        events = get("events", "--field-selector", "involvedObject.uid=" + uid)["items"]
        evidence = {"confirmed": True, "deployment_uid": deployment["metadata"]["uid"],
                    "generation": deployment["metadata"]["generation"],
                    "pod": pod["metadata"]["name"], "pod_uid": uid, "image": container["image"],
                    "node": pod["spec"].get("nodeName"), "repairable": False,
                    "memory_limit": container.get("resources", {}).get("limits", {}).get("memory"),
                    "events": [{k: e.get(k) for k in ("type", "reason", "message")} for e in events[-12:]
                               if e.get("involvedObject", {}).get("uid") == uid]}
        if (status.get("phase") == "Pending" and not pod["spec"].get("nodeName") and any(
                c.get("type") == "PodScheduled" and c.get("status") == "False" and c.get("reason") == "Unschedulable"
                for c in status.get("conditions", [])) and any(e.get("reason") == "FailedScheduling" for e in evidence["events"])):
            return {**evidence, "kind": "failed_scheduling", "reason": "Current Pod is unscheduled; matching FailedScheduling event observed. Cause-specific manual review required."}
        rows = status.get("containerStatuses", [])
        require(len(rows) == 1 and rows[0].get("name") == CONTAINER and rows[0].get("ready") is False, "No current unhealthy container")
        row = rows[0]
        require(row.get("state", {}).get("waiting", {}).get("reason") == "CrashLoopBackOff"
                or row.get("state", {}).get("terminated", {}).get("reason") == "OOMKilled",
                "Old OOM record does not establish the current failure")
        termination = row.get("state", {}).get("terminated") or row.get("lastState", {}).get("terminated", {})
        require(termination.get("reason") == "OOMKilled" and type(termination.get("exitCode")) is int
                and termination["exitCode"] == 137 and type(row.get("restartCount")) is int and row["restartCount"] >= 0,
                "Kubernetes OOMKilled record absent; exit 137 alone is not proof")
        require(bool(pod["spec"].get("nodeName")), "OOM node unknown")
        node = get("node", pod["spec"]["nodeName"])
        memory_pressure = next((c.get("status") for c in node.get("status", {}).get("conditions", []) if c.get("type") == "MemoryPressure"), "Unknown")
        target = LIMIT_STEPS.get(evidence["memory_limit"])
        # Only the three fixed numeric Mi limits above can be changed. No LLM
        # number, arbitrary resource quantity, request or node change is accepted.
        service = get("service", DEPLOYMENT)
        routing_ok = (service.get("metadata", {}).get("namespace") == NAMESPACE
                      and service.get("metadata", {}).get("uid")
                      and service.get("spec", {}).get("selector") == {"app": DEPLOYMENT}
                      and not service["spec"].get("publishNotReadyAddresses")
                      and any(p.get("port") == 8080 and p.get("targetPort") == "http" for p in service["spec"].get("ports", [])))
        repairable = bool(target and memory_pressure == "False" and routing_ok)
        return {**evidence, "kind": "oomkilled", "termination_reason": "OOMKilled", "exit_code": 137,
                "restart_count": row["restartCount"], "proposed_memory_limit": target,
                "service_uid": service.get("metadata", {}).get("uid"),
                "node_memory_pressure": memory_pressure, "repairable": repairable,
                "reason": "Kubernetes confirms an OOMKilled container. A memory leak or limit-related root cause is not independently proven. " +
                          ("A bounded limit increase may be proposed for human review." if repairable else "Limit or node pressure is outside repair policy; manual review required.")}
    except (ValueError, KeyError, TypeError, OSError, subprocess.SubprocessError) as error:
        return {"confirmed": False, "reason": "Live Kubernetes resource evidence unavailable/outside policy (" + type(error).__name__ + ")"}


def matches(stored, live):
    return (isinstance(stored, dict) and stored.get("confirmed") is True and live.get("confirmed") is True
            and stored.get("kind") == "oomkilled" and stored.get("repairable") is True and live.get("repairable") is True
            and all(stored.get(k) is not None and stored[k] == live.get(k) for k in IDENTITY)
            and type(stored.get("restart_count")) is int and type(live.get("restart_count")) is int
            and 0 <= stored["restart_count"] <= live["restart_count"])


def verify_recovery(expected, timeout_seconds=120, stable_seconds=30):
    deadline, identity, since = time.monotonic() + timeout_seconds, None, None
    while time.monotonic() < deadline:
        try:
            deployment, pod, container = snapshot()
            require(deployment["metadata"]["uid"] == expected["deployment_uid"]
                    and deployment["metadata"]["generation"] == expected["generation"] + 1
                    and pod["metadata"]["uid"] != expected["pod_uid"] and container["image"] == expected["image"]
                    and container.get("resources", {}).get("limits", {}).get("memory") == expected["proposed_memory_limit"], "Recovery is not this approved rollout")
            rows = pod.get("status", {}).get("containerStatuses", [])
            require(len(rows) == 1 and rows[0].get("ready") is True and type(rows[0].get("restartCount")) is int
                    and rows[0]["restartCount"] == 0 and rows[0].get("containerID")
                    and any(c.get("type") == "Ready" and c.get("status") == "True" for c in pod.get("status", {}).get("conditions", []))
                    and deployment.get("status", {}).get("readyReplicas") == 1
                    and deployment["status"].get("availableReplicas") == 1, "Pod is not stably Ready")
            service = get("service", DEPLOYMENT)
            require(service["metadata"].get("uid") == expected["service_uid"]
                    and service["spec"].get("selector") == {"app": DEPLOYMENT}
                    and not service["spec"].get("publishNotReadyAddresses"), "Service identity/routing changed")
            slices = get("endpointslices", "-l", "kubernetes.io/service-name=" + DEPLOYMENT)["items"]
            endpoints = []
            for item in slices:
                require(any(o.get("kind") == "Service" and o.get("uid") == expected["service_uid"] for o in item["metadata"].get("ownerReferences", [])), "EndpointSlice owner changed")
                endpoints.extend(item.get("endpoints", []))
            require(endpoints and all(e.get("targetRef", {}).get("uid") == pod["metadata"]["uid"]
                    and e.get("conditions", {}).get("ready") is True and not e.get("conditions", {}).get("terminating") for e in endpoints), "Ready Service endpoint not verified")
            reply = json.loads(run("exec", pod["metadata"]["name"], "-c", CONTAINER, "--", "python", "-c", SERVICE_SCRIPT))
            require(reply == {"service_http_status": 200}, "Service HTTP did not recover")
            current = (pod["metadata"]["uid"], rows[0]["containerID"])
            if current != identity:
                identity, since = current, time.monotonic()
            if time.monotonic() - since >= stable_seconds:
                return {"recovered": True, "service_http_verified": True, "ready_service_endpoint_verified": True,
                        "stable_seconds": stable_seconds, "note": "Short-term recovery only; a memory leak or future recurrence is not ruled out."}
        except (ValueError, KeyError, TypeError, OSError, subprocess.SubprocessError):
            identity, since = None, None
        time.sleep(2)
    return {"recovered": False, "service_http_verified": False, "reason": "Approved rollout, stable readiness and Service HTTP were not all verified"}


def execute_oom_remediation(incident_id, execute=False):
    from api_service.app.approval_store import get_approval, claim_approved_action
    approval = get_approval(incident_id)
    result = {"target": NAMESPACE + "/" + DEPLOYMENT, "action": ACTION}
    def blocked(reason):
        return {**result, "status": "blocked", "reason": reason}
    if (CONTEXT, NAMESPACE, DEPLOYMENT, CONTAINER) != ("opspilot", "opspilot", "opspilot-demo", "opspilot-demo"):
        return blocked("Target outside fixed local app allowlist")
    if not approval or (approval.get("action"), approval.get("namespace"), approval.get("deployment"), approval.get("incident_type")) != (ACTION, NAMESPACE, DEPLOYMENT, "oomkilled"):
        return blocked("Approval action/type/target not allowlisted")
    if approval.get("status") != "approved" or approval.get("expires_at", 0) <= int(time.time()):
        return blocked("Approval is missing, expired, rejected or already used")
    try:
        stored = json.loads(approval.get("incident_evidence") or "null")
    except (ValueError, TypeError):
        return blocked("Stored evidence is invalid")
    if not matches(stored, inspect_incident()):
        return blocked("Live OOM incident changed after approval")
    if not execute:
        return {**result, "status": "dry_run_passed", "reason": "No Kubernetes change; approval remains unused"}
    if os.getenv(FEATURE) != "1":
        return blocked("OOM repair feature is disabled")
    if claim_approved_action(incident_id) is None:
        return blocked("Approval cannot be claimed once")
    try:
        deployment, _, _ = snapshot()
        if not matches(stored, inspect_incident()):
            return blocked("OOM incident changed immediately before execution")
        service = get("service", DEPLOYMENT)
        require(service["metadata"].get("uid") == stored.get("service_uid"), "Service identity changed")
        # Atomic UID/resourceVersion tests reject concurrent edits. Only one
        # memory LIMIT field changes; requests, probes, images and nodes stay intact.
        patch = [{"op": "test", "path": "/metadata/uid", "value": stored["deployment_uid"]},
                 {"op": "test", "path": "/metadata/resourceVersion", "value": deployment["metadata"]["resourceVersion"]},
                 {"op": "test", "path": "/spec/template/spec/containers/0/resources/limits/memory", "value": stored["memory_limit"]},
                 {"op": "replace", "path": "/spec/template/spec/containers/0/resources/limits/memory", "value": stored["proposed_memory_limit"]}]
        require(LIMIT_STEPS.get(stored["memory_limit"]) == stored["proposed_memory_limit"], "Limit step outside fixed ceiling")
        run("patch", "deployment", DEPLOYMENT, "--type=json", "-p", json.dumps(patch))
    except (ValueError, KeyError, TypeError, OSError, subprocess.SubprocessError) as error:
        return {**result, "status": "execution_failed", "execution_attempted": True,
                "repair_command_succeeded": False, "reason": "Guarded patch failed (" + type(error).__name__ + "); no automatic retry"}
    recovery = verify_recovery(stored)
    return {**result, "status": "recovered" if recovery.get("recovered") else "recovery_not_verified",
            "execution_attempted": True, "repair_command_succeeded": True,
            "restart_command_succeeded": False, "recovery": recovery}
