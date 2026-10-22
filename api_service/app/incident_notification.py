
from ai_service.app.remediation_executor import (
    get_live_restart_evidence,
)
from api_service.app.approval_store import (
    create_approval,
    get_approval,
)
from api_service.app.telegram_notifications import (
    send_approval_request,
)


def request_incident_approval(
    action,
    summary,
    incident_type=None,
    incident_evidence=None,
):
    """
    Prepare an approval for a currently verified
    application crash. Does not execute remediation.
    """

    if action != "restart_deployment":
        raise ValueError("Unsupported remediation action")

    if incident_type != "application_failure":
        raise ValueError("Unsupported incident type")

    live = get_live_restart_evidence()

    if not live.get("confirmed"):
        raise ValueError(
            "No active eligible application crash: "
            + live.get("reason", "Unknown")
        )

    if not isinstance(incident_evidence, dict):
        raise ValueError("Incident evidence must be a dictionary")

    # Bind this approval to the observed pod and crash.
    evidence = {
        "pod": live["pod"],
        "waiting_reason": live["waiting_reason"],
        "termination_reason": live["termination_reason"],
        "exit_code": live["exit_code"],
        "restart_count": live["restart_count"],
    }

    incident_id = create_approval(
        action=action,
        namespace="opspilot",
        deployment="opspilot",
        incident_type=incident_type,
        incident_evidence=evidence,
        ttl_seconds=600,
    )

    approval = get_approval(incident_id)

    message_id = send_approval_request(
        incident_id=incident_id,
        action=approval["action"],
        summary=(
            f"{summary}\n"
            f"Pod: {evidence['pod']}\n"
            f"Failure: {evidence['waiting_reason']}\n"
            "Target: deployment/opspilot\n"
            "Namespace: opspilot"
        ),
    )

    return {
        "incident_id": incident_id,
        "telegram_message_id": message_id,
        "status": "pending",
        "evidence": evidence,
    }
