
import os
import subprocess


def get_target():
    namespace = os.getenv("OPSPILOT_NAMESPACE", "opspilot")
    label = os.getenv("OPSPILOT_APP_LABEL", "app=opspilot")
    return namespace, label


def k_get_pods():
    """List only the selected application's pods."""
    namespace, label = get_target()

    result = subprocess.run(
        [
            "kubectl", "get", "pods",
            "-n", namespace,
            "-l", label,
        ],
        capture_output=True,
        text=True,
        check=True,
        timeout=20,
    )

    return result.stdout


def k_describe_pod(pod_name):
    """Describe a pod in the selected namespace."""
    namespace, _ = get_target()

    result = subprocess.run(
        [
            "kubectl", "describe", "pod",
            pod_name, "-n", namespace,
        ],
        capture_output=True,
        text=True,
        check=True,
        timeout=20,
    )

    return result.stdout


def k_logs(pod_name):
    """Read logs from a pod."""
    namespace, _ = get_target()

    result = subprocess.run(
        [
            "kubectl", "logs", pod_name,
            "-n", namespace,
        ],
        capture_output=True,
        text=True,
        check=True,
        timeout=20,
    )

    return result.stdout


def k_restart_deployment(deploy_name):
    """
    Legacy helper. Direct restarts are disabled.

    Remediation must go through the approval
    and safety-checked executor.
    """
    raise RuntimeError(
        "Direct restart disabled. "
        "Use the approved remediation executor."
    )
