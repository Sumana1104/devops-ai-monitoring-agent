"""Fixed-target reads and numeric telemetry; no LLM and no Kubernetes writes."""
import hashlib
import json
import math
import re
import subprocess
import threading
import time
from datetime import datetime, timezone

import requests

TARGET = "opspilot-traffic"
JOB = "opspilot-traffic-load"
IMAGE = "opspilot-traffic:load-v1"
K = ["kubectl", "--context", "opspilot", "-n", "opspilot"]
BOUNDS = ("0.025", "0.05", "0.1", "0.2", "0.4", "0.8", "1.6", "3.2", "6.4", "+Inf")
_lock = threading.Lock()
_cache = {}


def require(value, reason):
    if not value:
        raise ValueError(reason)


def run(*args, timeout=20, input=None):
    result = subprocess.run(K + list(args), check=True, capture_output=True,
                            text=True, input=input, timeout=timeout)
    return result.stdout


def get(*args):
    return json.loads(run("get", *args, "-o", "json"))


def number(value):
    value = float(value)
    require(math.isfinite(value) and value >= 0, "Invalid telemetry number")
    return value


def quantity(value, cpu=False):
    units = {"n": 1e-9, "u": 1e-6, "m": .001} if cpu else {"Ki": 1024, "Mi": 1024**2, "Gi": 1024**3}
    match = re.fullmatch(r"([0-9]+(?:\.[0-9]+)?)([A-Za-z]*)", str(value))
    require(match is not None, "Unknown resource quantity")
    suffix = match[2]
    require(not suffix or suffix in units, "Unsupported resource unit")
    return number(match[1]) * units.get(suffix, 1)


def age(stamp):
    parsed = datetime.fromisoformat(re.sub(r"\.(\d{6})\d+", r".\1", stamp).replace("Z", "+00:00"))
    require(parsed.tzinfo is not None, "Timestamp lacks timezone")
    return datetime.now(timezone.utc).timestamp() - parsed.timestamp()


def fresh(stamp, maximum=60):
    require(-5 <= age(stamp) <= maximum, "Evidence timestamp is stale or in the future")


def capacity():
    node = get("node", "opspilot")
    conditions = {c["type"]: c["status"] for c in node["status"]["conditions"]}
    require(conditions.get("Ready") == "True" and conditions.get("MemoryPressure") == "False"
            and conditions.get("DiskPressure") == "False" and not node["spec"].get("unschedulable"), "Node unavailable/pressured")
    data = json.loads(run("get", "--raw", "/apis/metrics.k8s.io/v1beta1/nodes/opspilot"))
    fresh(data["timestamp"])
    free_cpu = quantity(node["status"]["allocatable"]["cpu"], True) - quantity(data["usage"]["cpu"], True)
    free_memory = quantity(node["status"]["allocatable"]["memory"]) - quantity(data["usage"]["memory"])
    require(free_cpu >= .6 and free_memory >= 256 * 1024**2, "Node needs >=0.6 spare CPU and >=256Mi spare memory")
    return {"spare_cpu": free_cpu, "spare_memory_mib": free_memory / 1024**2}


def deployment(expected_replicas=None):
    d = get("deployment", TARGET)
    m, s = d["metadata"], d["spec"]
    require(d.get("status", {}).get("observedGeneration") == m.get("generation"), "Deployment generation not observed")
    require(m.get("name") == TARGET and m.get("namespace") == "opspilot"
            and m.get("labels", {}).get("opspilot-experiment") == "traffic-v1"
            and m.get("uid") and not m.get("deletionTimestamp"), "Unexpected Deployment")
    require(type(s.get("replicas")) is int and s["replicas"] in (1, 2)
            and (expected_replicas is None or s["replicas"] == expected_replicas)
            and not s.get("paused") and s["selector"] == {"matchLabels": {"app": TARGET}}, "Unsupported replica/layout")
    p = s["template"]["spec"]
    containers = p.get("containers", [])
    require(len(containers) == 1 and not p.get("initContainers")
            and p.get("automountServiceAccountToken") is False, "Unsupported Pod template")
    c = containers[0]
    require(c.get("name") == "traffic" and c.get("image") == IMAGE and not c.get("env")
            and not c.get("command") and not c.get("args")
            and c.get("resources") == {"requests": {"cpu": "250m", "memory": "64Mi"},
                                        "limits": {"cpu": "500m", "memory": "128Mi"}}, "Unexpected image/config/resources")
    for name in ("readinessProbe", "livenessProbe"):
        require(c.get(name, {}).get("httpGet") == {"path": "/healthz", "port": "http", "scheme": "HTTP"}
                or c.get(name, {}).get("httpGet") == {"path": "/healthz", "port": "http"}, "Unexpected health probe")
    require(not any(h.get("spec", {}).get("scaleTargetRef", {}).get("kind") == "Deployment"
                    and h["spec"]["scaleTargetRef"].get("name") == TARGET for h in get("hpa")["items"]), "HPA already owns traffic replicas")
    return d


