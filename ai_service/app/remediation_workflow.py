"""
OpsPilot V1: Local remediation workflow.

Runs the existing guarded executor and reports its result
to Telegram. Does not bypass approval or safety checks.
"""

from ai_service.app.remediation_executor import (
    execute_approved_remediation,
)
from api_service.app.telegram_notifications import (
    send_remediation_result_notification,
)


def process_approved_incident(incident_id, execute=False):
    """
    Process one approved incident.

    execute=False: validate only.
    execute=True: request execution through the guarded
                  remediation executor.
    """
    result = execute_approved_remediation(
        incident_id,
        execute=execute,
    )

    notification = {
        "sent": False,
        "message_id": None,
    }

    try:
        message_id = send_remediation_result_notification(
            incident_id,
            result,
        )
        notification = {
            "sent": True,
            "message_id": message_id,
        }
    except Exception as error:
        notification = {
            "sent": False,
            "error": type(error).__name__,
        }

    return {
        "incident_id": incident_id,
        "remediation": result,
        "notification": notification,
    }
