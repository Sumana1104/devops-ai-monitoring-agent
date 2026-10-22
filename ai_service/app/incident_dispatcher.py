
import json

from ai_service.app.action_policy import (
    get_pod_health,
    select_safe_action,
)


def prepare_incident_notification(
    analysis_json,
    pod_healthy=None,
):
    """
    Combine AI analysis with Kubernetes health
    and enforce the remediation safety policy.
    """

    report = json.loads(analysis_json)

    # By default, verify live Kubernetes health.
    # Preserve the optional argument for compatibility
    # with existing callers and tests.
    if pod_healthy is None:
        health = get_pod_health()
    elif isinstance(pod_healthy, dict):
        health = pod_healthy
    else:
        health = {
            "status": (
                "healthy"
                if pod_healthy
                else "unhealthy"
            ),
            "reason": "Health supplied by caller",
            "pods_checked": None,
        }

    safe_action = select_safe_action(
        report,
        health,
    )

    return {
        "root_cause": report["root_cause"],
        "severity": report["severity"],
        "confidence": report["confidence"],
        "health_status": health["status"],
        "health_reason": health["reason"],
        "pods_checked": health.get(
            "pods_checked"
        ),
        "ai_recommended_action": report.get(
            "recommended_action"
        ),
        "safe_action": safe_action,
        "requires_manual_review": (
            safe_action == "manual_review"
        ),
    }
