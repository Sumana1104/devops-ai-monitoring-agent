"""Check the three real recoveries and upload only the reviewed V1 source.

Default is check-only. --publish --confirm-upload explicitly authorizes Git
staging, one checkpoint commit if needed, and a normal (never forced) main push.
No Kubernetes repair, credentials extraction, Git reset or deletion is done.
"""
import argparse
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys

from opspilot_integration import scenario, readiness_demo

ROOT = Path(__file__).resolve().parents[1]


def git(*args, capture=False, check=True):
    return subprocess.run(["git", "-C", str(ROOT), *args], check=check,
                          text=True, capture_output=capture)


def source_files():
    values = json.loads((ROOT / "opspilot_integration/v1_source_files.json").read_text())
    if not isinstance(values, list) or not values or len(values) != len(set(values)):
        raise ValueError("Invalid reviewed-source list")
    for name in values:
        parts = PurePosixPath(name).parts
        if (not parts or PurePosixPath(name).is_absolute() or ".." in parts or
                parts[0] not in {".github", "ai_service", "api_service", "docs", "opspilot_integration", "tests"}
                or any(p in {"runtime", "data", ".git", "venv", ".venv"} or p.startswith(".env") for p in parts)
                or not name.endswith((".py", ".yml", ".yaml", ".md", ".txt", ".json", ".sh", "Dockerfile", ".dockerignore"))):
            raise ValueError("Private/unsupported path in source list")
        path = ROOT
        for part in parts:
            path = path / part
            if path.is_symlink(): raise ValueError("Symlink in reviewed-source list")
        if not path.is_file(): raise ValueError("Missing reviewed source: " + name)
        text = path.read_text()
        patterns = (r"gsk_[A-Za-z0-9]{20,}", r"[0-9]{7,12}:[A-Za-z0-9_-]{30,}",
                    r"AKIA[A-Z0-9]{16}", r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----")
        if any(re.search(pattern, text) for pattern in patterns):
            raise ValueError("Potential credential pattern in " + name + "; nothing staged. Pattern checks are not a comprehensive secret audit.")
    return values


def finalize(publish=False, confirmed=False):
    if publish and not confirmed: raise ValueError("Use --publish --confirm-upload to explicitly authorize upload")
    branch = git("branch", "--show-current", capture=True).stdout.strip()
    if branch != "main": raise ValueError("Expected main; no branch was changed")
    origin = git("remote", "get-url", "origin", capture=True).stdout.strip()
    if origin not in {"https://github.com/Sumana1104/devops-ai-monitoring-agent.git", "https://github.com/Sumana1104/devops-ai-monitoring-agent",
                      "git@github.com:Sumana1104/devops-ai-monitoring-agent.git"}:
        raise ValueError("Unexpected origin; no upload attempted")
    if git("diff", "--cached", "--quiet", check=False).returncode:
        raise ValueError("Existing staged work found; no staging/commit/upload attempted")
    files = source_files()
    readiness_demo.bridge_health(safe=True)
    if not scenario.verify(): raise ValueError("Three verified linked recoveries are not all present; V1 is not marked complete")
    environment = dict(os.environ, OTEL_SDK_DISABLED="true")
    subprocess.run([sys.executable, "-m", "unittest", "discover", "-s", "tests", "-p", "test_*.py", "-q"], cwd=ROOT, env=environment, check=True)
    subprocess.run([sys.executable, "-m", "pytest", "tests", "-q"], cwd=ROOT / "ai_service", env=environment, check=True)
    git("diff", "--check")
    if not publish:
        print("PASS: three linked live recoveries, current Service HTTP and code checks. No Git write or upload performed.")
        return
    git("add", "--", *files)
    git("diff", "--cached", "--check")
    staged = git("diff", "--cached", "--name-only", "-z", capture=True).stdout.split("\0")
    if any(path and path not in files for path in staged):
        raise ValueError("Unexpected staged path; stopped without commit/upload. Review the index; no reset performed.")
    if git("diff", "--cached", "--quiet", check=False).returncode:
        git("commit", "-m", "Finish metrics-first OpsPilot V1 scenarios and verification")
    git("push", "origin", "main")
    print("PASS: reviewed V1 source uploaded on main. Private env/keys/databases/runtime and unrelated files were not selected.")
    print("GitHub Actions status and dependency alerts are separate; an upload is not proof of a green pipeline or production readiness.")


def main():
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env", override=False)
    load_dotenv(ROOT / "ai_service/.env", override=False)
    os.environ["PATH"] = str(Path.home() / ".local/bin") + os.pathsep + os.environ.get("PATH", "")
    os.environ["OPSPILOT_APPROVAL_DB"] = str(ROOT / "opspilot_approvals.db")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--publish", action="store_true")
    parser.add_argument("--confirm-upload", action="store_true")
    args = parser.parse_args()
    finalize(args.publish, args.confirm_upload)


if __name__ == "__main__":
    try: main()
    except ValueError as error: raise SystemExit("STOP: " + str(error)) from None
    except Exception as error: raise SystemExit("STOP: finalization failed (" + type(error).__name__ + "). No successful upload is claimed; an earlier local checkpoint may remain.") from None
