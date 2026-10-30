# Live Kubernetes resource incidents: local OpsPilot policy

Sources:
- https://kubernetes.io/docs/tasks/configure-pod-container/assign-memory-resource/
- https://kubernetes.io/docs/tasks/debug/debug-application/debug-running-pod/

Type: reviewed_runbook

This runbook covers real state in context opspilot, namespace opspilot,
Deployment opspilot-demo. It does not authorize any action.

## OOMKilled

Trust the container termination reason OOMKilled and exit 137 together. Exit
137 alone or an old termination on a currently Ready Pod is insufficient.
Do not claim that an OOM proves a memory leak. Inspect configured limits and
node MemoryPressure; unavailable evidence requires manual review.

For this one allowlisted app, Python policy permits proposing
increase_demo_memory_limit only when current resource_evidence.repairable is
true. The only possible changes are 64Mi -> 128Mi, 128Mi -> 256Mi, or
256Mi -> 512Mi. The number comes from code, never from the LLM. This is a
temporary mitigation, not an automatic or proven root-cause fix.

A fresh Telegram approval, separate execution feature flag, unchanged Pod and
deployment identities, and an atomic resourceVersion check are required. Only
the memory LIMIT changes. Requests, images, probes and placement are preserved.
The resulting rollout replaces the Pod. Verify a new Ready Pod, no restarts,
the original Service identity, ready endpoints, and Service HTTP 200 for 30s.
Outside this policy, recommend manual_review.

## FailedScheduling

Require an unscheduled Pending Pod, PodScheduled=False/Unschedulable, and a
FailedScheduling Event for that same Pod UID. Explain the actual event:
capacity, requests, selectors/affinity, taints or another restriction. A Pending
Pod alone does not establish the cause. No fabricated logs or memory usage.

Recommend manual_review. Do not restart the Deployment, lower requests, remove
placement rules/taints or add/change nodes automatically. Such changes require
review of the real scheduling constraint and are not implemented by this update.
