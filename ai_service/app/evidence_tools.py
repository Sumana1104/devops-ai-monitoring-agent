import subprocess


def run_kubectl(*args):
    result = subprocess.run(
        ["kubectl", *args],
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )

    if result.returncode != 0:
        raise RuntimeError(
            f"kubectl failed: {result.stderr.strip()}"
        )

    return result.stdout


def get_pod_logs(pod_name, namespace="opspilot"):
    return run_kubectl(
        "logs", pod_name,
        "-n", namespace,
        "--tail=80",
    )


def get_pod_events(pod_name, namespace="opspilot"):
    return run_kubectl(
        "get", "events",
        "-n", namespace,
        "--field-selector",
        f"involvedObject.name={pod_name}",
    )


def get_pod_details(pod_name, namespace="opspilot"):
    return run_kubectl(
        "describe", "pod", pod_name,
        "-n", namespace,
    )
