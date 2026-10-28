# OpsPilot: the AI alert-to-recovery slice

This patch connects the existing controlled demo; it is not a production-ready
autonomous operator. The previously demonstrated manual approval/restart is
real evidence for that milestone, not proof this new AI path has run live.

## What was missing in the uploaded source

Your graph and Chroma retrieval code already existed. The Kubernetes watcher
could issue approval without invoking that graph. The Alertmanager handler
called AI but did not use its recommendation to gate restart approval. The
bridge did not save execution/recovery back to incident history or Chroma.
The graph also imported helper modules absent from the uploaded snapshot.

The patch uses read-only, explicit-context kubectl evidence collection so it
does not guess those unseen helpers. Existing approval expiry, atomic claim,
secret/user/chat checks and the demo-only target restriction remain. Pod UID
binding and explicit `--context opspilot` are added to execution checks.

## The connected path

1. Prometheus scrapes the demo through port 19110 and evaluates real rules.
2. Alertmanager posts its firing event to `/alerts` with the existing bearer token.
3. The bridge journals/deduplicates the event before running LangGraph.
4. Graph nodes: collect_evidence -> retrieve_knowledge -> investigate ->
   select_action -> persist_report. Groq receives current logs/state/events,
   demo Prometheus metrics, semantic RAG references and up to 25 prior summaries.
5. Only an AI restart proposal with confidence >=0.75, matching RAG references,
   and a freshly confirmed allowlisted demo crash can request approval.
   Empty/unavailable RAG, invalid output or unsupported failures fail closed.
6. Human approval still gates the deterministic executor. It claims once,
   rechecks the bound evidence, restarts only opspilot/opspilot-demo and checks
   Kubernetes rollout/ready replicas. It does not claim an HTTP check occurred.
7. The actual outcome is journalled. Only a linked human-approved, successfully
   executed and verified recovery is indexed as a verified incident in Chroma.
   AI root-cause text remains explicitly labelled a hypothesis, not a verified cause.

The watcher now ONLY maintains the crash episode/recovery state. It cannot
create approval or bypass the AI. Manual Python approval tests are still
possible, but are not silently presented as AI-driven history.

## Where your data lives

Paths are relative to the repository root, not whichever directory launched Python.

| Data | Default location | Contents |
| --- | --- | --- |
| Vector database | `data/chroma/` | Chroma collection `opspilot_incidents`; MiniLM embeddings, runbook text, verified outcome text/metadata |
| Incident journal | `data/opspilot_history.sqlite3` | Real Alertmanager events, bounded redacted log/event snapshots, graph steps, AI report, policy decision, approval link and actual outcome |
| Approval/dedup store | `opspilot_approvals.db` | Existing expiring approvals and ongoing crash-notification deduplication |
| Runbook source | `ai_service/knowledge/runbooks/*.md` | Existing runbooks plus three explicit demo/reference guides |

Existing `OPSPILOT_CHROMA_PATH` is honored; relative paths are now anchored at
the repo root. `OPSPILOT_HISTORY_PATH` can override the new journal location.
The patch does not move, erase or fabricate existing database records.

`all-MiniLM-L6-v2` generates embeddings locally on CPU. Groq generates the
diagnosis; it is NOT your embedding model. First use can download model weights
and Python dependencies; this is not a paid embedding API. Groq API account
limits/costs still apply. Do not assume unlimited free usage.

RAG is semantic search for up to 3 relevant runbooks/verified outcomes. The
last 25 actual incident summaries are ALSO provided as chronological context.
These are distinct: "top 3 similar" does not mean "last 25". Each evidence
collection reads up to 25 current + 25 previous log lines per pod, bounded in
bytes, for up to 3 demo pods. This journal is NOT a complete log platform/Loki.
Full journal snapshots remain local; only a bounded relevant excerpt reaches
Groq. Redaction is best effort, not a guarantee of complete secret/PII removal.
Never put credentials or personal data in application logs. Custom data paths
may need your own `.gitignore` rules; do not commit databases, `.env`, tokens.

## Install without overwriting unrelated work

Stop the OLD bridge with Ctrl+C in its terminal. Leave your current Cloudflare
tunnel running. Work from `~/devops-ai-monitoring-agent`.

