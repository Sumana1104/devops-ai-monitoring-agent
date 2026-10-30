# OpsPilot controlled readiness demo — local only

This extends the existing Python/FastAPI demo on port 8080. DO NOT apply the
untracked k8s/opspilot-demo-readiness*.yaml Nginx examples: those replace this
workload with a different container/port. They are preserved, not overwritten.

The supported incident is a deliberately injected pod-scoped readiness.flag,
not arbitrary production readiness failure. OOMKilled remediation is still NOT
implemented. This feature does not make the full V1 production-ready.

## What changes during this demo

| Check | Healthy | Injected readiness flag |
| --- | --- | --- |
| /healthz (liveness) | HTTP 200 | HTTP 200 |
| /readyz (readiness) | HTTP 200 | HTTP 503 |
| /metrics | HTTP 200, opspilot_demo_ready=1 | HTTP 200, opspilot_demo_ready=0 |
| Kubernetes Pod | Running, Ready | Running, NotReady |
| Service EndpointSlice | ready=true | ready=false |

The real Prometheus readiness rule requires ready=0 while scraping is up=1.
Probe traffic is excluded from application HTTP error-rate/latency counters.
No synthetic /alerts request is sent. The graph collects actual Kubernetes
state/probes/logs/events, live HTTP checks, Prometheus data and semantic RAG.
It may propose clear_demo_readiness_flag, but cannot approve it or execute it.

## Install source, then run offline tests

Install with the supplied hash-checking installer from the repository root.
It does not start/stop services, build an image, inject a fault, change secrets
or databases, or commit/push. Unknown local changes stop the installer.

```bash
# Enter the repository.
cd ~/devops-ai-monitoring-agent &&
# Run all root safety tests; external AI/Telegram/Kubernetes are mocked.
./venv/bin/python -m unittest discover -s tests -p 'test_*.py' -q
```

## 1. Reload one bridge in safe mode

Ctrl+C only the old bridge in its own terminal. Leave Cloudflare running.
Do not kill an old PID copied from another session.

```bash
# Start this updated bridge with BOTH execution features disabled.
./venv/bin/python -m opspilot_integration.run_ai_bridge
```

Health/operator status should show runtime_version=opspilot-readiness-1,
demo_restart_enabled=false, demo_readiness_repair_enabled=false. If the port is
occupied, the launcher reports the active settings and starts no duplicate.

## 2. Prepare the demo (one-time, explicitly changes the DEMO)

In another Ubuntu terminal, from the repository root:

```bash
# Build/load the new demo image, update only its image/readiness path,
# wait for Ready, and recreate ONLY Prometheus to remount/load the new rule.
./venv/bin/python -m opspilot_integration.readiness_demo prepare --confirm-demo-only
```

This requires the original healthy opspilot-demo:integrated-v1 on port 8080,
or a known earlier demo image upgraded by the V1 finish command. It checks ownership,
one-replica Recreate layout, probes, emptyDir and Service endpoints first.
It validates the new rule with the pinned stack's promtool. A resourceVersion/
UID-checked JSON Patch changes only the known demo image and readiness path.
It never applies the Nginx examples, changes liveness/resources or touches the
main deployment/opspilot. Setup replaces the one demo Pod once, so a brief demo
outage is expected. The existing Prometheus named data volume is preserved.
No fault/approval/repair is created by prepare.

## 3. Enable ONLY human-approved readiness repair

Stop that one bridge with Ctrl+C in its own terminal, then:

```bash
# Permit only approved readiness-flag repair; crash restarts remain disabled.
./venv/bin/python -m opspilot_integration.run_ai_bridge --enable-demo-readiness-repair
```

Do not pass --enable-demo-restart for this test. No action occurs just because
a feature is enabled: the fresh human decision and all live guards still apply.

## 4. Inject one controlled readiness fault

```bash
# Check bridge version, authenticated public callback, loaded alert rule,
# healthy metrics/Pod/Service, then create one unique /runtime/readiness.flag.
./venv/bin/python -m opspilot_integration.readiness_demo inject --confirm-demo-only
```

If the temporary tunnel expired, inject stops BEFORE changing Kubernetes.
Refresh only the Telegram route with operator webhook <NEW_HTTPS_TUNNEL_URL>;
the saved Render backup is not changed. Then rerun inject, not a crash helper.
If the fresh metric is not present yet, wait for the next scrape; do not force it.

Expect a real OpsPilotDemoReadinessFailure alert after the configured scrape,
20-second rule duration and Alertmanager grouping delay (not an exact deadline).
Groq/RAG must support the proposed action; otherwise the result is manual review.

## 5. Review the new Telegram request and verify recovery

Approve ONLY the fresh clear_demo_readiness_flag request for opspilot/opspilot-demo
within its 10-minute lifetime. Reject never repairs anything. An expired or
previously used decision never executes. Do not click an old crash approval.

The executor revalidates exact deployment UID/generation, Pod UID, container ID,
restart count, fault ID, image/probes and Service before/after atomic claim.
Its fixed Python function removes ONLY the matching regular readiness.flag.
Symlinks, a different ID, a replacement/restarted container or an unknown failure
are blocked. It never disables probes or restarts the workload for readiness.

Success requires Ready replicas, the SAME Pod/container, /readyz 200, no flag,
a ready Service endpoint and HTTP 200 through the Kubernetes Service.
Only then is a linked human-approved recovery indexed in history.
Look for recovered, readiness flag repair=true, Service HTTP verified=true;
restart_command_succeeded=false is CORRECT for this readiness scenario.

```bash
# Inspect the real demo Pod without changing it.
minikube --profile opspilot kubectl -- --context opspilot -n opspilot get pods -l app=opspilot-demo
# Inspect actual saved investigation/outcome/index state.
./venv/bin/python -m opspilot_integration.ai_status
```

The Pod UID should be unchanged by flag repair. If a command succeeds but Service
verification fails, the result is recovery_not_verified, not a fabricated success.

For an EXPIRED, unexecuted pending/eligible-blocked readiness request only,
operator retry-approval <APPROVAL_ID> --confirm-demo-only reruns fresh evidence/
AI/policy and issues a new request if the original fault/alert still match.
It never renews the old TTL, retries a rejection/claimed action or clears a flag.
Uncertain delivery is never automatically resent.

## Return to safe mode

Ctrl+C the bridge and restart run_ai_bridge without execution flags. Confirm
BOTH feature settings are false with operator status. Leave the tunnel alone
unless explicitly restoring the previous Render webhook.

## Validation boundary

Offline tests use real LangGraph/Pydantic/FastAPI/SQLite and deterministic mocked
external boundaries, including full alert -> approval -> repair -> Service proof
-> indexing. They are not a real Docker build, Kubernetes deployment, Telegram
button delivery or Groq response. Those must be verified on the laptop. The
existing default-branch vulnerability alerts and OTLP collector configuration
are not fixed here. OOMKilled is still manual review; no new cloud costs/services.

## Separate API dependencies from the local AI bridge

The default ai_service/requirements.txt now delegates to requirements-api.txt.
Docker and API tests use only that web API dependency list. LangGraph, Chroma
and Sentence Transformers remain in opspilot_integration/requirements-ai-flow.txt
for the LOCAL bridge; no installed packages in your existing venv are removed.
This does not move the full local agent into the API image or automate deploying
the bridge. Existing CI was canceled while downloading a 6.49 GB image artifact;
the build log shows PyTorch/CUDA was pulled in through Sentence Transformers.
The new size guard blocks an API image above 1 GiB BEFORE artifact upload. Its
actual new Docker size/CI result still need verification; no scan is bypassed.
