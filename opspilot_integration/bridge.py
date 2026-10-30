"""Local OpsPilot link: Alertmanager -> LangGraph -> Telegram -> guarded K8s.

Designed for ONE local operator; no K8s credentials leave Ubuntu.
Only the demo deployment can be considered for remediation.
"""
from __future__ import annotations

import hmac
import json
import logging
import os
import threading
import time
from pathlib import Path

import requests
from dotenv import load_dotenv
from fastapi import BackgroundTasks, FastAPI, HTTPException, Request
from fastapi.responses import Response

from opspilot_integration.alert_freshness import SUPPORTED_ALERTS, verify_current_alert

ROOT = Path(__file__).resolve().parent.parent
HERE = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env", override=False)
load_dotenv(ROOT / "ai_service/.env", override=False)
# Always use one stable local SQLite path, not Render's ephemeral disk.
os.environ.setdefault("OPSPILOT_APPROVAL_DB", str(ROOT / "opspilot_approvals.db"))
os.environ.setdefault("OPSPILOT_NAMESPACE", "opspilot")
os.environ.setdefault("OPSPILOT_APP_LABEL", "app=opspilot-demo")
os.environ.setdefault("OPSPILOT_DEPLOYMENT", "opspilot-demo")

app = FastAPI(title="OpsPilot Local Integration Bridge", version="1.0")
RUNTIME_VERSION = "opspilot-v1-metrics-1"
log = logging.getLogger("opspilot.bridge")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
_seen: dict[str, float] = {}
_seen_lock = threading.Lock()
_stop = threading.Event()


def alert_token() -> str:
    path = HERE / "runtime/alert_token"
    return path.read_text().strip() if path.is_file() else ""


def telegram_configured() -> bool:
    return bool(os.getenv("TELEGRAM_BOT_TOKEN") and os.getenv("TELEGRAM_CHAT_ID"))


def telegram_api(method: str, body: dict) -> dict:
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        raise RuntimeError("Missing TELEGRAM_BOT_TOKEN in local env")
    from api_service.app.telegram_notifications import _telegram_request
    return _telegram_request(method, token, body, timeout=15)


def telegram_text(body: str) -> None:
    if not telegram_configured():
        log.warning("Telegram not configured; message not sent: %s", body[:120])
        return
    telegram_api("sendMessage", {
        "chat_id": os.environ["TELEGRAM_CHAT_ID"],
        "text": body[:3900],
    })


def first_time(key: str, ttl: float = 600.0) -> bool:
    now = time.monotonic()
    with _seen_lock:
        for old, at in list(_seen.items()):
            if now - at > 3600:
                _seen.pop(old, None)
        if key in _seen and now - _seen[key] < ttl:
            return False
        _seen[key] = now
        return True


def run_ai_investigation(incident: str, investigation_id=None, resource_kind=None) -> tuple[dict | None, str | None]:
    """This is the actual handoff previously missing from the project."""
    try:
        from ai_service.app.ai_agent import agent
        state = {"incident": incident, "investigation_id": investigation_id}
        if resource_kind is not None:
            if resource_kind not in ("oomkilled", "failed_scheduling"):
                raise ValueError("Invalid Kubernetes incident kind")
            state["resource_incident_kind"] = resource_kind
        report = agent.invoke(state)
        parsed = json.loads(report["analysis"])
        if not isinstance(parsed, dict):
            raise ValueError("Expected AI JSON object")
        parsed.update({name: report.get(name) for name in
                       ("safe_action", "policy_reason", "retrieval_sources", "rag_status", "steps", "resource_evidence")})
        return parsed, None
    except Exception as exc:
        log.exception("AI investigation could not complete")
        return None, getattr(exc, "safe_code", type(exc).__name__)


def get_prometheus_snapshot() -> str:
    try:
        res = requests.get(
            "http://127.0.0.1:9090/api/v1/query",
            params={"query": 'sum(rate(opspilot_http_requests_total{status=~"5.."}[1m]))'},
            timeout=5,
        )
        res.raise_for_status()
        series = res.json()["data"]["result"]
        return f"Current observed HTTP 5xx rate series: {series}"
    except Exception as exc:
        return f"Prometheus detail query unavailable ({type(exc).__name__})"


