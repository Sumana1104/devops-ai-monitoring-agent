# OpsPilot stability handoff

## What is and is not complete

The human-approved demo **CrashLoopBackOff** recovery has been demonstrated live:
a genuine Prometheus event was investigated; after an operator-requested retry of
that same event, Telegram approval led to a demo rollout, Kubernetes recovery
verification and an indexed outcome. A retry is not a new automatic Prometheus
trigger, and an AI hypothesis is not independent proof of root cause.

This update is a source/test candidate, **not a declaration that the entire V1 is
finished or production-ready**. No changes from this package have yet been applied
to the laptop, pushed, run in GitHub Actions, or live-validated.

| Frozen V1 incident | Current honest status |
| --- | --- |
| Application runtime crash | Approved demo recovery demonstrated; counter fix and operational regressions covered offline. |
| OOMKilled/memory limit | Runbook and manual-review safety behavior exist. A dedicated approved remediation/demo is still unfinished. |
| Readiness failure | Runbook and manual-review safety behavior exist. A dedicated approved remediation/demo is still unfinished. |

A Locust call to the deliberately failing `/error` or slow `/slow` endpoint does
not become an eligible CrashLoopBackOff. Restarting will not fix hardcoded endpoint
behavior. Do not weaken the policy to pretend that it does.

A **currently firing scrape failure** with a healthy Pod can be a real monitoring
path problem. The last manual-review notification did not restart anything and
did not undo the successful recovery. Do not label it a proven app crash or hide
it without confirming the actual scrape state.

## Changes in this update

- Preserve the applied counter fix: same Pod UID/crash signature and valid
  nondecreasing integer restart counts; unchanged expiry, feature flag, atomic
  claim and revalidation before execution.
- Bind fresh approval creation to the evidence just checked, including Pod UID.
- Start the incident bridge even when scraping remains down; state explicitly
  that monitoring is degraded, never claim the app/monitoring is healthy.
- If port 18008 is occupied, report the **existing** bridge mode/version.
  Never terminate or replace an unowned process. Do not confuse a failed new
  process's startup log with the running bridge's mode.
- Reusable status, webhook refresh and operator retry commands replace long
  one-off pasted programs.
- Check public route and shared-secret authentication before callback refresh or
  a fresh operator retry. Empty test payloads cannot approve or restart anything.
- Never overwrite the former Render webhook backup. Preserve it even when a
  setWebhook timeout has an uncertain result. Restore still requires explicit
  RESTORE and the local secret must match the former Render secret.
- Keep credential-bearing Telegram URLs out of notification/API exceptions.
- The main CI workflow now requires the reusable AI/safety suite before building
  and publishing, in addition to the existing API/CodeQL/build gates.
  Trivy vulnerability findings remain report-only as configured; scanner/tool
  execution failures are still visible and the existing gate still fails closed.
- No new clouds, agents, paid services, permissions, Kubernetes actions or secrets.

## One foreground bridge supervisor

From the project root, after explicitly stopping the **old bridge only**, use:

```bash
# Monitoring mode; real restarts disabled.
./venv/bin/python -m opspilot_integration.run_ai_bridge
```

For an explicitly approved demo session only:

```bash
./venv/bin/python -m opspilot_integration.run_ai_bridge --enable-demo-restart
```

The supervisor owns one bridge and a reconnecting metrics forward. It reuses an
external forward on 19110 while that forward lives and never kills it.
Keep just one bridge supervisor. Starting it again is not how to change a
running process's environment.

The Cloudflare quick tunnel remains a separate foreground process:

```bash
cloudflared tunnel --protocol http2 --url http://127.0.0.1:18008
```

This is a local demonstration, not an always-on production service. Laptop/WSL
sleep, closing terminals and network outages can interrupt it. This package does
not claim that ephemeral tunnel URLs are permanent or automatically managed.

## Short operator commands

```bash
# Read the active bridge mode/version and Telegram delivery information.
./venv/bin/python -m opspilot_integration.operator status

# Verify a NEW tunnel and refresh Telegram; type SWITCH when asked.
./venv/bin/python -m opspilot_integration.operator webhook https://YOUR-NEW-HOST.trycloudflare.com

# Read stored incidents without running AI or remediation.
./venv/bin/python -m opspilot_integration.ai_status

# Re-investigate an expired, unexecuted request for the SAME still-live crash.
# Replace EXPIRED_APPROVAL_ID; this creates a fresh request, never approves it.
./venv/bin/python -m opspilot_integration.operator retry-approval EXPIRED_APPROVAL_ID --confirm-demo-only
```

The retry command verifies the running updated bridge, actual callback route,
original recorded alert lineage and same live Pod/crash before running fresh
LangGraph/RAG/Groq analysis. It checks again afterwards and creates at most one
child request per parent. Expired records are never extended. Claimed, rejected,
previously executed or still-valid requests cannot be renewed by this command.

Do not retry an already recovered incident or inject another fault to repair a
tunnel. A request to retry is not an approval; the new Telegram button is still
required. Missing/changed evidence and unknown monitoring fail closed.

## Offline verification

```bash
# Existing venv; external boundaries are mocked in the root unit suite.
./venv/bin/python -m unittest discover -s tests -p 'test_*.py' -q
```

Default lightweight tests skip one optional native Chroma test. It can be enabled
with `OPSPILOT_NATIVE_RAG_TEST=1` when the existing compatible Chroma installation
is available. That test uses a temporary collection and deterministic fixture
embeddings, not the laptop's learned incidents and not a semantic-quality score.
The `RAG unavailable: RuntimeError` line in a negative test is expected.

Validated here on Python 3.12: 129 root tests with the native Chroma option enabled
and 12 mocked workflow-authorization cases. The separate API pytest suite could
not be collected in this validation environment because its OTLP HTTP exporter
dependency is absent; this is not evidence that the laptop's app is broken.
Its existing CI dependency-install/test job remains in place. Python 3.10/3.14
GitHub matrix execution remains unverified until
the update is pushed. Do not reinstall or downgrade the working Chroma/venv merely
to apply a source update.

## Repository and release boundaries

- Reviewed source snapshot: main `37f98b23405fd01f0e71423b0f0e1db812e4998b`.
- The review found GitHub's default branch set to `ecr`, while this work is on
  `main`. This package does not change that setting. Review the intended default
  branch deliberately rather than changing it during incident recovery.
- The existing CI image/deployment targets `ai_service`; the host-side local
  integration bridge is a separately started process. This update does not claim
  automatic deployment of that host process.
- Dependency vulnerability notices have not been resolved by this package; no
  package list or reachability assessment has been invented.
- Root README files, local backups, experimental manifests, .env files, databases,
  Chroma content, runtime tokens and saved webhook state are not in the payload.

## Fixed finish line

1. Apply/check this candidate and verify the active runtime version once; do not
   repeat the already demonstrated crash solely to accumulate success messages.
2. Implement and live-validate the still-missing OOM/readiness demo/remediation
   paths with explicit incident-specific guards. They are not finished by a
   crash-only rollout or a runbook.
3. Run the full CI and record one final acceptance result per frozen scenario,
   then document the project honestly and stop expanding scope.
