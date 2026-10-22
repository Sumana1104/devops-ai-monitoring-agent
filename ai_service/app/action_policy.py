
import json
import subprocess


ALLOWED_ACTIONS = {
    "restart_deployment",
    "manual_review",
    "no_action",
}


def get_pod_health():
    """
    Check current Kubernetes health separately
    from historical container restarts.
    """

    try:
        result = subprocess.run(
            [
                "kubectl", "get", "pods",
                "-n", "opspilot",
                "-l", "app=opspilot",
                "-o", "json",
            ],
            capture_output=True,
            text=True,
            check=True,
            timeout=20,
        )

        pods = json.loads(result.stdout).get("items", [])

        if not pods:
            return {
                "status": "unknown",
                "reason": "No OpsPilot pods found",
                "pods_checked": 0,
                "historical_restarts": 0,
                "requires_review": True,
            }

        problems = []
        restart_history = []
        total_restarts = 0

        for pod in pods:
            name = pod["metadata"]["name"]
            status = pod.get("status", {})
            phase = status.get("phase", "Unknown")

            ready = any(
                condition.get("type") == "Ready"
                and condition.get("status") == "True"
                for condition in status.get("conditions", [])
            )

            containers = status.get(
                "containerStatuses", []
            )

            containers_ready = (
                bool(containers)
                and all(
                    container.get("ready") is True
                    and container.get("state", {}).get("running")
                    is not None
                    for container in containers
                )
            )

            if (
                phase != "Running"
                or not ready
                or not containers_ready
            ):
                problems.append(
                    f"{name}: phase={phase}, "
                    f"ready={ready}, "
                    f"containers_ready={containers_ready}"
                )

            for container in containers:
                count = container.get("restartCount", 0)
                total_restarts += count

                if count > 0:
                    previous = container.get(
                        "lastState", {}
                    ).get("terminated", {})

                    reason = previous.get(
                        "reason", "Unknown"
                    )
                    exit_code = previous.get("exitCode")

                    restart_history.append(
                        f"{name}/{container.get('name')}: "
                        f"{count} restarts, "
                        f"last reason={reason}, "
                        f"exit code={exit_code}"
                    )

        if problems:
            return {
                "status": "unhealthy",
                "reason": "; ".join(problems),
                "pods_checked": len(pods),
                "historical_restarts": total_restarts,
                "requires_review": True,
            }

        if restart_history:
            return {
                "status": "healthy",
                "reason": (
                    "All current pods are Running and Ready. "
                    "Historical restart evidence: "
                    + "; ".join(restart_history)
                ),
                "pods_checked": len(pods),
                "historical_restarts": total_restarts,
                "requires_review": True,
            }

        return {
            "status": "healthy",
            "reason": (
                "All matching pods are Running and Ready, "
                "with no recorded container restarts"
            ),
            "pods_checked": len(pods),
            "historical_restarts": 0,
            "requires_review": False,
        }

    except (
        subprocess.SubprocessError,
        OSError,
        ValueError,
        KeyError,
        TypeError,
    ) as error:
        return {
            "status": "unknown",
            "reason": (
                "Unable to verify Kubernetes health: "
                f"{type(error).__name__}"
            ),
            "pods_checked": 0,
            "historical_restarts": 0,
            "requires_review": True,
        }


def select_safe_action(report, pod_healthy):
    """
    Never execute remediation automatically.
    Require review for historical abnormal events.
    """

    recommended = report.get(
        "recommended_action",
        "manual_review",
    )

    if isinstance(pod_healthy, dict):
        health_status = pod_healthy.get(
            "status", "unknown"
        )
        requires_review = pod_healthy.get(
            "requires_review", True
        )
    else:
        health_status = (
            "healthy" if pod_healthy is True
            else "unhealthy" if pod_healthy is False
            else "unknown"
        )
        requires_review = True

    if recommended not in ALLOWED_ACTIONS:
        return "manual_review"

    # Remediation execution is not enabled yet.
    if recommended == "restart_deployment":
        return "manual_review"

    # Unhealthy or unverified Kubernetes state.
    if health_status != "healthy":
        return "manual_review"

    # Healthy now, but historical failures need review.
    if requires_review:
        return "manual_review"

    return recommended