def load_job():
    j = get("job", JOB)
    require(j["metadata"].get("labels", {}).get("opspilot-experiment") == "traffic-v1"
            and j["status"].get("active") == 1 and not j["status"].get("failed")
            and not j["status"].get("succeeded"), "Locust job is not active")
    c = j["spec"]["template"]["spec"]["containers"]
    require(len(c) == 1 and c[0].get("image") == "locustio/locust:2.32.1"
            and not c[0].get("command") and not c[0].get("env")
            and c[0].get("args") == ["-f", "/load/locustfile.py", "--headless", "--host",
                                    "http://opspilot-traffic.opspilot.svc.cluster.local:8080", "--only-summary"], "Unexpected load configuration")
    load_pods = get("pods", "-l", "job-name=" + JOB)["items"]
    require(len(load_pods) == 1 and any(o.get("kind") == "Job" and o.get("uid") == j["metadata"]["uid"]
                and o.get("controller") is True for o in load_pods[0]["metadata"].get("ownerReferences", [])), "Unexpected load Pod owner")
    logs = run("logs", load_pods[0]["metadata"]["name"], "-c", "locust", "--tail=20", timeout=8)
    samples = [json.loads(line.split("OPSPILOT_LOAD_STATE ", 1)[1]) for line in logs.splitlines() if "OPSPILOT_LOAD_STATE " in line]
    require(bool(samples), "Live Locust state not yet reported")
    sample = samples[-1]
    require(-5 <= time.time() - number(sample["timestamp"]) < 20, "Locust state is stale")
    elapsed = number(sample["elapsed"])
    burst = sample.get("burst") is True and sample.get("users") == 8 and sample.get("state") == "running" and 110 <= elapsed < 1100
    cm = get("configmap", "opspilot-traffic-load")
    from pathlib import Path
    content = Path(__file__).with_name("locustfile.py").read_text()
    require(cm.get("data") == {"locustfile.py": content}, "Load source changed")
    spec = j["spec"]["template"]["spec"]
    require(c[0].get("volumeMounts") == [{"name": "load", "mountPath": "/load", "readOnly": True}]
            and any(v.get("name") == "load" and v.get("configMap", {}).get("name") == JOB
                    for v in spec.get("volumes", [])) and spec.get("automountServiceAccountToken") is False,
            "Unexpected load source mount or identity")
    return {"uid": j["metadata"]["uid"], "burst": burst, "elapsed": elapsed,
            "client_rps": number(sample["client_rps"]),
            "client_error_ratio": number(sample["client_error_ratio"]),
            "client_p95": number(sample["client_p95_ms"]) / 1000,
            "source_hash": hashlib.sha256(content.encode()).hexdigest()}


