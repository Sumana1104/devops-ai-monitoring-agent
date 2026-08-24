import requests
import os

GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
GITHUB_REPO = os.getenv("GITHUB_REPO")  # example: "suman/myrepo"

headers = {
    "Authorization": f"Bearer {GITHUB_TOKEN}",
    "Accept": "application/vnd.github+json"
}

# ---------------------------------------------------------
# Get latest workflow run (build/deploy)
# ---------------------------------------------------------

def gh_latest_run():
    url = f"https://api.github.com/repos/{GITHUB_REPO}/actions/runs?per_page=1"
    r = requests.get(url, headers=headers)

    if r.status_code != 200:
        return f"❌ Error fetching runs: {r.text}"

    run = r.json()["workflow_runs"][0]
    status = run["status"]
    conclusion = run["conclusion"]
    html_url = run["html_url"]

    return (
        f"🔧 Latest GitHub Actions Run\n"
        f"Status: {status}\n"
        f"Conclusion: {conclusion}\n"
        f"URL: {html_url}"
    )

# ---------------------------------------------------------
# Get logs for latest run
# ---------------------------------------------------------

def gh_latest_logs():
    url = f"https://api.github.com/repos/{GITHUB_REPO}/actions/runs?per_page=1"
    r = requests.get(url, headers=headers)

    if r.status_code != 200:
        return f"❌ Error fetching runs: {r.text}"

    run = r.json()["workflow_runs"][0]
    logs_url = run["logs_url"]

    logs = requests.get(logs_url, headers=headers)

    if logs.status_code != 200:
        return f"❌ Error fetching logs: {logs.text}"

    return f"📄 Logs downloaded (raw text):\n\n{logs.text}"

# ---------------------------------------------------------
# Trigger workflow dispatch (redeploy)
# ---------------------------------------------------------

def gh_trigger_workflow(workflow_file="deploy.yml", branch="main"):
    url = f"https://api.github.com/repos/{GITHUB_REPO}/actions/workflows/{workflow_file}/dispatches"

    payload = {"ref": branch}

    r = requests.post(url, headers=headers, json=payload)

    if r.status_code != 204:
        return f"❌ Error triggering workflow: {r.text}"

    return f"🚀 Deployment triggered for workflow `{workflow_file}` on branch `{branch}`"
