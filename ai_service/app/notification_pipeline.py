
from ai_service.app.incident_dispatcher import (
    prepare_incident_notification,
)
from api_service.app.telegram_notifications import (
    send_manual_review_notification,
)


def notify_incident(analysis_json, pod_healthy=None):
    """
    Process the AI report through the safety policy.

    Send manual-review notifications when required.
    Never execute Kubernetes remediation here.
    """

    incident = prepare_incident_notification(
        analysis_json,
        pod_healthy=pod_healthy,
    )

    print("Current health:", incident["health_status"])
    print("AI recommendation:", incident["ai_recommended_action"])
    print("Safety decision:", incident["safe_action"])

    if incident["requires_manual_review"]:
        message_id = send_manual_review_notification(
            incident
        )

        return {
            "status": "manual_review_notified",
            "message_id": message_id,
            "incident": incident,
        }

    return {
        "status": "no_remediation_requested",
        "incident": incident,
    }
