"""Small operator commands; never approve an incident or execute remediation."""
import argparse
import json
import os
from pathlib import Path
import re
import time

import requests
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent


def stop(message):
    raise ValueError(message + " No remediation executed by this command.")


def root_alert(entry, history):
    """Follow only recorded operator retries back to the original real event."""
    seen, claimed_events = set(), []
    for _ in range(12):
        event = entry.get("alert")
        if not isinstance(event, dict):
            stop("Recorded alert is missing")
        labels = event.get("labels") or {}
        if labels.get("alertname") != "OpsPilotOperatorRetry":
            from ai_service.app.incident_history import alert_event_key
            key = alert_event_key(event)
            if any(alert_event_key(item) != key for item in claimed_events):
                stop("Retry lineage does not match the original alert")
            return event
        if not str(event.get("source", "")).startswith("operator_requested_retry"):
            stop("Unexpected operator-retry provenance")
        parent = labels.get("retry_of")
        if not isinstance(parent, str) or not re.fullmatch(r"[a-f0-9]{32}", parent) or parent in seen:
            stop("Invalid or cyclic retry lineage")
        seen.add(parent)
        original = event.get("original_alert")
        if not isinstance(original, dict):
            stop("Original alert reference is missing")
        claimed_events.append(original)
        entry = history.get_investigation_for_approval(parent)
        if not entry:
            stop("Recorded parent investigation was not found")
    stop("Retry lineage is too deep; manual review required")


def retry_approval(approval_id, confirmed=False):
    if not confirmed:
        stop("Use --confirm-demo-only to explicitly request an operator retry")
    if not re.fullmatch(r"[a-f0-9]{32}", approval_id):
        stop("Expected a 32-character approval ID")
    from opspilot_integration import bridge, telegram_webhook as webhook
    from ai_service.app import incident_history as history
    from api_service.app import approval_store as store
    from api_service.app.incident_notification import request_incident_approval

    # A typo/cold checkout must not silently create empty incident databases.
    if not Path(store.DB_PATH).is_file() or not history.history_path().is_file():
        stop("Existing approval/history databases are missing")
    prior = store.get_approval(approval_id)
    entry = history.get_investigation_for_approval(approval_id)
    if not prior or not entry:
        stop("Linked approval and investigation were not found")
    readiness_action = prior.get("action") == "clear_demo_readiness_flag"
    oom_action = prior.get("action") == "increase_demo_memory_limit"
    expected_action = "increase_demo_memory_limit" if oom_action else "clear_demo_readiness_flag" if readiness_action else "restart_deployment"
    expected_type = "oomkilled" if oom_action else "readiness_failure" if readiness_action else "application_failure"
    if (prior.get("action"), prior.get("namespace"), prior.get("deployment"),
            prior.get("incident_type")) != (expected_action, "opspilot", "opspilot-demo", expected_type):
        stop("Action or target is outside the demo allowlist")
    expires = prior.get("expires_at")
    if type(expires) is not int or expires > int(time.time()):
        stop("Prior approval is not confirmed expired; no overlapping request allowed")
    outcome = entry.get("outcome") or {}
    pending = (prior.get("status") == "pending" and entry.get("stage") == "awaiting_approval"
               and not outcome)
    blocked = (prior.get("status") == "approved" and entry.get("stage") == "blocked"
               and outcome.get("reason") in {
                   "Incident evidence changed after approval",
                   "Readiness incident changed after approval",
                   "Live OOM incident changed after approval",
                   "No eligible active application crash confirmed"})
    if (not (pending or blocked) or outcome.get("execution_attempted")
            or outcome.get("restart_command_succeeded") or outcome.get("repair_command_succeeded")):
        stop("Only an expired, unexecuted pending/eligible blocked request may be retried")

    response = requests.get("http://127.0.0.1:18008/health", timeout=5)
    response.raise_for_status()
    health = response.json()
    feature = "oom_repair_enabled" if oom_action else "demo_readiness_repair_enabled" if readiness_action else "demo_restart_enabled"
    if (health.get("local_bridge") is not True
            or health.get(feature) is not True
            or health.get("runtime_version") != bridge.RUNTIME_VERSION):
        stop("Running bridge must be the updated version with the incident-specific execution feature enabled")
    # Verify actual callback connectivity BEFORE reserving/issuing a new request.
    target = webhook.call("getWebhookInfo").get("url", "")
    suffix = "/telegram/webhook"
    if not isinstance(target, str) or not target.endswith(suffix):
        stop("Telegram is not configured for the local callback")
    webhook.check_route(target[:-len(suffix)])

    event = root_alert(entry, history)
    before = bridge.verify_current_alert(event)
    saved = json.loads(prior["incident_evidence"])
    if not isinstance(saved, dict):
        stop("Stored evidence is invalid")
    fields = ("pod", "pod_uid", "waiting_reason", "termination_reason", "exit_code")

    inspect = bridge.inspect_crash
    if readiness_action:
        from ai_service.app.readiness_remediation import get_live_readiness_evidence, evidence_matches
        inspect = get_live_readiness_evidence
    elif oom_action:
        from ai_service.app.resource_incidents import inspect_incident, matches
        inspect = inspect_incident

    def same_crash(current):
        if readiness_action:
            return evidence_matches(saved, current)
        if oom_action:
            return matches(saved, current)
        a, b = saved.get("restart_count"), current.get("restart_count")
        return (current.get("confirmed") is True
                and all(saved.get(field) == current.get(field) for field in fields)
                and type(a) is int and type(b) is int and 0 <= a <= b)

    if before["status"] != "firing" or not same_crash(inspect()):
        stop("The original firing alert and original live incident are not both confirmed")
    retry = {
        "source": "operator_requested_retry",
        "original_alert": event,
        "labels": {"alertname": "OpsPilotOperatorRetry", "service": "opspilot-demo",
                   "retry_of": approval_id},
        "startsAt": event.get("startsAt", "unknown"),
        "fingerprint": "operator-retry:" + approval_id,
    }
    investigation = history.reserve_investigation(retry)
    if investigation is None:
        stop("This parent request has already been retried; inspect its recorded outcome")

    print("Collecting fresh evidence and running LangGraph / RAG / Groq...", flush=True)
    arguments = {"resource_kind": "oomkilled"} if oom_action else {}
    report, error = bridge.run_ai_investigation(
        "Explicit operator retry of expired unexecuted approval " + approval_id
        + ". Original Prometheus alert is still firing. Collect fresh evidence. "
        "Do not assume the old diagnosis. No new fault was injected.", investigation, **arguments)
    after = bridge.verify_current_alert(event)
    bridge.save_alert_state(
        investigation, {"alert_verification": {"before_ai": before, "after_ai": after}},
        "investigated" if report else "ai_failed")
    if not report or report.get("safe_action") != expected_action:
        stop("Fresh AI/policy requires manual review (" + str(error or "no eligible proposal") + ")")
    fresh = inspect()
    if after["status"] != "firing" or not same_crash(fresh):
        stop("Live alert or incident changed during investigation")
    result = request_incident_approval(
        action=expected_action, incident_type=expected_type,
        incident_evidence=fresh, investigation_id=investigation,
        summary=(
            "Operator-requested fresh approval; expired parent " + approval_id[:10]
            + "\nOriginal Prometheus alert: currently firing"
            + "\nFresh AI hypothesis: " + report["root_cause"][:600]
            + "\nConfidence: " + str(report["confidence"]) + " | Severity: " + report["severity"]
            + "\nPolicy: " + str(report.get("policy_reason"))
            + "\nRAG: " + str(report.get("rag_status"))))
    print("Fresh Telegram approval sent:", result["incident_id"])
    print("Review ONLY this new request. No approval decision or remediation was executed by this command.")
    return result


