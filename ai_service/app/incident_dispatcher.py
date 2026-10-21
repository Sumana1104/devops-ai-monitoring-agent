import json

from ai_service.app.action_policy import select_safe_action


def prepare_incident_notification(analysis_json, pod_healthy):
    report = json.loads(analysis_json)

    action = select_safe_action(report, pod_healthy)

    return {
        "root_cause": report["root_cause"],
        "severity": report["severity"],
        "confidence": report["confidence"],
        "safe_action": action,
        "requires_manual_review": action == "manual_review",
    }
