"""Local, redacted incident journal; never a source of execution permission."""
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import sqlite3
import time

ROOT = Path(__file__).resolve().parents[2]


def history_path():
    path = Path(os.getenv("OPSPILOT_HISTORY_PATH", "data/opspilot_history.sqlite3"))
    return path if path.is_absolute() else ROOT / path


def redact(value):
    """Best effort, NOT a guarantee of PII removal. Never log secrets upstream."""
    text = str(value)
    for name, secret in os.environ.items():
        if re.search(r"TOKEN|SECRET|PASSWORD|API_KEY|CREDENTIAL", name, re.I) and len(secret) >= 6:
            text = text.replace(secret, "[REDACTED]")
    text = re.sub(r"(?i)(Bearer\s+)[A-Za-z0-9._~+/=-]+", r"\1[REDACTED]", text)
    text = re.sub(r"(?i)(api\.telegram\.org/bot)[^/\s]+", r"\1[REDACTED]", text)
    return text


@contextmanager
def database():
    path = history_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(str(path), timeout=15)
    db.row_factory = sqlite3.Row
    try:
        os.chmod(path, 0o600)
        db.execute("""CREATE TABLE IF NOT EXISTS ai_investigations (
            investigation_id TEXT PRIMARY KEY, event_key TEXT NOT NULL UNIQUE,
            created_at REAL NOT NULL, alert_json TEXT NOT NULL,
            stage TEXT NOT NULL, state_json TEXT, approval_id TEXT UNIQUE,
            outcome_json TEXT, indexed INTEGER NOT NULL DEFAULT 0
        )""")
        yield db
        db.commit()
    except BaseException:
        db.rollback()
        raise
    finally:
        db.close()


def alert_event_key(alert):
    """Stable identity shared by persistent and in-memory event deduplication."""
    labels = alert.get("labels") or {}
    key_data = [labels, alert.get("startsAt", "unknown"), alert.get("fingerprint", "")]
    return hashlib.sha256(json.dumps(key_data, sort_keys=True).encode()).hexdigest()


def reserve_investigation(alert):
    """Reserve a Prometheus event once, surviving process restarts/redelivery."""
    key = alert_event_key(alert)
    investigation_id = secrets.token_hex(16)
    encoded_alert = redact(json.dumps(alert))
    if len(encoded_alert) > 12000:
        raise ValueError("Single alert exceeds journal size limit")
    with database() as db:
        result = db.execute("""INSERT OR IGNORE INTO ai_investigations
            (investigation_id, event_key, created_at, alert_json, stage)
            VALUES (?, ?, ?, ?, 'received')""",
            (investigation_id, key, time.time(), encoded_alert))
        return investigation_id if result.rowcount == 1 else None


def save_investigation(investigation_id, state, stage="investigated"):
    fields = ("incident", "pod_info", "pod_logs", "pod_events", "pod_details",
              "prometheus_metrics", "readiness_evidence", "resource_incident_kind", "resource_evidence", "retrieved_knowledge", "retrieval_sources",
              "rag_status", "analysis", "safe_action", "policy_reason", "steps",
              "alert_verification")
    snapshot = {name: state[name] for name in fields if name in state}
    encoded = redact(json.dumps(snapshot, ensure_ascii=False))
    if len(encoded) > 100000:
        raise ValueError("Evidence snapshot exceeds local journal size limit")
    with database() as db:
        db.execute("UPDATE ai_investigations SET stage=?, state_json=? WHERE investigation_id=?",
                   (stage, encoded, investigation_id))


def link_approval(investigation_id, approval_id):
    with database() as db:
        result = db.execute("""UPDATE ai_investigations SET approval_id=?, stage='awaiting_approval'
            WHERE investigation_id=? AND approval_id IS NULL""", (approval_id, investigation_id))
        if result.rowcount != 1:
            raise ValueError("AI investigation missing or already linked to an approval")


def decode(row):
    item = dict(row)
    for name in ("alert_json", "state_json", "outcome_json"):
        item[name[:-5]] = json.loads(item.pop(name) or "{}")
    return item


def record_outcome(approval_id, outcome):
    with database() as db:
        db.execute("UPDATE ai_investigations SET outcome_json=?, stage=? WHERE approval_id=?",
                   (redact(json.dumps(outcome)), outcome.get("status", "unknown"), approval_id))
        row = db.execute("SELECT * FROM ai_investigations WHERE approval_id=?", (approval_id,)).fetchone()
    return decode(row) if row else None


def recent_history(limit=25, exclude_id=None):
    limit = max(1, min(int(limit), 25))
    with database() as db:
        rows = db.execute("""SELECT * FROM ai_investigations
            WHERE investigation_id != ? ORDER BY created_at DESC, rowid DESC LIMIT ?""",
            (exclude_id or "", limit)).fetchall()
    return [decode(row) for row in rows]


def get_investigation(investigation_id):
    with database() as db:
        row = db.execute("SELECT * FROM ai_investigations WHERE investigation_id=?",
                         (investigation_id,)).fetchone()
    return decode(row) if row else None


def get_investigation_for_approval(approval_id):
    """Resolve the existing linked journal entry; never create an approval."""
    with database() as db:
        row = db.execute("SELECT * FROM ai_investigations WHERE approval_id=?",
                         (approval_id,)).fetchone()
    return decode(row) if row else None


def mark_indexed(investigation_id):
    with database() as db:
        db.execute("UPDATE ai_investigations SET indexed=1 WHERE investigation_id=?", (investigation_id,))


def pending_verified_outcomes():
    with database() as db:
        rows = db.execute("SELECT * FROM ai_investigations WHERE stage='recovered' AND indexed=0").fetchall()
    return [decode(row) for row in rows]
