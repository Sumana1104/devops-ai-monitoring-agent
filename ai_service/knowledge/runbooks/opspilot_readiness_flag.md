# Controlled OpsPilot readiness flag (local demo ONLY)

Use CURRENT Kubernetes/HTTP evidence, not this reference alone.
Target: context opspilot, namespace opspilot, deployment/container opspilot-demo.
Image: opspilot-demo:v1-final-1. One replica, Recreate strategy, port 8080.

The intentionally injected /runtime/readiness.flag contains a unique fault ID.
While it exists, /readyz returns 503 and opspilot_demo_ready is 0. /healthz and
/metrics continue returning 200: failed readiness must not kill the container.
Kubernetes should show Running/NotReady and its Service EndpointSlice ready=false.
This is NOT a MetricsDown or CrashLoopBackOff incident.

Only when local read-only tools confirm the exact live Pod UID/owner, container
identity, known image/probes, fault ID, readiness 503/liveness 200, and no ready
Service endpoint may the AI propose clear_demo_readiness_flag. It cannot approve
or execute that proposal. Other readiness faults require manual_review.

Human Telegram approval, a fresh incident binding and the separate explicit
readiness-repair feature gate are required. The deterministic function removes
ONLY that matching /runtime/readiness.flag in the same demo container. It does
not disable probes, alter Service selectors, increase limits or restart a Pod.

Recovery must confirm rollout/Ready replicas, /readyz 200, no remaining flag,
a ready Service EndpointSlice and HTTP 200 through the Kubernetes Service.
Index only the linked, approved and verified outcome. A repair command returning
success without those checks is NOT verified recovery.
