"""Deliberate demo fault. Does NOT create approvals or call /alerts manually."""
import argparse
import json
import subprocess


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--confirm-demo-only", action="store_true", required=True)
    parser.parse_args()
    command = ["kubectl", "--context", "opspilot", "-n", "opspilot"]
    result = subprocess.run(command + ["get", "deployment", "opspilot-demo", "-o", "json"],
                            capture_output=True, text=True, check=True, timeout=20)
    deployment = json.loads(result.stdout)
    spec = deployment["spec"]["template"]["spec"]
    containers = spec.get("containers", [])
    if len(containers) != 1 or containers[0].get("name") != "opspilot-demo":
        raise SystemExit("STOP: unexpected demo container layout")
    container = containers[0]
    if "/runtime/crash.flag" not in " ".join(container.get("args", [])):
        raise SystemExit("STOP: expected crash.flag demo startup command missing")
    mounts = {m["mountPath"]: m["name"] for m in container.get("volumeMounts", [])}
    volumes = {v["name"]: v for v in spec.get("volumes", [])}
    if "emptyDir" not in volumes.get(mounts.get("/runtime"), {}):
        raise SystemExit("STOP: /runtime is not a pod-scoped emptyDir")
    result = subprocess.run(command + ["get", "pods", "-l", "app=opspilot-demo", "-o", "json"],
                            capture_output=True, text=True, check=True, timeout=20)
    pods = json.loads(result.stdout)["items"]
    if len(pods) != 1 or not any(c.get("type") == "Ready" and c.get("status") == "True"
                              for c in pods[0].get("status", {}).get("conditions", [])):
        raise SystemExit("STOP: expected exactly one healthy demo pod before fault injection")
    pod = pods[0]["metadata"]["name"]
    print("Injecting intentional crash ONLY into opspilot/" + pod, flush=True)
    subprocess.run(command + ["exec", pod, "--", "/bin/sh", "-c",
                   "touch /runtime/crash.flag && kill -TERM 1"], check=True, timeout=20)
    print("Fault injected. Wait for Prometheus -> Alertmanager -> LangGraph -> RAG/Groq -> Telegram.")
    print("No approval was created by this command. Only a fresh Telegram approval can authorize recovery.")


if __name__ == "__main__":
    main()