def snapshot(expected_replicas=None):
    d = deployment(expected_replicas)
    s = get("service", TARGET)
    require(s["metadata"].get("namespace") == "opspilot" and s["metadata"].get("uid")
            and s["spec"].get("selector") == {"app": TARGET}
            and not s["spec"].get("publishNotReadyAddresses")
            and s["spec"].get("type", "ClusterIP") == "ClusterIP"
            and len(s["spec"].get("ports", [])) == 1
            and s["spec"]["ports"][0].get("port") == 8080
            and s["spec"]["ports"][0].get("targetPort") == "http", "Service routing changed")
    pods = get("pods", "-l", "app=" + TARGET)["items"]
    require(len(pods) == d["spec"]["replicas"], "Wait for settled traffic replicas")
    metrics = json.loads(run("get", "--raw", "/apis/metrics.k8s.io/v1beta1/namespaces/opspilot/pods"))["items"]
    result = []
    for p in pods:
        require(p["metadata"].get("namespace") == "opspilot" and not p["metadata"].get("deletionTimestamp")
                and p["spec"].get("nodeName") == "opspilot", "Unexpected/terminating traffic Pod")
        actual = p["spec"].get("containers", [])
        wanted = d["spec"]["template"]["spec"]["containers"][0]
        require(len(actual) == 1 and all(actual[0].get(k) == wanted.get(k)
                for k in ("name", "image", "resources", "command", "args", "env")), "Pod/template mismatch")
        refs = [r for r in p["metadata"].get("ownerReferences", []) if r.get("controller")]
        require(len(refs) == 1 and refs[0].get("kind") == "ReplicaSet", "Unknown Pod owner")
        rs = get("replicaset", refs[0]["name"])
        require(rs["metadata"]["uid"] == refs[0]["uid"] and any(
            r.get("uid") == d["metadata"]["uid"] and r.get("kind") == "Deployment"
            and r.get("controller") is True for r in rs["metadata"].get("ownerReferences", [])), "Wrong controller identity")
        rows = p["status"].get("containerStatuses", [])
        require(len(rows) == 1 and rows[0].get("name") == "traffic" and rows[0].get("ready") is True
                and rows[0].get("restartCount") == 0 and rows[0].get("image") in (IMAGE, "docker.io/library/" + IMAGE)
                and rows[0].get("state", {}).get("running")
                and any(c.get("type") == "Ready" and c.get("status") == "True" for c in p["status"].get("conditions", [])), "Traffic Pod unhealthy/restarting")
        pm = [r for r in metrics if r["metadata"]["name"] == p["metadata"]["name"]]
        require(len(pm) == 1, "Pod resource metrics unavailable")
        fresh(pm[0]["timestamp"])
        require(len(pm[0]["containers"]) == 1, "Unexpected CPU sample")
        cpu = quantity(pm[0]["containers"][0]["usage"]["cpu"], True) / .5
        script = 'import urllib.request; print(urllib.request.urlopen("http://127.0.0.1:8080/telemetry",timeout=3).read().decode())'
        telemetry = json.loads(run("exec", p["metadata"]["name"], "-c", "traffic", "--", "python", "-c", script, timeout=8))
        require(telemetry.get("instance") == p["metadata"]["name"] and set(telemetry.get("counts", {})) == {"200", "503"}
                and len(telemetry.get("buckets", [])) == len(BOUNDS), "Invalid app telemetry")
        values = list(telemetry["counts"].values()) + telemetry["buckets"]
        require(all(type(v) is int and v >= 0 for v in values), "Counters are not nonnegative integers")
        require(telemetry["buckets"] == sorted(telemetry["buckets"])
                and telemetry["buckets"][-1] == sum(telemetry["counts"].values()), "Inconsistent histogram")
        number(telemetry["duration_sum"])
        result.append({"name": p["metadata"]["name"], "uid": p["metadata"]["uid"], "cpu": cpu, "telemetry": telemetry})
    slices = get("endpointslices", "-l", "kubernetes.io/service-name=" + TARGET)["items"]
    endpoint_uids = set()
    for item in slices:
        require(any(r.get("kind") == "Service" and r.get("uid") == s["metadata"]["uid"]
                    for r in item["metadata"].get("ownerReferences", [])), "Unexpected EndpointSlice owner")
        for ep in item.get("endpoints", []):
            require(ep.get("conditions", {}).get("ready") is True and not ep.get("conditions", {}).get("terminating"), "Endpoint not ready")
            endpoint_uids.add(ep.get("targetRef", {}).get("uid"))
    require(endpoint_uids == {p["uid"] for p in result}, "Original Service endpoints do not match ready Pods")
    return {"deployment_uid": d["metadata"]["uid"], "generation": d["metadata"]["generation"],
            "resource_version": d["metadata"]["resourceVersion"], "replicas": d["spec"]["replicas"],
            "template_hash": hashlib.sha256(json.dumps(d["spec"]["template"], sort_keys=True).encode()).hexdigest(),
            "service_uid": s["metadata"]["uid"], "pods": result, "load": load_job(), "observed": time.time()}


def refresh():
    global _cache
    try:
        value = snapshot()
    except (ValueError, KeyError, TypeError, OSError, subprocess.SubprocessError):
        value = {"unavailable": True, "observed": time.time()}
    with _lock:
        _cache = value


