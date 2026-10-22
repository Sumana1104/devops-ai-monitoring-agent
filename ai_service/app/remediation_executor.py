
import json
import subprocess
import time

from api_service.app.approval_store import get_approval


NAMESPACE = "opspilot"
DEPLOYMENT = "opspilot"
APP_LABEL = "app=opspilot"


def get_live_restart_evidence():
    """
    Read current Kubernetes container state.
    Never modify Kubernetes.
    """
    try:
        result = subprocess.run(
            [
                "kubectl", "get", "pods",
                "-n", NAMESPACE,
                "-l", APP_LABEL,
                "-o", "json",
            ],
            capture_output=True,
            text=True,
            check=True,
            timeout=20,
        )

        pods = json.loads(result.stdout).get("items", [])

        if len(pods) != 1:
            return {
                "confirmed": False,
                "reason": "Expected exactly one OpsPilot pod",
            }

        pod = pods[0]
        status = pod.get("status", {})
        containers = status.get("containerStatuses", [])

        if len(containers) != 1:
            return {
                "confirmed": False,
                "reason": "Unexpected container count",
            }

        container = containers[0]
        waiting = container.get(
            "state", {}
        ).get("waiting", {})

        previous = container.get(
            "lastState", {}
        ).get("terminated", {})

        waiting_reason = waiting.get("reason")
        termination_reason = previous.get("reason")
        exit_code = previous.get("exitCode")

        confirmed = (
            waiting_reason == "CrashLoopBackOff"
            and termination_reason == "Error"
            and isinstance(exit_code, int)
            and exit_code != 0
            and container.get("ready") is False
        )

        return {
            "confirmed": confirmed,
            "pod": pod["metadata"]["name"],
            "waiting_reason": waiting_reason,
            "termination_reason": termination_reason,
            "exit_code": exit_code,
            "restart_count": container.get(
                "restartCount", 0
            ),
            "reason": (
                "Active application crash pattern confirmed"
                if confirmed
                else "No eligible active application crash confirmed"
            ),
        }

    except (
        subprocess.SubprocessError,
        OSError,
        ValueError,
        KeyError,
        TypeError,
    ) as error:
        return {
            "confirmed": False,
            "reason": (
                "Kubernetes evidence unavailable: "
                f"{type(error).__name__}"
            ),
        }


def check_remediation_eligibility(incident_id):
    """
    Read-only eligibility assessment.

    Does not claim approval or execute remediation.
    """

    approval = get_approval(incident_id)

    if not approval:
        return {
            "eligible": False,
            "reason": "Approval not found",
        }

    if approval["status"] != "approved":
        return {
            "eligible": False,
            "reason": "Approval is not approved",
        }

    if approval["expires_at"] <= int(time.time()):
        return {
            "eligible": False,
            "reason": "Approval expired",
        }

    if (
        approval["action"] != "restart_deployment"
        or approval["namespace"] != NAMESPACE
        or approval["deployment"] != DEPLOYMENT
    ):
        return {
            "eligible": False,
            "reason": "Action or target not allowlisted",
        }

    if approval.get("incident_type") != "application_failure":
        return {
            "eligible": False,
            "reason": "Incident type not eligible",
        }

    if (
        not approval.get("created_at")
        or not approval.get("incident_evidence")
    ):
        return {
            "eligible": False,
            "reason": "Trusted incident context missing",
        }

    try:
        stored_evidence = json.loads(
            approval["incident_evidence"]
        )
    except (ValueError, TypeError):
        return {
            "eligible": False,
            "reason": "Invalid stored incident evidence",
        }

    if not isinstance(stored_evidence, dict):
        return {
            "eligible": False,
            "reason": "Invalid incident evidence format",
        }

    live = get_live_restart_evidence()

    if not live["confirmed"]:
        return {
            "eligible": False,
            "reason": live["reason"],
            "live_evidence": live,
        }

    # This is a candidate only, not execution permission.
    # The future executor must validate incident binding,
    # claim approval once, and recheck live evidence.

    return {
        "eligible": True,
        "reason": (
            "Active crash confirmed; candidate for "
            "human-reviewed remediation"
        ),
        "live_evidence": live,
    }


def verify_recovery(timeout_seconds=120):
    """
    Check whether the deployment rollout completed.
    Does not modify Kubernetes.
    """
    try:
        rollout = subprocess.run(
            [
                "kubectl", "rollout", "status",
                f"deployment/{DEPLOYMENT}",
                "-n", NAMESPACE,
                f"--timeout={timeout_seconds}s",
            ],
            capture_output=True,
            text=True,
            check=True,
            timeout=timeout_seconds + 10,
        )

        result = subprocess.run(
            [
                "kubectl", "get", "deployment",
                DEPLOYMENT,
                "-n", NAMESPACE,
                "-o", "json",
            ],
            capture_output=True,
            text=True,
            check=True,
            timeout=20,
        )

        deployment = json.loads(result.stdout)
        spec = deployment.get("spec", {})
        status = deployment.get("status", {})

        desired = spec.get("replicas", 1)
        ready = status.get("readyReplicas", 0)
        updated = status.get("updatedReplicas", 0)
        available = status.get("availableReplicas", 0)

        recovered = (
            desired > 0
            and ready == desired
            and updated == desired
            and available == desired
            and status.get("observedGeneration", 0)
            >= deployment["metadata"]["generation"]
        )

        return {
            "recovered": recovered,
            "desired_replicas": desired,
            "ready_replicas": ready,
            "details": rollout.stdout.strip(),
        }

    except (
        subprocess.SubprocessError,
        OSError,
        ValueError,
        KeyError,
        TypeError,
    ) as error:
        return {
            "recovered": False,
            "reason": (
                "Recovery verification failed: "
                f"{type(error).__name__}"
            ),
        }
