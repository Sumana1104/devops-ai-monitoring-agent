"""Index real, linked, human-approved recovery outcomes; never fake history."""
import json

from ai_service.app.incident_history import mark_indexed
from ai_service.app.rag_tools import store_incident


def index_recovered_investigation(row):
    outcome = row["outcome"]
    if not (row.get("approval_id") and outcome.get("status") == "recovered"
            and outcome.get("restart_command_succeeded") is True
            and outcome.get("recovery", {}).get("recovered") is True):
        return False
    report = json.loads(row["state"].get("analysis", "{}"))
    description = (row["state"].get("incident", "Prometheus demo incident") +
                   "\nBounded original log snapshot:\n" + row["state"].get("pod_logs", "")[:3500])
    store_incident(
        row["investigation_id"], description,
        report.get("root_cause", "Root cause unconfirmed"),
        "Human-approved restart_deployment of opspilot/opspilot-demo. "
        "Kubernetes rollout/ready-replica recovery verified. "
        "This does not independently verify the AI root-cause hypothesis or all application HTTP behavior.",
        verified=True,
    )
    mark_indexed(row["investigation_id"])
    return True
