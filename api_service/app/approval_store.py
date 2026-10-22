
import json
import os
import secrets
import sqlite3
import time


DB_PATH = os.getenv(
    "OPSPILOT_APPROVAL_DB",
    "opspilot_approvals.db",
)

NAMESPACE = "opspilot"
DEPLOYMENT = "opspilot"

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
            expires_at INTEGER NOT NULL,
            namespace TEXT NOT NULL DEFAULT 'opspilot',
            deployment TEXT NOT NULL DEFAULT 'opspilot',
            incident_type TEXT,
            incident_evidence TEXT,
            created_at INTEGER
        )
    """)

    columns = {
        row[1]
        for row in db.execute(
            "PRAGMA table_info(approvals)"
        )
    }

    # Upgrade existing databases without losing records.
    migrations = {
        "namespace": (
            "TEXT NOT NULL DEFAULT 'opspilot'"
        ),
        "deployment": (
            "TEXT NOT NULL DEFAULT 'opspilot'"
        ),
        "incident_type": "TEXT",
        "incident_evidence": "TEXT",
        "created_at": "INTEGER",
    }

    for name, definition in migrations.items():
        if name not in columns:
            db.execute(
                f"ALTER TABLE approvals "
                f"ADD COLUMN {name} {definition}"
            )

    db.commit()
    return db


def create_approval(
    action,
    ttl_seconds=600,
    namespace=NAMESPACE,
    deployment=DEPLOYMENT,
    incident_type=None,
    incident_evidence=None,
):
    if action not in ALLOWED_ACTIONS:
        raise ValueError("Action not allowlisted")

    if namespace != NAMESPACE or deployment != DEPLOYMENT:
        raise ValueError("Target not allowlisted")

    if ttl_seconds <= 0:
        raise ValueError("TTL must be positive")

    if incident_evidence is not None:
        if not isinstance(incident_evidence, dict):
            raise ValueError("Evidence must be a dictionary")

        evidence_json = json.dumps(incident_evidence)
    else:
        evidence_json = None

    incident_id = secrets.token_hex(16)
    now = int(time.time())

    with connect() as db:
        db.execute(
            """
            INSERT INTO approvals (
                incident_id,
                action,
                status,
                expires_at,
                namespace,
                deployment,
                incident_type,
                incident_evidence,
                created_at
            )
            VALUES (?, ?, 'pending', ?, ?, ?, ?, ?, ?)
            """,
            (
                incident_id,
                action,
                now + ttl_seconds,
                namespace,
                deployment,
                incident_type,
                evidence_json,
                now,
            ),
        )

    return incident_id


def decide_approval(incident_id, decision):
    if decision not in {"approve", "reject"}:
        return False

    new_status = (
        "approved"
        if decision == "approve"
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
            (
                new_status,
                incident_id,
                int(time.time()),
            ),
        )

        return result.rowcount == 1


def get_approval(incident_id):
    with connect() as db:
        db.row_factory = sqlite3.Row

        row = db.execute(
            """
            SELECT *
            FROM approvals
            WHERE incident_id = ?
            """,
            (incident_id,),
        ).fetchone()

        return dict(row) if row else None


def claim_approved_action(incident_id):
    """
    Atomically claim an approved action once.

    This is NOT permission to run Kubernetes.
    Live incident eligibility must be revalidated
    by the executor before any remediation.
    """

    with connect() as db:
        result = db.execute(
            """
            UPDATE approvals
            SET status = 'claimed'
            WHERE incident_id = ?
              AND status = 'approved'
              AND expires_at > ?
              AND action = 'restart_deployment'
              AND namespace = ?
              AND deployment = ?
              AND incident_type IS NOT NULL
              AND incident_evidence IS NOT NULL
              AND created_at IS NOT NULL
            """,
            (
                incident_id,
                int(time.time()),
                NAMESPACE,
                DEPLOYMENT,
            ),
        )

        if result.rowcount != 1:
            return None

        return get_approval(incident_id)