def status():
    try:
        response = requests.get("http://127.0.0.1:18008/health", timeout=5)
        response.raise_for_status()
        payload = response.json()
        print("Bridge:", json.dumps({key: payload.get(key) for key in
              ("status", "local_bridge", "runtime_version", "demo_restart_enabled", "demo_readiness_repair_enabled", "oom_repair_enabled", "investigation_trigger", "watcher_issues_approvals")}))
    except (requests.RequestException, ValueError, AttributeError) as error:
        print("Bridge unavailable:", type(error).__name__)
    try:
        from opspilot_integration import telegram_webhook as webhook
        info = webhook.call("getWebhookInfo")
        print("Telegram URL:", info.get("url") or "(none)")
        print("Pending callback deliveries:", info.get("pending_update_count", 0))
        message = str(info.get("last_error_message", "none"))
        for secret in (webhook.token, webhook.secret):
            if secret:
                message = message.replace(secret, "[REDACTED]")
        print("Last Telegram delivery error:", message)
        if info.get("last_error_date"):
            print("Last error age, seconds:", int(time.time() - info["last_error_date"]))
    except SystemExit:
        print("Telegram: missing local configuration")
    except Exception as error:
        print("Telegram status unavailable:", type(error).__name__)


def main():
    load_dotenv(ROOT / ".env", override=False)
    load_dotenv(ROOT / "ai_service/.env", override=False)
    os.environ["OPSPILOT_APPROVAL_DB"] = str(ROOT / "opspilot_approvals.db")
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("status", help="Read active bridge settings and Telegram delivery state")
    refresh = commands.add_parser("webhook", help="Verify and refresh a temporary tunnel, preserving restore backup")
    refresh.add_argument("url")
    retry = commands.add_parser("retry-approval", help="Request a fresh human decision; never restart directly")
    retry.add_argument("approval_id")
    retry.add_argument("--confirm-demo-only", action="store_true", required=True)
    args = parser.parse_args()
    if args.command == "status":
        status()
    elif args.command == "webhook":
        from opspilot_integration.telegram_webhook import refresh_route
        refresh_route(args.url)
        print("PASS: Telegram route verified and updated; restore backup unchanged.")
    else:
        retry_approval(args.approval_id, confirmed=args.confirm_demo_only)


if __name__ == "__main__":
    try:
        main()
    except ValueError as error:
        # Our validation messages contain no provider bodies or credential URLs.
        raise SystemExit("STOP: " + str(error)) from None
    except Exception as error:
        raise SystemExit("STOP: operator command failed (" + type(error).__name__ + "). No recovery confirmed.") from None
