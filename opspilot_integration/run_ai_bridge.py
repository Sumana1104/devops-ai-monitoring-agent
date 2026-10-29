"""One foreground supervisor for the existing local AI incident pipeline.

Starts only Prometheus/Alertmanager via the existing Compose overlay, owns one
reconnecting demo port-forward and one bridge. Never stops unrelated processes.
"""
import argparse
import os
from pathlib import Path
import secrets
import shutil
import socket
import subprocess
import sys
import threading
import time

ROOT = Path(__file__).resolve().parent.parent


def stop_child(child):
    if child is not None and child.poll() is None:
        child.terminate()
        try:
            child.wait(timeout=10)
        except subprocess.TimeoutExpired:
            child.kill()  # Only a child started by this supervisor.
            child.wait(timeout=5)


def main():
    from dotenv import load_dotenv
    import requests
    load_dotenv(ROOT / ".env", override=False)
    load_dotenv(ROOT / "ai_service/.env", override=False)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--enable-demo-restart", action="store_true",
                        help="Allow approved demo restarts; NEVER bypasses human approval")
    args = parser.parse_args()
    os.chdir(ROOT)
    os.environ["PATH"] = str(Path.home() / ".local/bin") + os.pathsep + os.environ.get("PATH", "")
    os.environ["OPSPILOT_DISABLE_WATCHER"] = "0"  # Health/dedup maintenance ONLY, never approval.
    os.environ["OPSPILOT_ENABLE_DEMO_RESTART"] = "1" if args.enable_demo_restart else "0"
    os.environ["OPSPILOT_APPROVAL_DB"] = str(ROOT / "opspilot_approvals.db")
    os.environ["OPSPILOT_RUN_UID"] = str(os.getuid())
    os.environ["OPSPILOT_RUN_GID"] = str(os.getgid())
    for name in ("GROQ_API_KEY", "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID",
                 "TELEGRAM_APPROVER_USER_ID", "TELEGRAM_WEBHOOK_SECRET"):
        if not os.getenv(name):
            raise SystemExit(f"Missing {name}; put it in local .env. Do not paste its value.")
    if not shutil.which("kubectl") or not shutil.which("docker"):
        raise SystemExit("kubectl/docker unavailable in PATH. No processes started.")
    with socket.socket() as probe:
        if probe.connect_ex(("127.0.0.1", 18008)) == 0:
            try:
                existing = requests.get("http://127.0.0.1:18008/health", timeout=5)
                existing.raise_for_status()
                info = existing.json()
            except (requests.RequestException, ValueError):
                raise SystemExit("Port 18008 is occupied; existing service could not be verified. Nothing stopped or started.") from None
            if not isinstance(info, dict) or info.get("local_bridge") is not True:
                raise SystemExit("Port 18008 belongs to another service. Nothing stopped or started.")
            mode = info.get("demo_restart_enabled")
            print("Existing bridge actual restart setting:", mode, flush=True)
            print("Existing bridge runtime version:", info.get("runtime_version", "older version"), flush=True)
            requested = bool(args.enable_demo_restart)
            if type(mode) is bool and mode == requested and info.get("runtime_version") == "opspilot-stability-1":
                print("Bridge already running in the requested mode. No duplicate started. Its supervisor/tunnel ownership is unchanged.")
                return
            raise SystemExit("Stop ONLY the existing bridge in its terminal, then rerun this command to load the requested version/mode. Nothing was stopped automatically.")
    subprocess.run(["docker", "info", "--format", "{{.ServerVersion}}"], check=True, timeout=20)
    subprocess.run(["kubectl", "--context", "opspilot", "-n", "opspilot", "get", "deployment", "opspilot-demo"],
                   check=True, timeout=20)
    base = ROOT / "observability/docker-compose.yml"
    if not base.is_file():
        raise SystemExit("Existing observability/docker-compose.yml is missing; refusing to guess or replace your stack.")
    token = ROOT / "opspilot_integration/runtime/alert_token"
    token.parent.mkdir(parents=True, exist_ok=True)
    if not token.exists():
        with token.open("x") as output:
            output.write(secrets.token_urlsafe(32))
    if not token.read_text().strip():
        raise SystemExit("Existing alert_token is empty; refusing to change it silently.")
    os.chmod(token, 0o600)
    compose = ["docker", "compose", "-f", str(base), "-f", str(ROOT / "opspilot_integration/compose.integration.yml")]
    subprocess.run(compose + ["config", "--quiet"], check=True, timeout=30)
    subprocess.run(compose + ["up", "-d", "prometheus", "alertmanager"], check=True, timeout=180)
    stop = threading.Event()
    port_process = [None]

    def forward():
        while not stop.is_set():
            with socket.socket() as probe:
                occupied_now = probe.connect_ex(("127.0.0.1", 19110)) == 0
            if occupied_now:
                # Reuse an external forward while it lives, then take over if it
                # exits on pod replacement. Never kill a process we did not start.
                stop.wait(2)
                continue
            child = subprocess.Popen([
                "kubectl", "--context", "opspilot", "-n", "opspilot", "port-forward",
                "--address=0.0.0.0", "--pod-running-timeout=10s", "deployment/opspilot-demo", "19110:8080",
            ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            port_process[0] = child
            while child.poll() is None and not stop.wait(1):
                pass
            stop_child(child)
            stop.wait(2)

    # If a working pre-existing forward owns 19110, reuse it instead of killing it.
    with socket.socket() as probe:
        occupied = probe.connect_ex(("127.0.0.1", 19110)) == 0
    worker = threading.Thread(target=forward, daemon=True)
    worker.start()
    if occupied:
        print("Port 19110 already in use: verifying the existing metrics forward; not replacing it.")
    bridge = None
    try:
        # Reconnect metrics BEFORE slow local model warmup. Otherwise every
        # bridge restart can itself hold the scrape path down past the alert's
        # 30-second threshold, even though the Kubernetes app remains healthy.
        print("Starting metrics forward before local Chroma/MiniLM initialization. No LLM API call.", flush=True)
        initialized = subprocess.run([sys.executable, "-m", "ai_service.app.ingest_runbooks"],
                                     cwd=ROOT, env=dict(os.environ), timeout=600)
        if initialized.returncode != 0:
            raise SystemExit("Local Chroma/embedding initialization failed. No AI bridge started. "
                             "Check your installed chromadb/sentence-transformers packages and model download.")
        # Verify the transport from Prometheus, not just localhost. No fake alerts.
        good = False
        for _ in range(45):
            try:
                res = requests.get("http://127.0.0.1:9090/api/v1/query",
                                   params={"query": 'up{job="opspilot-demo"}'}, timeout=3)
                res.raise_for_status()
                rows = res.json()["data"]["result"]
                if any(float(row["value"][1]) == 1 for row in rows):
                    good = True
                    break
            except (requests.RequestException, ValueError, KeyError):
                pass
            if stop.wait(1):
                break
        if not good:
            # A crashed workload is exactly when the incident bridge is needed.
            # Scrape failure remains a real monitoring fault, never fake health.
            print("MONITORING DEGRADED: Prometheus cannot currently scrape the demo. Starting the bridge so alerts/approvals can still be handled; no healthy state or remediation is implied.", flush=True)
        else:
            print("PASS: Prometheus sees demo up=1. Starting Alertmanager -> AI bridge.", flush=True)
        print("Real demo restarts enabled:", bool(args.enable_demo_restart), flush=True)
        print("Keep your existing Cloudflare tunnel running; webhook path remains /telegram/webhook.", flush=True)
        bridge = subprocess.Popen([
            sys.executable, "-m", "uvicorn", "opspilot_integration.bridge:app",
            "--host", "0.0.0.0", "--port", "18008",
        ], cwd=ROOT, env=dict(os.environ))
        raise SystemExit(bridge.wait())
    except KeyboardInterrupt:
        print("Stopping only this supervisor's bridge/port-forward.")
    finally:
        stop.set()
        stop_child(bridge)
        stop_child(port_process[0])
        if worker:
            worker.join(timeout=12)


if __name__ == "__main__":
    main()