```bash
git apply --check /mnt/c/Users/suman/Downloads/opspilot-ai-flow.patch
git apply /mnt/c/Users/suman/Downloads/opspilot-ai-flow.patch
./venv/bin/python -m pip install -r opspilot_integration/requirements-ai-flow.txt
./venv/bin/python tests/test_ai_flow.py -q
```

The first command is a compatibility check. If it fails, STOP and send the error;
do not force-apply or replace whole files. All edits are a normal Git patch.
Existing CI workflows remain; the new AI-flow workflow tests on Python 3.10/3.14
without requesting API credentials or doing real Kubernetes changes.

## Start the complete local path

Docker Desktop and the existing opspilot Minikube cluster must be running, and
the demo should initially be healthy. The original `observability/docker-compose.yml`
and existing integration/Grafana files must exist on your laptop (not all were
in this ZIP). The supervisor refuses to invent a replacement stack.

```bash
./venv/bin/python -m opspilot_integration.run_ai_bridge
```

This is safe dry-run mode. It indexes actual runbook files, brings up ONLY
Prometheus/Alertmanager using your existing Compose overlay, maintains a
reconnecting demo metrics port-forward, verifies `up{job="opspilot-demo"}=1`
FROM Prometheus, then starts one bridge. It never kills an unrelated process.
If 18008 is occupied it stops with instructions rather than starting duplicates.
The overlay runs Alertmanager as your local UID/GID so it can read the protected
mode-0600 bearer-token file without running as root. Its demo silence/notification
state uses writable `/tmp/opspilot-alertmanager` and is ephemeral on container
recreation; do not use this setup for persistent production silences.
Binding the demo forward to 0.0.0.0 lets Docker reach WSL; keep this on a trusted
local network, not a publicly exposed host. Existing bridge tunnel/auth remains.
If Docker-to-WSL connectivity fails, the supervisor stops before accepting AI work.

To actually demonstrate the human-approved demo recovery, stop this supervisor
with Ctrl+C, wait for its children to exit, and start explicitly:

```bash
./venv/bin/python -m opspilot_integration.run_ai_bridge --enable-demo-restart
```

Real changes are still impossible without an eligible fresh approval. This flag
does not grant the LLM arbitrary command execution. You retain your existing
Cloudflare URL/webhook setup; if a quick tunnel expired, re-register its new URL
using your existing safe switch procedure and preserve the Render backup.

## Demonstrate the REAL entry point, not a manually created approval

In a free terminal with kubectl in PATH, deliberately fault only the known demo:

```bash
PATH="$HOME/.local/bin:$PATH" ./venv/bin/python -m opspilot_integration.demo_failure --confirm-demo-only
```

This validates the known startup script/emptyDir, creates `/runtime/crash.flag`
ONLY in the single healthy demo pod, then terminates its app process. Container
retries retain the flag. Prometheus should see the demo scrape fail; the existing
30-second `OpsPilotDemoMetricsDown` rule plus scrape/group delays trigger the real
webhook. This command does NOT post `/alerts`, invoke the graph or create approval.

Wait for a Telegram message containing the actual AI hypothesis, confidence,
RAG references, investigation ID and policy decision. If it offers Approve,
click promptly; restart-count evidence can change during container retries.
Stale evidence blocks rather than silently expanding permission.

On an approved rollout, the new pod has an empty /runtime, starts normally,
and the supervisor reconnects the metrics forward. Verify pod Ready/Running,
Prometheus demo up=1, actual journal outcome and indexed Chroma record. If AI
recommends manual review, inspect its evidence/report; do NOT bypass the guard
or fabricate a confident restart recommendation to make a demo pass.

## See the actual data

```bash
./venv/bin/python -m opspilot_integration.ai_status
./venv/bin/python -m opspilot_integration.ai_status --incident INVESTIGATION_ID
```

Use the investigation ID printed in the AI message, not the separate approval ID.
The first command shows the exact paths, real vector record count and last 25
actual investigations/outcomes. Zero historical incidents is a truthful result;
previous manual tests are not backfilled as fake AI investigations.

If recovery succeeded but embedding/indexing failed, the SQLite outcome remains
and can be retried WITHOUT re-executing remediation:

