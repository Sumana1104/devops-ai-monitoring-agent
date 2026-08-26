import subprocess

def k_get_pods():
    """Return list of pods."""
    result = subprocess.run(
        ["kubectl", "get", "pods", "-A"],
        capture_output=True, text=True
    )
    return result.stdout

def k_describe_pod(pod_name):
    """Describe a pod."""
    result = subprocess.run(
        ["kubectl", "describe", "pod", pod_name],
        capture_output=True, text=True
    )
    return result.stdout

def k_logs(pod_name):
    """Get logs of a pod."""
    result = subprocess.run(
        ["kubectl", "logs", pod_name],
        capture_output=True, text=True
    )
    return result.stdout

def k_restart_deployment(deploy_name):
    """Restart a deployment."""
    result = subprocess.run(
        ["kubectl", "rollout", "restart", f"deployment/{deploy_name}"],
        capture_output=True, text=True
    )
    return result.stdout
