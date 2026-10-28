"""One automatic notification attempt per ongoing demo crash, stored in SQLite.

Notification deduplication never grants or renews remediation approval.
Unknown delivery is deliberately not retried: Telegram has no sendMessage
idempotency key, so retrying after a timeout could send the same request twice.
"""
from contextlib import contextmanager
import json
import os
from pathlib import Path
import secrets
import sqlite3
import subprocess
import time

ROOT = Path(__file__).resolve().parent.parent
KEY = "opspilot/opspilot-demo/application_failure"
HEALTHY_SECONDS = 60
MAX_HEALTH_SAMPLE_GAP = 45


@contextmanager
def _database():
    path = Path(os.getenv("OPSPILOT_APPROVAL_DB", str(ROOT / "opspilot_approvals.db")))
    if not path.is_absolute():
        path = ROOT / path
    db = sqlite3.connect(str(path), timeout=10)
    try:
        db.execute("""CREATE TABLE IF NOT EXISTS opspilot_crash_notifications (
            target TEXT PRIMARY KEY,
            episode_id TEXT NOT NULL,
            active INTEGER NOT NULL,
            delivery TEXT NOT NULL,
            incident_id TEXT,
            healthy_since REAL,
            last_healthy_at REAL
        )""")
        db.commit()
        db.execute("BEGIN IMMEDIATE")
        yield db
        db.commit()
    except BaseException:
        db.rollback()
        raise
    finally:
        db.close()


def claim_crash_notification():
    """Atomically reserve a single send; pod/restart count are NOT the key."""
    episode_id = secrets.token_hex(16)
    with _database() as db:
        # A confirmed crash interrupts any recovery observation, even when
        # this notification itself is suppressed as a duplicate.
        db.execute("""UPDATE opspilot_crash_notifications
            SET healthy_since = NULL, last_healthy_at = NULL
            WHERE target = ? AND active = 1""", (KEY,))
        result = db.execute("""INSERT INTO opspilot_crash_notifications
            (target, episode_id, active, delivery)
            VALUES (?, ?, 1, 'sending')
            ON CONFLICT(target) DO UPDATE SET
                episode_id = excluded.episode_id,
                active = 1, delivery = 'sending', incident_id = NULL,
                healthy_since = NULL, last_healthy_at = NULL
            WHERE opspilot_crash_notifications.active = 0""", (KEY, episode_id))
        return episode_id if result.rowcount == 1 else None


def finish_notification(episode_id, incident_id=None):
    """Record sent or uncertain; neither state permits another automatic send."""
    with _database() as db:
        db.execute("""UPDATE opspilot_crash_notifications
            SET delivery = ?, incident_id = ?
            WHERE target = ? AND episode_id = ? AND active = 1""",
            ("sent" if incident_id else "uncertain", incident_id, KEY, episode_id))


def observe_demo_health(healthy, now=None):
    """Close only after repeated healthy samples; errors/unknown never close."""
    now = time.time() if now is None else now
    with _database() as db:
        row = db.execute("""SELECT healthy_since, last_healthy_at
            FROM opspilot_crash_notifications WHERE target = ? AND active = 1""",
            (KEY,)).fetchone()
        if row is None:
            return False
        if not healthy:
            db.execute("""UPDATE opspilot_crash_notifications
                SET healthy_since = NULL, last_healthy_at = NULL WHERE target = ?""", (KEY,))
            return False
        since, last = row
        if (since is None or last is None or now < last
                or now - last > MAX_HEALTH_SAMPLE_GAP):
            since = now
        closed = now - since >= HEALTHY_SECONDS
        db.execute("""UPDATE opspilot_crash_notifications
            SET active = ?, healthy_since = ?, last_healthy_at = ? WHERE target = ?""",
            (0 if closed else 1, since, now, KEY))
        return closed


def _kubectl_json(*args):
    result = subprocess.run(
        ["kubectl", "--context", "opspilot", "-n", "opspilot", *args, "-o", "json"],
        capture_output=True, text=True, check=True, timeout=20,
    )
    return json.loads(result.stdout)


def demo_is_healthy():
    """Read-only recovery probe. A missing crash alone is NOT recovery."""
    try:
        deployment = _kubectl_json("get", "deployment", "opspilot-demo")
        desired = deployment.get("spec", {}).get("replicas", 1)
        status = deployment.get("status", {})
        if not isinstance(desired, int) or desired < 1:
            return False
        if deployment["metadata"].get("deletionTimestamp"):
            return False
        if status.get("observedGeneration", 0) < deployment["metadata"]["generation"]:
            return False
        if any(status.get(field, 0) != desired for field in
               ("readyReplicas", "updatedReplicas", "availableReplicas")):
            return False
        pods = _kubectl_json("get", "pods", "-l", "app=opspilot-demo")["items"]
        # Do not treat a rollout still terminating old pods as stable recovery.
        if len(pods) != desired:
            return False
        for pod in pods:
            if pod["metadata"].get("deletionTimestamp"):
                return False
            state = pod.get("status", {})
            if state.get("phase") != "Running":
                return False
            if not any(c.get("type") == "Ready" and c.get("status") == "True"
                       for c in state.get("conditions", [])):
                return False
            containers = state.get("containerStatuses", [])
            if not containers or not all(c.get("ready") is True and
                    isinstance(c.get("state", {}).get("running"), dict) for c in containers):
                return False
        return True
    except (subprocess.SubprocessError, OSError, ValueError, KeyError, TypeError, AttributeError):
        return False