def inspect_crash() -> dict:
    from ai_service.app.remediation_executor import (
        APP_LABEL, DEPLOYMENT, NAMESPACE, get_live_restart_evidence,
    )
    # Defense in depth: NEVER evaluate main opspilot for demo restart.
    if (NAMESPACE, DEPLOYMENT, APP_LABEL) != (
        "opspilot", "opspilot-demo", "app=opspilot-demo"
    ):
        return {"confirmed": False, "reason": "Local allowlist mismatch (expected opspilot-demo)"}
    return get_live_restart_evidence()


def process_crash_evidence(evidence: dict, source: str, investigation_id=None, ai_summary=None) -> None:
    """Send approval ONLY for real live CrashLoopBackOff in demo pod."""
    from opspilot_integration.incident_dedup import (
        claim_crash_notification, finish_notification,
    )
    if evidence.get("confirmed") is not True:
        return
    pod = evidence.get("pod")
    episode_id = claim_crash_notification()
    if episode_id is None:
        log.debug("Suppressed duplicate approval for ongoing opspilot-demo crash")
        return
    summary = ai_summary or f"Kubernetes confirmed {pod} CrashLoopBackOff ({source})."
    try:
        from api_service.app.incident_notification import request_incident_approval
        result = request_incident_approval(
            action="restart_deployment",
            summary=summary,
            incident_type="application_failure",
            incident_evidence=evidence,
            investigation_id=investigation_id,
        )
        finish_notification(episode_id, result["incident_id"])
        log.info("Approval sent (incident ID: %s)", result.get("incident_id"))
    except Exception:
        log.exception("Approval delivery failed or is uncertain; automatic resend suppressed")
        try:
            finish_notification(episode_id)
        except Exception:
            log.exception("Could not record delivery outcome; reserved notification remains blocked")


def save_alert_state(investigation_id, updates, stage):
    from ai_service.app.incident_history import get_investigation, save_investigation
    row = get_investigation(investigation_id)
    state = dict(row["state"]) if row else {}
    state.update(updates)
    save_investigation(investigation_id, state, stage)


def filter_non_current_alert(investigation_id, verification, checks):
    """Quietly journal cleared events; warn explicitly if monitoring is unknown."""
    status = verification.get("status")
    if status == "firing":
        return False
    if status == "inactive":
        save_alert_state(investigation_id, {"alert_verification": checks}, "ignored_inactive")
        log.info("Queued alert %s is no longer current; journalled without incident notification", investigation_id)
    else:
        save_alert_state(investigation_id, {"alert_verification": checks}, "verification_unavailable")
        telegram_text(
            f"OpsPilot monitoring check unavailable for event {investigation_id[:10]}.\n"
            f"{verification.get('reason', 'Current alert could not be verified')}\n"
            "Current service health is NOT confirmed. Manual monitoring review required.\n"
            "No remediation approval issued; no Kubernetes change executed."
        )
    return True


