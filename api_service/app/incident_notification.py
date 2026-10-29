
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
    investigation_id=None,
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

    # New graph/operator callers bind the proposal to the exact observed Pod.
    # A normal retry count increase is allowed, but a replacement Pod, a new
    # failure signature or invalid/decreasing counters require a new review.
    if incident_evidence:
        fields = ("pod", "pod_uid", "waiting_reason", "termination_reason", "exit_code")
        previous_count = incident_evidence.get("restart_count")
        current_count = live.get("restart_count")
        if (any(incident_evidence.get(field) != live.get(field) for field in fields)
                or type(previous_count) is not int or type(current_count) is not int
                or not 0 <= previous_count <= current_count):
            raise ValueError("Live incident changed before approval creation")

    # Bind this approval to the observed pod and crash.
    evidence = {
        "pod": live["pod"],
        "pod_uid": live["pod_uid"],
        "waiting_reason": live["waiting_reason"],
        "termination_reason": live["termination_reason"],
        "exit_code": live["exit_code"],
        "restart_count": live["restart_count"],
    }

    incident_id = create_approval(
        action=action,
        namespace="opspilot",
        deployment="opspilot-demo",
        incident_type=incident_type,
        incident_evidence=evidence,
        ttl_seconds=600,
    )

    approval = get_approval(incident_id)

    # Link BEFORE sending Telegram so an immediate button click can record the
    # real investigation/outcome. Legacy manual tests remain possible but are
    # not presented as AI-driven historical incidents.
    if investigation_id:
        from ai_service.app.incident_history import link_approval
        link_approval(investigation_id, incident_id)

    message_id = send_approval_request(
        incident_id=incident_id,
        action=approval["action"],
        summary=(
            f"{summary}\n"
            f"Pod: {evidence['pod']}\n"
            f"Failure: {evidence['waiting_reason']}\n"
            "Target: deployment/opspilot-demo\n"
            "Namespace: opspilot"
        ),
    )

    return {
        "incident_id": incident_id,
        "telegram_message_id": message_id,
        "status": "pending",
        "evidence": evidence,
    }
