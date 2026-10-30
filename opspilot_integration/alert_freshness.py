"""Read-only current-alert checks. Never asks an LLM or changes Kubernetes."""
from datetime import datetime
import os
import re
import time

import requests

SUPPORTED_ALERTS = frozenset({
    "OpsPilotDemoMetricsDown", "OpsPilotHighErrorRate", "OpsPilotHighLatency",
    "OpsPilotDemoReadinessFailure",
    "OpsPilotDemoOOMKilled", "OpsPilotDemoCrashLoopBackOff", "OpsPilotDemoFailedScheduling",
})
MAX_SCRAPE_AGE_SECONDS = 30


def verify_current_alert(alert, now=None):
    """Return firing/inactive/unknown; unavailable monitoring is NOT recovery.

    For MetricsDown, also check the actual target's last scrape attempt. A
    healthy Kubernetes pod alone cannot prove that Prometheus can scrape it.
    Error-rate/latency alerts are NOT cleared merely because scraping works.
    """
    checked_at = time.time() if now is None else now

    def result(status, reason):
        return {"status": status, "reason": reason, "checked_at": checked_at}

    if alert.get("status") == "resolved":
        return result("inactive", "Alertmanager explicitly marked the event resolved")
    labels = alert.get("labels") or {}
    if (not isinstance(labels, dict)
            or not all(isinstance(k, str) and isinstance(v, str) for k, v in labels.items())
            or labels.get("alertname") not in SUPPORTED_ALERTS
            or labels.get("service") != "opspilot-demo" or alert.get("status") != "firing"):
        return result("unknown", "Unexpected alert identity or state")

    base = os.getenv("OPSPILOT_PROMETHEUS_URL", "http://127.0.0.1:9090").rstrip("/")

    def get(path):
        response = requests.get(base + path, timeout=5)
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict) or payload.get("status") != "success":
            raise ValueError("Invalid monitoring response")
        data = payload.get("data")
        if not isinstance(data, dict):
            raise ValueError("Invalid monitoring data")
        return data

    try:
        active = get("/api/v1/alerts").get("alerts")
        if not isinstance(active, list) or any(
                not isinstance(row, dict) or not isinstance(row.get("labels"), dict)
                or not isinstance(row["labels"].get("alertname"), str)
                or not all(isinstance(k, str) and isinstance(v, str) for k, v in row["labels"].items())
                or row.get("state") not in {"pending", "firing"} for row in active):
            raise ValueError("Invalid active-alert list")
        # External labels may be added by the notifier. Every label in the
        # actual Prometheus alert must match; never match by alertname alone.
        matching = [row for row in active if row["state"] == "firing"
                    and all(labels.get(k) == v for k, v in row["labels"].items())
                    and row["labels"].get("alertname") == labels["alertname"]
                    and row["labels"].get("service") == "opspilot-demo"]
        if not matching:
            return result("inactive", "No matching firing alert exists in current Prometheus state")

        if labels["alertname"] != "OpsPilotDemoMetricsDown":
            return result("firing", "Matching Prometheus rule is currently firing")

        if (labels.get("job") != "opspilot-demo" or not isinstance(labels.get("instance"), str)
                or not labels["instance"]):
            return result("unknown", "Metrics-down event lacks the expected target identity")
        targets = get("/api/v1/targets?state=active").get("activeTargets")
        if not isinstance(targets, list):
            raise ValueError("Invalid target list")
        targets = [row for row in targets if isinstance(row, dict)
                   and isinstance(row.get("labels"), dict)
                   and row["labels"].get("job") == "opspilot-demo"
                   and row["labels"].get("instance") == labels["instance"]]
        if len(targets) != 1:
            return result("unknown", "Expected exactly one matching active scrape target")
        target = targets[0]
        # lastScrape is an actual scrape timestamp, unlike an instant-query
        # evaluation timestamp, which alone cannot establish sample freshness.
        stamp = target.get("lastScrape")
        if not isinstance(stamp, str):
            raise ValueError("Missing scrape timestamp")
        # Prometheus emits RFC3339 nanoseconds; Python 3.10's parser accepts
        # fewer fractional digits. Normalize precision without changing the zone.
        stamp = re.sub(r"\.(\d+)(?=Z|[+-]\d{2}:\d{2}$)",
                       lambda match: "." + match.group(1)[:6].ljust(6, "0"), stamp)
        parsed = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            raise ValueError("Scrape timestamp lacks timezone")
        age = checked_at - parsed.timestamp()
        if age < -5 or age > MAX_SCRAPE_AGE_SECONDS:
            return result("unknown", "Scrape evidence is stale or its clock is inconsistent")
        if target.get("health") == "up":
            return result("inactive", "The matching target has a fresh successful scrape")
        if target.get("health") == "down":
            return result("firing", "Matching rule is firing and a fresh scrape attempt failed")
        return result("unknown", "Scrape target health is unknown")
    except (requests.RequestException, ValueError, TypeError, KeyError, OverflowError) as error:
        # Do not include raw URLs, response bodies or lastError in diagnostics.
        return result("unknown", "Monitoring verification unavailable (" + type(error).__name__ + ")")