def process_alert(alert: dict) -> None:
    # Resource RCA is initiated by a currently firing real metric alert, never
    # by the periodic Kubernetes observer itself.
    name = (alert.get("labels") or {}).get("alertname")
    if name in {"OpsPilotDemoOOMKilled", "OpsPilotDemoFailedScheduling"}:
        from opspilot_integration.resource_incidents import process_current_incident
        process_current_incident(alert=alert)
        return
    from ai_service.app.incident_history import reserve_investigation, redact
    investigation_id = reserve_investigation(alert)
    if investigation_id is None:
        log.info("Suppressed redelivery of existing Alertmanager investigation")
        return
    before = verify_current_alert(alert)
    checks = {"before_ai": before}
    if filter_non_current_alert(investigation_id, before, checks):
        return
    save_alert_state(investigation_id, {"alert_verification": checks}, "received")
    labels = alert.get("labels") or {}
    name = labels.get("alertname", "unknown")
    summary = (alert.get("annotations") or {}).get("summary", name)
    message = redact(f"Authenticated Alertmanager event: {name}. {summary}. "
                     "Target=opspilot/opspilot-demo. Verify actual Kubernetes and metrics evidence.")[:3000]
    analysis, error = run_ai_investigation(message, investigation_id)
    # Groq/retrieval can take time. A real failure may have cleared while AI ran;
    # do not send a new incident or an approval based on that old firing event.
    after = verify_current_alert(alert)
    checks["after_ai"] = after
    updates = {"alert_verification": checks}
    if not analysis:
        updates.update({"incident": message, "analysis": error})
    save_alert_state(investigation_id, updates, "investigated" if analysis else "ai_failed")
    if filter_non_current_alert(investigation_id, after, checks):
        return
    if not analysis:
        telegram_text(f"OpsPilot AI investigation {investigation_id[:10]} failed ({error}). "
                      "Manual review required. No approval issued; no remediation executed.")
        return
    refs = analysis.get("retrieval_sources") or []
    references = ", ".join(str(r.get("source", "unknown"))[:80] for r in refs) or "none"
    details = redact(
        f"Alert: {name} (currently firing)\nInvestigation: {investigation_id}\n"
        f"AI hypothesis: {analysis['root_cause'][:600]}\n"
        f"Confidence: {analysis['confidence']} | Severity: {analysis['severity']}\n"
        f"AI proposal: {analysis['recommended_action']}\n"
        f"RAG ({analysis.get('rag_status')}): {references}\n"
        f"Policy: {analysis.get('policy_reason')}"
    )
    if analysis.get("safe_action") == "restart_deployment":
        live = inspect_crash()
        if live.get("confirmed"):
            process_crash_evidence(live, name, investigation_id=investigation_id, ai_summary=details)
            return
        details += "\nLive state changed before approval; manual review required."
    elif analysis.get("safe_action") == "clear_demo_readiness_flag":
        from ai_service.app.readiness_remediation import get_live_readiness_evidence, claim_notification
        live = get_live_readiness_evidence()
        if live.get("confirmed"):
            if not claim_notification(live["fault_id"]):
                log.info("Suppressed duplicate/uncertain readiness approval for this fault")
                return
            try:
                from api_service.app.incident_notification import request_incident_approval
                request_incident_approval(
                    action="clear_demo_readiness_flag", summary=details,
                    incident_type="readiness_failure", incident_evidence=live,
                    investigation_id=investigation_id,
                )
            except Exception:
                log.exception("Readiness approval delivery failed or is uncertain; automatic resend suppressed")
            return
        details += "\nLive readiness incident changed before approval; manual review required."
    telegram_text(details + "\nNo remediation approval issued. No Kubernetes changes executed.")


def watch_kubernetes() -> None:
    from opspilot_integration.incident_dedup import demo_is_healthy, observe_demo_health
    from opspilot_integration.kubernetes_metrics import refresh
    while not _stop.wait(10):
        try:
            refresh()  # Read-only metric observation; no AI/notification/repair.
            evidence = inspect_crash()
            if evidence.get("confirmed"):
                # Watcher never bypasses LangGraph/Groq/RAG to create approval.
                observe_demo_health(False)
            elif observe_demo_health(demo_is_healthy()):
                log.info("opspilot-demo healthy for 60s; closed crash notification episode")
        except Exception:
            log.exception("Kubernetes watcher failure")


@app.on_event("startup")
def startup():
    _stop.clear()
    if os.getenv("OPSPILOT_TRAFFIC_MONITORING") == "1":
        def observe_traffic():
            from opspilot_integration.traffic.evidence import refresh
            while not _stop.is_set():
                refresh()  # Read-only, no AI/approvals.
                _stop.wait(10)
        threading.Thread(target=observe_traffic, daemon=True).start()
    if os.getenv("OPSPILOT_DISABLE_WATCHER") != "1":
        threading.Thread(target=watch_kubernetes, name="opspilot-k8s-watcher", daemon=True).start()
    log.info("OpsPilot bridge started. Real remediation enabled: %s", os.getenv("OPSPILOT_ENABLE_DEMO_RESTART") == "1")


@app.on_event("shutdown")
def shutdown():
    _stop.set()


