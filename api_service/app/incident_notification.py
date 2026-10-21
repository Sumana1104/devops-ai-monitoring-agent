
from api_service.app.approval_store import create_approval
from api_service.app.telegram_notifications import send_approval_request


def request_incident_approval(action, summary):
    # Step 1: Create pending approval in SQLite.
    incident_id = create_approval(action)

    # Step 2: Send Telegram notification with approval buttons.
    message_id = send_approval_request(
        incident_id=incident_id,
        action=action,
        summary=summary,
    )

    # Step 3: Return IDs for tracking.
    return {
        "incident_id": incident_id,
        "telegram_message_id": message_id,
        "status": "pending",
    }
