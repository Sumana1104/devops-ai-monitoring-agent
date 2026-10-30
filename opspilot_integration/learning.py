"""Index real, linked, human-approved recovery outcomes; never fake history."""
import json

from ai_service.app.incident_history import mark_indexed
from ai_service.app.rag_tools import store_incident


def index_recovered_investigation(row):
    outcome = row["outcome"]
    oom = (row["state"].get("safe_action") == "increase_demo_memory_limit"
           and outcome.get("action") == "increase_demo_memory_limit"
           and outcome.get("repair_command_succeeded") is True
           and outcome.get("recovery", {}).get("service_http_verified") is True
           and outcome.get("recovery", {}).get("ready_service_endpoint_verified") is True
           and outcome.get("recovery", {}).get("stable_seconds", 0) >= 30)
    readiness = (row["state"].get("safe_action") == "clear_demo_readiness_flag"
                 and outcome.get("action") == "clear_demo_readiness_flag"
                 and outcome.get("repair_command_succeeded") is True
                 and outcome.get("recovery", {}).get("service_http_verified") is True
                 and outcome.get("recovery", {}).get("ready_service_endpoint_verified") is True)
    if not (row.get("approval_id") and outcome.get("status") == "recovered"
            and (oom or readiness or outcome.get("restart_command_succeeded") is True)
            and outcome.get("recovery", {}).get("recovered") is True):
        return False
    report = json.loads(row["state"].get("analysis", "{}"))
    description = (row["state"].get("incident", "Prometheus demo incident") +
                   "\nBounded original log snapshot:\n" + row["state"].get("pod_logs", "")[:3500])
    store_incident(
        row["investigation_id"], description,
        report.get("root_cause", "Root cause unconfirmed"),
        ("Human-approved bounded memory-limit mitigation of opspilot/opspilot-demo; Service HTTP and stable readiness verified for 30s. This does not establish that a memory leak was fixed. "
         if oom else "Human-approved readiness flag removal of opspilot/opspilot-demo; Service HTTP and ready endpoint verified. "
         if readiness else "Human-approved restart_deployment of opspilot/opspilot-demo. ") +
        "Kubernetes rollout/ready-replica recovery verified. "
        "This does not independently verify the AI root-cause hypothesis or all application HTTP behavior.",
        verified=True,
    )
    mark_indexed(row["investigation_id"])
    return True