```bash
./venv/bin/python -m opspilot_integration.ai_status --retry-index
```

## Verification boundaries

The default suite uses REAL LangGraph, FastAPI/Pydantic, approval/history SQLite,
and webhook handlers. It MOCKS Groq, Telegram, kubectl and semantic retrieval;
it proves wiring/safety logic, not model diagnostic quality or live delivery.
The optional native Chroma test uses real local Chroma with deterministic TEST
vectors, not a pretrained embedding-quality benchmark:

```bash
OPSPILOT_NATIVE_RAG_TEST=1 ./venv/bin/python tests/test_ai_flow.py -q
```

In the build environment, Chroma 1.5.9's native Rust extension raised SIGBUS while
loading. The same native smoke test PASSED with Chroma 1.5.8. The default suite
keeps this test explicitly optional so lightweight CI does not need a native
vector database/model. The patch does NOT silently downgrade your installed
Chroma or migrate/delete its data. Your existing local Chroma/model setup must
pass initialization and a live runbook retrieval before claiming semantic RAG.
If your native library fails, report the installed version and error; preserve
your existing database before changing package versions.
No real Groq call, paid API call, private credential use or live cluster test was
performed while generating this patch. Do not call this production-ready.

Build verification: 21 AI-flow tests passed with the optional native Chroma
test enabled on Chroma 1.5.8, plus all 14 existing notification-dedup tests.
This used Python 3.12, LangGraph 1.2.18, Groq SDK 1.7.0, FastAPI 0.162.0 and
Pydantic 2.14.1. The generated CI matrix will check Python 3.10/3.14 after you
push; it has not been run on GitHub here. Pretrained MiniLM semantic quality
and a real Groq diagnostic response still need validation on your laptop.

Current action coverage is deliberately narrow: the known emptyDir crash-flag
demo is eligible for restart. OOMKilled, readiness and image pull failures can
be investigated, but remain manual-review-only; no new remediation is invented.
Recovery checks currently prove Kubernetes rollout/readiness, not every HTTP
business endpoint. Automatic retry after uncertain delivery is suppressed to
avoid duplicate messages; failed investigations need explicit operator review.

After the live demo, stop the supervisor and restart it WITHOUT
`--enable-demo-restart` to return to safe development mode. Ctrl+C stops only
its own bridge and port-forward; existing Compose containers are left intact.

## JSON-output reliability follow-up

A real investigation returned Groq HTTP 400 `json_validate_failed` with an
empty `failed_generation`. This means the JSON-output request failed; the empty
field does not prove why (for example, token exhaustion is not established).
It does not invalidate the separately verified demo recovery or its stored record.

GPT-OSS 20B/120B now use `response_format.type=json_schema` with `strict=true`,
all five report fields required and `additionalProperties=false`. Other
operator-configured models retain JSON-object mode without unsupported
GPT-OSS reasoning options; the model is never silently changed. Full local
Pydantic type, enum, length, confidence-range and evidence-count validation
remains mandatory in either mode. Empty, truncated, refused or invalid reports
cannot request approval, execute Kubernetes or be indexed as verified outcomes.

The GPT-OSS request uses low reasoning effort and a maximum completion budget
of 4096 tokens. There is at most ONE additional LLM request, only for HTTP 400
`json_validate_failed`. Other request errors, invalid local reports, empty
responses or truncation are not automatically retried. SDK retries remain zero.
This is not a whole-graph, approval or Kubernetes retry. Two LLM requests can
consume more quota/cost than one; the limits are finite, not free/unlimited.

Repeated JSON failures save a safe code such as `groq_json_validate_failed` in
the existing journal and Telegram message. `ai_status` now displays that code
for failed investigations without making an LLM call. Raw provider messages,
`failed_generation`, private prompts and API keys are not added to these
diagnostics. Older failed rows retain their original class-only diagnostics.
The failed event is NOT silently replayed or relabelled as a success.

This follow-up changes no Kubernetes target, human approval, incident binding,
expiration, atomic claim, watcher, deduplication or learning eligibility rules.
Existing history/vector/approval databases and credentials are not edited by
applying the patch. Restart the safe-mode bridge after installing and testing;
the running Python process otherwise keeps using its previously loaded code.

