from ai_service.app.incident_dispatcher import (
    prepare_incident_notification,
)
from api_service.app.telegram_notifications import (
    send_manual_review_notification,
)


def notify_incident(analysis_json, pod_healthy):
    incident = prepare_incident_notification(
        analysis_json,
        pod_healthy,
    )

    if incident["requires_manual_review"]:
        message_id = send_manual_review_notification(incident)

        return {
            "status": "manual_review_notified",
            "message_id": message_id,
        }

    return {
        "status": "no_remediation_requested",
    }
