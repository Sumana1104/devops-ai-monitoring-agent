"""Route real Kubernetes resource incidents through the existing AI/approval flow.

No workload creation, fake alerts, fault injection or automatic approval.
One automatic investigation per Pod UID/type prevents repeat restart noise.
"""
import argparse
import json
import os
from pathlib import Path


def process_current_incident(candidate=None, alert=None):
    from ai_service.app.resource_incidents import ACTION, inspect_incident, matches
    from ai_service.app.incident_history import reserve_investigation, redact
    from opspilot_integration import bridge
    if (not isinstance(alert, dict) or alert.get("status") != "firing"
            or (alert.get("labels") or {}).get("alertname") not in
            {"OpsPilotDemoOOMKilled", "OpsPilotDemoFailedScheduling"}):
        return {"status": "metric_trigger_required", "reason": "A real firing Prometheus resource alert is required; no investigation started"}
    before = bridge.verify_current_alert(alert)
    if before.get("status") != "firing":
        return {"status": "alert_not_current", "reason": before.get("reason")}
    live = candidate if candidate is not None else inspect_incident()
    if live.get("confirmed") is not True:
        return {"status": "no_current_resource_incident", "reason": live.get("reason")}
    kind = live["kind"]
    if kind not in ("oomkilled", "failed_scheduling"):
        raise ValueError("Unknown Kubernetes incident kind")
    expected = {"oomkilled": "OpsPilotDemoOOMKilled", "failed_scheduling": "OpsPilotDemoFailedScheduling"}[kind]
    if alert["labels"]["alertname"] != expected:
        return {"status": "incident_changed", "reason": "Metric alert and current Kubernetes incident type differ"}
    # Bind to the REAL Prometheus event, retaining its labels/timestamps. A
    # second rule activation for the same ongoing Pod failure cannot spam AI.
    investigation_id = reserve_investigation(alert)
    if investigation_id is None:
        return {"status": "already_investigated", "reason": "One automatic investigation per Pod UID/type; no duplicate approval sent"}
    from api_service.app.approval_store import connect
    with connect() as db:
        db.execute("CREATE TABLE IF NOT EXISTS opspilot_resource_episodes (pod_uid TEXT NOT NULL, kind TEXT NOT NULL, investigation_id TEXT NOT NULL, PRIMARY KEY(pod_uid,kind))")
        claimed = db.execute("INSERT OR IGNORE INTO opspilot_resource_episodes VALUES (?,?,?)",
                             (live["pod_uid"], kind, investigation_id)).rowcount == 1
    if not claimed:
        bridge.save_alert_state(investigation_id, {}, "ignored_duplicate_resource_episode")
        return {"status": "already_investigated", "reason": "Existing same-Pod resource episode; no duplicate approval sent"}
    message = ("Actual Kubernetes " + kind + " on opspilot/opspilot-demo, Pod " + live["pod"] +
               ". Diagnose only the current collected evidence. A memory leak or scheduler cause must not be invented.")
    analysis, error = bridge.run_ai_investigation(message, investigation_id, resource_kind=kind)
    after = bridge.verify_current_alert(alert)
    checks = {"before_ai": before, "after_ai": after}
    bridge.save_alert_state(investigation_id, {"alert_verification": checks}, "investigated" if analysis else "ai_failed")
    if after.get("status") != "firing":
        bridge.save_alert_state(investigation_id, {"alert_verification": checks}, "ignored_changed_alert")
        return {"status": "alert_not_current", "investigation_id": investigation_id}
    if not analysis:
        bridge.save_alert_state(investigation_id, {"incident": message, "analysis": error}, "ai_failed")
        bridge.telegram_text("OpsPilot Kubernetes investigation " + investigation_id[:10] +
                             " failed (" + str(error) + "). Manual review required; no approval or repair issued.")
        return {"status": "ai_failed", "investigation_id": investigation_id}
    current = inspect_incident()
    if (current.get("confirmed") is not True or current.get("kind") != kind
            or current.get("pod_uid") != live["pod_uid"]):
        bridge.save_alert_state(investigation_id, {}, "ignored_changed_incident")
        return {"status": "incident_changed", "investigation_id": investigation_id}
    details = redact(
        f"Actual Kubernetes incident: {kind}\nInvestigation: {investigation_id}\n"
        f"AI hypothesis: {analysis.get('root_cause', 'Unconfirmed')[:600]}\n"
        f"Confidence: {analysis.get('confidence')} | Severity: {analysis.get('severity')}\n"
        f"Policy: {analysis.get('policy_reason')}\n"
        "Trigger: real firing Prometheus metric alert. Evidence: current Kubernetes state/events. "
        "An operator-induced test must not be represented as a spontaneous production incident.")
    if (kind == "oomkilled" and analysis.get("safe_action") == ACTION
            and matches(analysis.get("resource_evidence"), current)):
        from api_service.app.incident_notification import request_incident_approval
        # The persistent event reservation stays reserved on uncertain delivery;
        # rerunning/restarting cannot produce duplicate approvals for this Pod.
        result = request_incident_approval(ACTION, details, incident_type="oomkilled",
                                          incident_evidence=current, investigation_id=investigation_id)
        return {"status": "awaiting_approval", "incident_id": result["incident_id"], "investigation_id": investigation_id}
    bridge.telegram_text(details + "\nManual review required. No remediation approval issued; no Kubernetes changes made.")
    bridge.save_alert_state(investigation_id, {}, "manual_review")
    return {"status": "manual_review", "investigation_id": investigation_id}


def main():
    from dotenv import load_dotenv
    from ai_service.app.resource_incidents import inspect_incident
    from ai_service.app.incident_history import redact
    root = Path(__file__).resolve().parents[1]
    load_dotenv(root / ".env", override=False)
    load_dotenv(root / "ai_service/.env", override=False)
    os.environ["PATH"] = str(Path.home() / ".local/bin") + os.pathsep + os.environ.get("PATH", "")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("status", "investigate"))
    args = parser.parse_args()
    if args.command == "status":
        value = inspect_incident()
    else:
        # An explicit retry may use an existing firing real metric event, but
        # never fabricate /alerts or bypass the metric requirement.
        import requests
        response = requests.get(os.getenv("OPSPILOT_PROMETHEUS_URL", "http://127.0.0.1:9090").rstrip("/") + "/api/v1/alerts", timeout=5)
        response.raise_for_status()
        payload = response.json()
        if payload.get("status") != "success":
            raise ValueError("Current metric alerts unavailable")
        events = [a for a in payload.get("data", {}).get("alerts", []) if a.get("state") == "firing"
                  and a.get("labels", {}).get("service") == "opspilot-demo"
                  and a["labels"].get("alertname") in {"OpsPilotDemoOOMKilled", "OpsPilotDemoFailedScheduling"}]
        value = process_current_incident(alert={**events[0], "status": "firing"}) if len(events) == 1 else {"status": "metric_trigger_required"}
    print(redact(json.dumps(value, indent=2)))


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        raise SystemExit("STOP: Kubernetes incident inspection failed (" + type(error).__name__ + "). No recovery confirmed.") from None