@app.get("/health")
def health():
    return {
        "status": "ok",
        "traffic_extension_version": "locust-capacity-1",
        "traffic_monitoring_enabled": os.getenv("OPSPILOT_TRAFFIC_MONITORING") == "1",
        "traffic_scaling_enabled": os.getenv("OPSPILOT_ENABLE_TRAFFIC_SCALING") == "1",
        "local_bridge": True,
        "runtime_version": RUNTIME_VERSION,
        "telegram_configured": telegram_configured(),
        "demo_restart_enabled": os.getenv("OPSPILOT_ENABLE_DEMO_RESTART") == "1",
        "demo_readiness_repair_enabled": os.getenv("OPSPILOT_ENABLE_DEMO_READINESS_REPAIR") == "1",
        "oom_repair_enabled": os.getenv("OPSPILOT_ENABLE_OOM_REPAIR") == "1",
        "resource_incident_watch_enabled": os.getenv("OPSPILOT_WATCH_RESOURCE_INCIDENTS") == "1",
        "ai_pipeline": "alertmanager-langgraph-rag-groq-v1",
        "watcher_issues_approvals": False,
        "investigation_trigger": "verified_prometheus_alert",
        "watcher_requires_ai_policy": True,
    }


@app.get("/metrics")
def kubernetes_metrics():
    from opspilot_integration.kubernetes_metrics import render
    return Response(render(), media_type="text/plain; version=0.0.4; charset=utf-8")


@app.get("/traffic/metrics")
def traffic_metrics():
    from opspilot_integration.traffic.evidence import render
    return Response(render(), media_type="text/plain; version=0.0.4; charset=utf-8")


@app.post("/alerts")
async def alerts(request: Request, background_tasks: BackgroundTasks):
    expected = alert_token()
    supplied = request.headers.get("authorization", "")
    if not expected or not hmac.compare_digest(supplied, "Bearer " + expected):
        raise HTTPException(403, "Unauthorized Alertmanager")
    raw = await request.body()
    if len(raw) > 100000:
        raise HTTPException(413, "Alertmanager payload too large")
    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        raise HTTPException(400, "Invalid Alertmanager JSON") from None
    if not isinstance(data, dict) or not isinstance(data.get("alerts", []), list):
        raise HTTPException(400, "Invalid Alertmanager payload")
    if data.get("receiver") not in (None, "local-opspilot"):
        raise HTTPException(400, "Unexpected receiver")
    count = 0
    for alert in data.get("alerts", [])[:20]:
        if not isinstance(alert, dict):
            continue
        labels = alert.get("labels") or {}
        if not isinstance(labels, dict) or not isinstance(alert.get("annotations", {}), dict):
            continue
        if not all(isinstance(k, str) and isinstance(v, str) for k, v in labels.items()):
            continue
        name = labels.get("alertname", "")
        if (os.getenv("OPSPILOT_TRAFFIC_MONITORING") == "1"
                and name == "OpsPilotTrafficOverload" and labels.get("service") == "opspilot-traffic"):
            if alert.get("status") == "firing":
                from opspilot_integration.traffic.control import process
                background_tasks.add_task(process, alert)
                count += 1
            continue
        if name not in SUPPORTED_ALERTS:
            continue
        if labels.get("service") != "opspilot-demo":
            continue
        from ai_service.app.incident_history import alert_event_key
        # A new startsAt is a new event even if a prior resolved delivery was
        # missed. A fingerprint alone would suppress genuine recurring failures.
        key = alert_event_key(alert)
        if alert.get("status") == "resolved":
            with _seen_lock:
                _seen.pop("alert:" + key, None)
            continue
        if alert.get("status") == "firing" and first_time("alert:" + key, 900):
            background_tasks.add_task(process_alert, alert)
            count += 1
    return {"accepted": count}


