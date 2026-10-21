ALLOWED_ACTIONS = {
    "restart_deployment",
    "manual_review",
    "no_action",
}


def select_safe_action(report, pod_healthy):
    recommended = report.get(
        "recommended_action",
        "manual_review"
    )

    if recommended not in ALLOWED_ACTIONS:
        return "manual_review"

    # Never restart a healthy application.
    if recommended == "restart_deployment" and pod_healthy:
        return "manual_review"

    # Block restarts until incident-specific safety rules exist.
    if recommended == "restart_deployment":
        return "manual_review"

    return recommended
