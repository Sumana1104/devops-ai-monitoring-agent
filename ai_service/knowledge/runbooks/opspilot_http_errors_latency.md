# Demo HTTP 5xx and latency investigations

Reference guidance, not a claim about current logs or previous remediation.
For a 5xx ratio or p95 latency alert, correlate job/instance labels, request
rate, error counters, current and previous pod logs, events, and resources.
Do not infer application health from Prometheus self-scraping or old events.

High error rate or latency alone does NOT authorize restart_deployment.
OOMKilled needs investigation of limits and memory behavior. Readiness-only
failure needs probe/listener/dependency investigation. ImagePullBackOff
needs image name/tag/registry/credentials review. In this limited V1, these
cases require manual_review; no arbitrary patch, scale or delete is permitted.
