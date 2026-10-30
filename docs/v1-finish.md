# OpsPilot V1: one finish line, three controlled local recoveries

## Package validation

241 root tests passed, including the optional real local Chroma fixture with
deterministic test embeddings (11 runbooks), not a semantic-quality benchmark.
Both API tests passed with OTEL_SDK_DISABLED; nine disposable-checkout installer
checks passed. The seven PromQL rules and monitoring YAML parsed successfully;
Python compilation, shell syntax and Git whitespace checks passed. These checks
did not run Docker, real Kubernetes mutation, paid Groq or Telegram delivery.

This is a local portfolio V1, not a production-grade autonomous repair system.
There are exactly three remediation scenarios: controlled Error CrashLoopBackOff,
controlled OOMKilled memory-limit mitigation, and the known readiness-flag failure.
FailedScheduling remains diagnosis/manual review, not a fourth self-healing demo.
CI/CD/security alerts and cloud deployment are outside this finish pass.

## What changes and what can mutate

The Kubernetes observer only publishes numeric gauges at bridge `/metrics`.
It never runs AI or issues an approval. Prometheus scrapes the gauges and app
metrics; real firing Alertmanager events start LangGraph -> local semantic RAG
-> Groq -> Python policy -> fresh Telegram approval -> fixed allowlisted action
-> recovery checks -> journal and verified-outcome indexing.

| Scenario | Metric trigger | Exact approved action |
| --- | --- | --- |
| CrashLoopBackOff | `opspilot_demo_crash_active=1` | Restart only deployment/opspilot-demo after live Error crash evidence |
| OOMKilled | `opspilot_demo_oom_active=1` | For this test, only memory LIMIT 256Mi -> 512Mi; request stays 64Mi |
| Readiness | `opspilot_demo_ready=0` while app scraping works | Clear only the matching `/runtime/readiness.flag`; no restart/probe disabling |

The observer never sends private manifests, env or kubeconfig as metrics.
Unknown/stale Kubernetes observations are marked unavailable, not healthy.
Actual current Prometheus firing state is checked before and after AI.
Old/healthy Pod errors, wrong identities, changed evidence, missing references,
low-confidence proposals, rejected/expired/used approvals fail closed.
The independent execution switches default off. No command auto-approves.

The new demo image `opspilot-demo:v1-final-1` contains both test controls.
Normal startup allocates no test memory. `scenario oom` explicitly adds the
known `OPSPILOT_DEMO_MEMORY_MIB=320` env only to the existing demo Deployment.
Its main process touches and retains one 320 MiB block. This deliberately exceeds
256Mi, creating a REAL kernel OOM test, not `exit 137`, `kill -9` or a fake alert.
The env persists into the approved 512Mi rollout, so replacement alone does not
remove the load. Only OOMKilled/137 plus current identity permits a proposal.
The test refuses unhealthy nodes, stale memory samples, less than 1 GiB node/WSL
headroom, an unexpected memory limit or duplicate injection. This is a bounded
test, not a guarantee against all resource contention. Do not run on production.

OOM recovery requires a new Ready Pod, zero restarts and the original Service's
HTTP 200 for 30 seconds. That is short-term mitigation, not proof a leak is fixed.
`cleanup-oom` is an explicit MANUAL cleanup, never an AI recovery. It removes
only the test env, preserves other env and the current limit, and replaces the
demo Pod once. Readiness recovery requires the same Pod/container identity,
flag absent, ready Service endpoints and Service HTTP 200.

## Run in this order (keep the Cloudflare tunnel open)

Source installation is not runtime completion. Do not reapply older updates.
The installer backs up source and refuses unknown overlapping edits. It does
not deploy, inject, enable features, change secrets/databases or push Git.

1. Run the root offline tests after installing. `RAG unavailable: RuntimeError`
   in a simulated failure test is not itself a failed suite; inspect `OK/FAILED`.
2. Ctrl+C ONLY the old bridge in its own terminal, then load new code in safe mode:

```bash
# Terminal 1: all repairs off. Loads the new source and monitoring configuration.
cd ~/devops-ai-monitoring-agent && ./venv/bin/python -m opspilot_integration.run_ai_bridge
```

The launcher explicitly recreates only its Prometheus/Alertmanager services to
reload single-file config mounts; it preserves volumes and other services.
Runtime version must be `opspilot-v1-metrics-1`. Wait for startup to complete.