References (checked 2026-10-28):
- https://console.groq.com/docs/structured-outputs
- https://console.groq.com/docs/reasoning

Offline verification (no paid API or live Kubernetes action):

```bash
./venv/bin/python tests/test_ai_flow.py -q
./venv/bin/python tests/test_approval_connection.py -q
./venv/bin/python tests/test_incident_dedup.py -q
```

These tests mock Groq, Kubernetes and Telegram. A successful local regression
suite is not proof that a new live Groq response has been validated yet.

## Current-alert filtering and restart noise

The local scrape target is reached through a port-forward owned by the bridge
supervisor. The earlier startup order stopped that forward on shutdown and
only reconnected it AFTER local model initialization on the next launch.
That can cause a real metrics-transport interruption exceeding the existing
30-second alert threshold even while the Kubernetes pod remains healthy.
This is a mechanism identified in the code, not a measurement proving the
exact cause/duration of every previously received event. An AI suggestion of
stale/misconfigured alerting is a hypothesis, not verified configuration damage.

Startup now launches the reconnecting metrics worker BEFORE model warmup.
It still verifies the scrape from Prometheus before accepting AI work, and
still cleans up its own worker/port-forward on exit or initialization failure.
No detached service, systemd change, new credential, extra port or daemon is
installed. A long manual shutdown gap can still interrupt this demo's metrics;
this local port-forward design is not production-grade monitoring transport.

An authenticated firing event is now rechecked against the actual current
Prometheus `/api/v1/alerts` response BEFORE spending an LLM call. All current
alert labels must match; extra notifier/external labels are allowed. For
`OpsPilotDemoMetricsDown`, the exact job/instance also needs a fresh target
scrape attempt from `/api/v1/targets` (at most 30 seconds old, with 5 seconds of
future-clock tolerance). A newly successful scrape means that queued scrape-down
event is no longer current. Merely having a Running/Ready pod is NOT enough to
clear a failed scrape. Error-rate and latency alerts are not cleared because
their target is reachable: those conditions can be real while scraping works.

The same check runs AFTER AI finishes, before new incident notifications or
approvals. If the event has cleared, the existing journal retains it as
`ignored_inactive`, including any completed AI analysis, with no new incident
message, approval, Kubernetes action or verified-outcome indexing. These are
historical events, not invented incidents or claims that no earlier issue existed.
The normal human-approved execution/result notification remains unchanged.

If monitoring cannot be verified, the state is `verification_unavailable` and
one explicit manual-review warning is sent for that event. Unknown/stale/malformed
monitoring data is NEVER labelled healthy. Persistent firing alerts remain
notifiable, even if AI recommends no action or the pod has no eligible crash.
Normal persistent event deduplication remains: unavailable/failed/filtered old
events are not automatically replayed across restarts. Inspect monitoring if
verification is unavailable rather than assuming recovery.

In-memory deduplication now includes the event's `startsAt` and labels, just as
the existing persistent journal already did. This prevents a genuinely new firing
episode from being suppressed for 15 minutes merely because its old resolved
delivery was missed. A repeated delivery of the SAME event still cannot repeat
AI/approval. Crash-approval deduplication and all execution safeguards are unchanged.

No alert rule threshold is lengthened/disabled, no Alertmanager silence is
created, and Telegram webhook registrations/secrets/Render backups are not edited.
Existing Chroma records, approvals and journal history are preserved. `ai_status`
now labels its rows as journal entries because a filtered event did not run AI.

Offline checks (external systems are mocked):

```bash
./venv/bin/python tests/test_alert_freshness.py -q
./venv/bin/python tests/test_ai_flow.py -q
./venv/bin/python tests/test_approval_connection.py -q
./venv/bin/python tests/test_incident_dedup.py -q
```

After clean patch application and tests, restart the bridge in safe mode to load
the code. No new intentional application crash or manually fabricated `/alerts`
request is needed to begin validating quiet healthy operation. Use real monitoring
state/journal observations; test-only HTTP fixtures must not be sent to the live bot.

References (checked 2026-10-28):
- https://prometheus.io/docs/concepts/jobs_instances/
- https://prometheus.io/docs/prometheus/latest/configuration/alerting_rules/
- https://prometheus.io/docs/prometheus/latest/querying/api/
