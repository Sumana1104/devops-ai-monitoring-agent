# Real OOMKilled and FailedScheduling support

> Historical update notes. The metrics-first V1 finish update supersedes the
> watcher-triggered investigation and activation details below. Current commands,
> three-scenario scope and upload checks are in [v1-finish.md](v1-finish.md).

This update follows the installed readiness source update on commit d3ea4ee.
It uses your existing monitored app: context `opspilot`, namespace `opspilot`,
Deployment/container `opspilot-demo`. It does not create workloads, inject faults,
build an image, change CI/CD, or demonstrate live recovery during installation.
Readiness work is deferred; its existing source is retained.

## What the code actually does

| Function | Job | Can change Kubernetes? |
| --- | --- | --- |
| `resource_incidents.inspect_incident` in ai_service | Reads the current Deployment, Pod ownership, termination record, matching Events, node memory pressure and Service | No |
| `process_current_incident` in opspilot_integration | Reserves one real incident per Pod UID/type; runs the existing LangGraph, local RAG and Groq diagnosis; reports through Telegram | No |
| Existing `collect_evidence`, `investigate`, `select_action` | Collect evidence, obtain a validated AI report and enforce Python action policy | No |
| `request_oom_approval` | Shows the exact memory change and stores a 10-minute, identity-bound Telegram approval | No |
| `execute_oom_remediation` | Rechecks approval and live evidence; atomically changes one memory LIMIT field | Only after approval and explicit execution flag |
| `verify_recovery` | Requires the approved rollout, a new Ready Pod with no restarts, original Service/ready endpoints and Service HTTP 200 for 30 seconds | No |

The Kubernetes watcher runs these checks about every 15 seconds. An AI request
can take longer, so this is not a guaranteed notification deadline. No Prometheus
alert is fabricated. The existing crash-recovery path remains available.

## OOMKilled scope and limits

An active unhealthy container must have Kubernetes reason `OOMKilled` AND exit
137. An old OOM record on a healthy Pod, exit 137 alone, a changed owner/Pod,
unknown layout or another current failure cannot authorize this repair.

Only these fixed limit changes are supported: **64Mi to 128Mi, 128Mi to 256Mi,
or 256Mi to 512Mi**. The LLM cannot choose a number. Node MemoryPressure must be
False. The target is only the existing one-replica `opspilot-demo`, using its
known local Python image and Recreate strategy. Other workloads/limits require
manual review. The mutation preserves memory requests, CPU resources, images,
probes, selectors and node configuration. It replaces the Pod through a rollout.

A limit increase is a temporary mitigation; it does not establish that the
root cause was an undersized limit or that a memory leak has been fixed.
Thirty seconds of verified recovery does not guarantee future stability.

## FailedScheduling scope

The agent recognizes a current unscheduled Pending Pod, the Unschedulable
condition and a FailedScheduling Event for that exact Pod UID. The existing AI
explains the actual scheduler evidence (for example, insufficient CPU, memory,
taints, affinity or selectors). It **reports for manual review**. This update
does not implement automatic FailedScheduling remediation or claim a third
self-healing scenario is complete. It never removes legitimate placement rules
or lowers resource requests merely to make a Pod run.

## Installation and activation

The single downloadable installer uses Python's standard library. Run it from
the project root with `--explain` for this scope, `--show-diff` to inspect the
exact source diff, or no flags for a preview. `--apply` writes only the listed
source/docs/tests after exact SHA-256 compatibility checks and a local Git
source backup. Unknown overlapping edits, symlinks or staged work stop it.
It does not commit, push, install packages, change keys/databases or restart
anything. Existing READMEs, untracked Nginx manifests and private files are not
part of the update. The installer verifies source bytes, not runtime behavior.

After source installation, stop only the old Python bridge with Ctrl+C in its
own terminal. Keep the existing Cloudflare tunnel running. Load the updated code:

```bash
# Enter the existing project and start monitoring with all mutations disabled.
cd ~/devops-ai-monitoring-agent && ./venv/bin/python -m opspilot_integration.run_ai_bridge
```

To allow a later fresh Telegram approval to execute the bounded OOM repair,
stop that bridge in its terminal and start it with the separate execution flag:

```bash
# Enables the OOM execution path; it never bypasses your Telegram approval.
cd ~/devops-ai-monitoring-agent && ./venv/bin/python -m opspilot_integration.run_ai_bridge --enable-oom-repair
```

This command retains the existing launcher/observability startup behavior. It
does not start another copy if port 18008 already belongs to a bridge. The new
runtime version is `opspilot-resource-incidents-1`. Crash and readiness execution
remain separately gated. Do not enable them just for this OOM change.

For a read-only current incident report in another terminal:

```bash
# Reads current Kubernetes evidence; performs no AI call or mutation.
./venv/bin/python -m opspilot_integration.resource_incidents status
```

Optional `investigate` invokes the normal diagnosis/Telegram path once for a
real current incident; it does not create a fault or approve anything. No
incident means no fabricated success. One automatic investigation per Pod
UID/type prevents duplicate Telegram approvals during repeated OOM restarts.
An expired, failed or uncertain notification is not automatically renewed.
The existing `operator retry-approval` does not support the new OOM action;
such a case requires review rather than forcing/reusing an old approval.

## Data and safety

The existing Groq path receives bounded/redacted application logs, Kubernetes
status/events/configured limits, metrics and RAG context. Telegram receives the
diagnosis, target, proposal and outcome. This is external data transfer through
the providers already configured by your project. This update does not upload
your kubeconfig, `.env`, credentials or complete Pod manifests. Existing
redaction is best effort, so application logs still need appropriate handling.
New code does not read Secrets or turn LLM text into shell commands.

The OOM action requires your configured Telegram user/chat, unexpired approval,
a one-time atomic claim, unchanged live identities and a separate feature flag.
An atomic UID/resourceVersion test rejects concurrent Deployment edits. A
command failure or unverified HTTP/Pod recovery is reported as failure or
unverified recovery, never indexed as successful recovery.

Offline tests simulate external Kubernetes/Groq/Telegram while using real
LangGraph, SQLite and ASGI callbacks. Live recovery on your Minikube cluster
has not been verified here. This is a focused review of the added paths, not
a comprehensive security audit. Earlier dependency alerts and OTLP logging
configuration are not resolved by this update. CI/CD remains deferred.

References: [Kubernetes memory resources](https://kubernetes.io/docs/tasks/configure-pod-container/assign-memory-resource/)
and [Kubernetes Pod debugging](https://kubernetes.io/docs/tasks/debug/debug-application/debug-running-pod/).