def render():
    with _lock:
        value = dict(_cache)
    good = bool(value and not value.get("unavailable") and 0 <= time.time() - value["observed"] < 30)
    lines = ["opspilot_traffic_evidence_available " + str(int(good))]
    if good:
        lines += [f'opspilot_traffic_observed_seconds {value["observed"]}',
                  f'opspilot_traffic_load_burst {int(value["load"]["burst"])}',
                  f'opspilot_traffic_client_p95_seconds {value["load"]["client_p95"]}',
                  f'opspilot_traffic_client_rps {value["load"]["client_rps"]}',
                  f'opspilot_traffic_client_error_ratio {value["load"]["client_error_ratio"]}',
                  f'opspilot_traffic_replicas {value["replicas"]}']
        for p in value["pods"]:
            # Names come from Kubernetes, still escape as Prometheus label text.
            pod = json.dumps(p["name"])
            labels = 'pod=' + pod
            t = p["telemetry"]
            lines.append(f'opspilot_traffic_cpu_fraction{{{labels}}} {p["cpu"]}')
            for status, count in t["counts"].items():
                lines.append(f'opspilot_traffic_responses_total{{{labels},status="{status}"}} {count}')
            for le, count in zip(BOUNDS, t["buckets"]):
                lines.append(f'opspilot_traffic_duration_seconds_bucket{{{labels},le="{le}"}} {count}')
            lines += [f'opspilot_traffic_duration_seconds_sum{{{labels}}} {t["duration_sum"]}',
                      f'opspilot_traffic_duration_seconds_count{{{labels}}} {sum(t["counts"].values())}']
    return '\n'.join(lines) + '\n'


def query(expr):
    r = requests.get("http://127.0.0.1:9090/api/v1/query", params={"query": expr}, timeout=5)
    r.raise_for_status()
    payload = r.json()
    require(payload.get("status") == "success" and payload["data"]["resultType"] == "vector"
            and len(payload["data"]["result"]) == 1, "Monitoring query missing/ambiguous")
    row = payload["data"]["result"][0]["value"]
    require(-5 <= time.time() - number(row[0]) < 15, "Stale query evaluation")
    return number(row[1])


def measurements():
    require(query("opspilot_traffic_evidence_available") == 1
            and 0 <= time.time() - query("opspilot_traffic_observed_seconds") < 30, "Exporter data unavailable/stale")
    return {"p95": query('histogram_quantile(0.95, sum by (le) (rate(opspilot_traffic_duration_seconds_bucket[1m])))'),
            "rps": query('sum(rate(opspilot_traffic_responses_total[1m]))'),
            "error_ratio": query('sum(rate(opspilot_traffic_responses_total{status="503"}[1m])) / clamp_min(sum(rate(opspilot_traffic_responses_total[1m])), 0.001)'),
            "cpu": query('max(opspilot_traffic_cpu_fraction)')}


def firing(alert):
    require(alert.get("status") == "firing" and alert.get("labels", {}).get("alertname") == "OpsPilotTrafficOverload"
            and alert["labels"].get("service") == TARGET, "Wrong traffic alert")
    r = requests.get("http://127.0.0.1:9090/api/v1/alerts", timeout=5)
    r.raise_for_status()
    data = r.json()
    require(data.get("status") == "success", "Alert verification unavailable")
    matching = [x for x in data["data"]["alerts"] if x.get("state") == "firing"
                and all(alert["labels"].get(k) == v for k, v in x["labels"].items())
                and x["labels"].get("alertname") == "OpsPilotTrafficOverload"
                and x["labels"].get("service") == TARGET]
    require(len(matching) == 1, "Original alert no longer firing")
    # Prometheus activeAt starts at pending; notifier startsAt can be FiredAt
    # after the rule's hold duration. They need not be equal. An old notifier
    # event must not predate a newer activation, and future times fail closed.
    active_at = matching[0]["activeAt"]
    require(-5 <= age(alert["startsAt"]) <= age(active_at) + 5, "Alert belongs to an older/future activation")
    return active_at


def eligible(alert):
    active_at = firing(alert)
    current = snapshot(1)
    current["alert_active_at"] = active_at
    require(current["load"]["burst"], "Locust is not in the sustained burst window")
    require(current["load"]["client_rps"] > 1 and current["load"]["client_p95"] > .4,
            "Client latency/rate does not support overload")
    current["metrics"] = measurements()
    m = current["metrics"]
    require(m["p95"] > .4 and m["rps"] > 1 and m["cpu"] > .65, "Overload not currently supported by measured evidence")
    current["capacity"] = capacity()
    return current


def same(before, after):
    keys = ("deployment_uid", "generation", "template_hash", "service_uid", "replicas", "alert_active_at")
    return (all(before.get(k) is not None and before[k] == after.get(k) for k in keys)
            and before["load"]["uid"] == after["load"]["uid"]
            and before["load"]["source_hash"] == after["load"]["source_hash"]
            and {p["uid"] for p in before["pods"]} == {p["uid"] for p in after["pods"]})
