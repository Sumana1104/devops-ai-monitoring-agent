
import os
import sqlite3
import secrets
import time

DB_PATH = os.getenv(
    "OPSPILOT_APPROVAL_DB",
    "opspilot_approvals.db"
)

ALLOWED_ACTIONS = {
    "restart_deployment",
    "no_action",
}


def connect():
    db = sqlite3.connect(DB_PATH, timeout=10)
    db.execute("""
        CREATE TABLE IF NOT EXISTS approvals (
            incident_id TEXT PRIMARY KEY,
            action TEXT NOT NULL,
            status TEXT NOT NULL,
            expires_at INTEGER NOT NULL
        )
    """)
    return db


def create_approval(action, ttl_seconds=600):
    if action not in ALLOWED_ACTIONS:
        raise ValueError("Action not allowlisted")

    if ttl_seconds <= 0:
        raise ValueError("TTL must be positive")

    incident_id = secrets.token_hex(8)
    expires_at = int(time.time()) + ttl_seconds

    with connect() as db:
        db.execute(
            """
            INSERT INTO approvals
            (incident_id, action, status, expires_at)
            VALUES (?, ?, 'pending', ?)
            """,
            (incident_id, action, expires_at),
        )

    return incident_id


def decide_approval(incident_id, decision):
    if decision not in {"approve", "reject"}:
        return False

    new_status = (
        "approved" if decision == "approve"
        else "rejected"
    )

    with connect() as db:
        result = db.execute(
            """
            UPDATE approvals
            SET status = ?
            WHERE incident_id = ?
              AND status = 'pending'
              AND expires_at > ?
            """,
            (new_status, incident_id, int(time.time())),
        )

        return result.rowcount == 1
