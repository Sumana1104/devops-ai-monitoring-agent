import subprocess

# ---------------------------------------------------------
# PART A — THE ENGINE
# ---------------------------------------------------------
# This function runs ANY kubectl command.
# All other Kubernetes tools depend on this.
# ---------------------------------------------------------

def run_kubectl(command: str):
    try:
        result = subprocess.run(
            ["kubectl"] + command.split(),
            capture_output=True,
            text=True
        )
        if result.returncode != 0:
            return f"❌ Error:\n{result.stderr}"
        return f"✅ Output:\n{result.stdout}"
    except Exception as e:
        return f"❌ Exception: {str(e)}"


# ---------------------------------------------------------
# PART B — POD TOOLS (Topic: Pod Operations)
# ---------------------------------------------------------
# These are the basic pod functions your bot will use.
# ---------------------------------------------------------

def k_get_pods(namespace="default"):
    return run_kubectl(f"get pods -n {namespace}")


def k_describe_pod(pod, namespace="default"):
    return run_kubectl(f"describe pod {pod} -n {namespace}")


def k_logs(pod, namespace="default"):
    return run_kubectl(f"logs {pod} -n {namespace}")


def k_logs_follow(pod, namespace="default"):
    return run_kubectl(f"logs {pod} -n {namespace} -f")


def k_delete_pod(pod, namespace="default"):
    return run_kubectl(f"delete pod {pod} -n {namespace}")


# ---------------------------------------------------------
# PART C — DEPLOYMENT BASIC TOOL
# ---------------------------------------------------------
# Restarting a deployment is the correct way to restart
# a microservice (instead of restarting pods directly).
# ---------------------------------------------------------

def k_restart_deployment(deployment, namespace="default"):
    return run_kubectl(f"rollout restart deployment {deployment} -n {namespace}")