```bash
# Terminal 2: prepare ONE image/probe upgrade of the existing demo. Brief demo outage.
cd ~/devops-ai-monitoring-agent && ./venv/bin/python -m opspilot_integration.scenario prepare --confirm-demo-only
```

This builds/loads the new demo image, changes only the image/readiness path in
the known Deployment and validates/reloads rules. It does not inject a fault.
Never apply the unrelated Nginx readiness YAMLs to this same Deployment.

3. Ctrl+C Terminal 1 once and start it with the three separate execution gates:

```bash
# Terminal 1: repairs are possible ONLY after your fresh Telegram approval.
./venv/bin/python -m opspilot_integration.run_ai_bridge --enable-demo-restart --enable-oom-repair --enable-demo-readiness-repair
```

```bash
# Terminal 2: deliberately inject only the bounded OOM test into opspilot-demo.
./venv/bin/python -m opspilot_integration.scenario oom --confirm-demo-only
```

Wait for REAL metrics/rules (scrape/evaluation/grouping take time). Approve only
the NEW `increase_demo_memory_limit` request showing 256Mi -> 512Mi. Wait for
`recovered` AND Service HTTP verified. A blocked or unverified result is not
success and must not be bypassed. No fresh request means inspect the actual
Prometheus alert/bridge failure, not fabricate one or automatically resend.
An unknown/lost API outcome must not be blindly repeated.

```bash
# After verified OOM recovery: remove only the artificial load; retain 512Mi.
./venv/bin/python -m opspilot_integration.scenario cleanup-oom --confirm-demo-only

# Then deliberately inject the separate readiness fault; leave the app process alive.
./venv/bin/python -m opspilot_integration.scenario readiness --confirm-demo-only
```

Approve only the fresh `clear_demo_readiness_flag` request. Expect recovered,
Service HTTP verified, and no restart. If your historical verified CrashLoop
record is missing from verification, do ONE fresh crash test, not endless repeats:

```bash
# Optional only if the previous verified crash outcome is not recorded.
./venv/bin/python -m opspilot_integration.scenario crash --confirm-demo-only

# Reads the journal and current live Service; exits nonzero if a proof is missing.
./venv/bin/python -m opspilot_integration.scenario verify
```

4. Return to safe mode: Ctrl+C Terminal 1 and run its original command WITHOUT
execution flags. Keep Cloudflare running if using Telegram callbacks.

```bash
# Terminal 2: tests + three real recorded recoveries + current health + safe-mode checks.
# Only after these pass, stage the reviewed sources, commit and normally push main.
./venv/bin/python -m opspilot_integration.finalize_v1 --publish --confirm-upload
```

The uploader leaves unrelated/unreviewed README drafts, Nginx YAMLs, backup
scripts, private env/keys/databases/vector indexes/runtime and patches unstaged.
Its credential-pattern check is best effort, not a security audit. It refuses
existing staged work, wrong branch/origin, symlinks, missing proofs or failing
tests. No force push/reset/automatic repair is used. If push authentication or
remote history blocks upload, stop: the local commit may remain safely saved.
Enter Git credentials only in Git's normal local prompt, never in ChatGPT.

### Tunnel repair / expired approval, only if needed

The scenario command verifies the public authenticated callback BEFORE injecting
a fault. If a quick tunnel expired, refresh using your existing operator command
and its explicit SWITCH confirmation; do not overwrite the saved Render backup.
For an expired unexecuted approval, `operator retry-approval ID --confirm-demo-only`
requires the original Prometheus alert AND original eligible live incident, new
AI analysis, an enabled action-specific gate and a fresh Telegram decision. It
does not renew old TTL, retry rejections or consume an old claimed approval.

## What is NOT claimed

Offline tests do not prove live Docker/Minikube/Groq/Telegram behavior. A source
installer PASS or Git push does not mean three recoveries, a green CI pipeline,
production readiness, comprehensive security, or no future bugs. Historical
CrashLoop recovery was demonstrated; the two remaining live proofs must be
observed on your machine. Default-branch dependency alerts and normal runtime
OTLP collector configuration remain separate work. Offline API validation now
honors OTEL_SDK_DISABLED to avoid test-time collector export attempts.
