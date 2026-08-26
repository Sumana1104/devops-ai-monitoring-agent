import requests
import os

GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
REPO = os.getenv("GITHUB_REPO")  # example: "suman/myrepo"

headers = {
    "Authorization": f"Bearer {GITHUB_TOKEN}",
    "Accept": "application/vnd.github+json"
}

def gh_latest_run():
    """Get latest GitHub Actions run status."""
    url = f"https://api.github.com/repos/{REPO}/actions/runs?per_page=1"
    r = requests.get(url, headers=headers)
    data = r.json()

    if "workflow_runs" not in data or len(data["workflow_runs"]) == 0:
        return "No runs found."

    run = data["workflow_runs"][0]
    return f"Latest run: {run['status']} | Conclusion: {run['conclusion']}"

def gh_latest_logs():
    """Get logs of latest GitHub Actions run."""
    url = f"https://api.github.com/repos/{REPO}/actions/runs?per_page=1"
    r = requests.get(url, headers=headers)
    data = r.json()

    if "workflow_runs" not in data or len(data["workflow_runs"]) == 0:
        return "No runs found."

    run_id = data["workflow_runs"][0]["id"]
    logs_url = f"https://api.github.com/repos/{REPO}/actions/runs/{run_id}/logs"

    logs = requests.get(logs_url, headers=headers)
    return logs.text

def gh_trigger_workflow():
    """Trigger GitHub Actions workflow dispatch."""
    url = f"https://api.github.com/repos/{REPO}/actions/workflows/deploy.yml/dispatches"
    payload = {"ref": "main"}

    r = requests.post(url, headers=headers, json=payload)

    if r.status_code == 204:
        return "Deployment triggered successfully."
    else:
        return f"Failed to trigger deploy: {r.text}"