def process_approved_callback(incident_id: str):
    try:
        from ai_service.app.remediation_executor import execute_approved_remediation
        from api_service.app.approval_store import get_approval
        approval = get_approval(incident_id)
        readiness = approval and approval.get("action") == "clear_demo_readiness_flag"
        feature = ("OPSPILOT_ENABLE_OOM_REPAIR" if approval and approval.get("action") == "increase_demo_memory_limit"
                   else "OPSPILOT_ENABLE_DEMO_READINESS_REPAIR" if readiness else "OPSPILOT_ENABLE_DEMO_RESTART")
        execute = os.getenv(feature) == "1"
        result = execute_approved_remediation(incident_id, execute=execute)
        outcome = str(result.get("status", "unknown"))
        why = str(result.get("reason", ""))
        learned = "not indexed (outcome not a verified linked recovery)"
        try:
            from ai_service.app.incident_history import record_outcome
            from opspilot_integration.learning import index_recovered_investigation
            row = record_outcome(incident_id, result)
            if row and index_recovered_investigation(row):
                learned = "verified outcome stored in local Chroma history"
        except Exception as error:
            learned = "index pending; journal can be retried with ai_status --retry-index"
            log.error("Outcome journal/index update failed (%s)", type(error).__name__)
        telegram_text(
            f"OpsPilot approval result for {incident_id[:10]}:\n"
            f"{outcome}\n{why[:500]}\n"
            f"Execution enabled: {execute}.\n"
            f"Restart command succeeded: {result.get('restart_command_succeeded', False)}.\n"
            f"Approved repair command succeeded: {result.get('repair_command_succeeded', False)}.\n"
            f"Service HTTP verified: {result.get('recovery', {}).get('service_http_verified', False)}.\n"
            f"History: {learned}.\n"
            "Only opspilot/opspilot-demo is allowlisted."
        )
        log.info("Decision result %s: %s", incident_id, outcome)
    except Exception:
        log.exception("Post-approval validation failed")
        try:
            telegram_text("OpsPilot: approval recorded, but execution validation failed. No success confirmed; inspect bridge logs.")
        except Exception:
            log.exception("Failed to notify approval validation error")


@app.post("/telegram/webhook")
async def telegram_webhook(request: Request, background_tasks: BackgroundTasks):
    expected = os.getenv("TELEGRAM_WEBHOOK_SECRET", "")
    received = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
    if not expected or not hmac.compare_digest(expected, received):
        raise HTTPException(403, "Unauthorized Telegram webhook")
    update = await request.json()
    callback = update.get("callback_query")
    if not callback:
        return {"status": "ignored_non_callback"}
    user_id = str((callback.get("from") or {}).get("id", ""))
    chat_id = str(((callback.get("message") or {}).get("chat") or {}).get("id", ""))
    if not os.getenv("TELEGRAM_APPROVER_USER_ID") or not os.getenv("TELEGRAM_CHAT_ID"):
        raise HTTPException(503, "Approver configuration unavailable")
    if not (
        hmac.compare_digest(user_id, os.environ["TELEGRAM_APPROVER_USER_ID"])
        and hmac.compare_digest(chat_id, os.environ["TELEGRAM_CHAT_ID"])
    ):
        log.warning("Telegram callback denied: wrong user/chat")
        return {"status": "unauthorized_approver"}
    action = str(callback.get("data", ""))
    if action.startswith(("traffic_approve:", "traffic_reject:")):
        from opspilot_integration.traffic.control import callback as traffic_callback
        return await traffic_callback(callback, background_tasks)
    try:
        decision, incident_id = action.split(":", 1)
    except ValueError:
        return {"status": "invalid_callback"}
    if decision not in {"approve", "reject"} or len(incident_id) != 32:
        return {"status": "invalid_callback"}
    from api_service.app.approval_store import decide_approval
    accepted = decide_approval(incident_id, decision)
    try:
        telegram_api("answerCallbackQuery", {
            "callback_query_id": callback["id"],
            "text": ("Decision recorded" if accepted else "Already used or expired"),
            "show_alert": False,
        })
    except Exception:
        log.exception("Failed acknowledging callback")
    if not accepted:
        return {"status": "invalid_or_expired_approval"}
    if decision == "approve":
        background_tasks.add_task(process_approved_callback, incident_id)
    else:
        background_tasks.add_task(process_rejection, incident_id)
    return {"status": "decision_recorded", "decision": decision}


def process_rejection(incident_id):
    from ai_service.app.incident_history import record_outcome
    record_outcome(incident_id, {"status": "rejected", "restart_command_succeeded": False})
    telegram_text("❌ OpsPilot incident rejected. Kubernetes remains unchanged.")
