# Locust-driven CPU capacity experiment (not a Kubernetes fault injection)

Target ONLY opspilot/opspilot-traffic, never opspilot or opspilot-demo.
Locust runs in-cluster and sends real requests through the ClusterIP Service to
/work. Work is fixed-size PBKDF2 computation; there are no sleeps or forced 500s.
One processing worker per replica and a bounded queue limit the experiment.
One user provides the baseline; eight users provide a sustained closed-loop burst.
High request rate alone is NOT a reason to scale. Require sustained measured
p95 above 400ms, current CPU use above 65% of its fixed container CPU limit,
healthy ready backends, no HPA for this Deployment, no OOM/unready backend,
and enough node resources. Rate limits, downstream failure or unavailable
monitoring require manual review, not blind scaling.
Allowed proposal: scale_demo_replicas, ONLY one to two replicas. Fresh Telegram
approval and separate explicit traffic scaling gate required. No extra scaling,
automatic scale-down, probe edits, CPU/memory edits or Deployment restarts.
After scale-up, preserve the same active Locust job, users and workload. Verify
two ready original-Service endpoints, requests served by both Pods, p95 below
400ms and at least 25% below baseline, error ratio below 1%, request rate not
below 80% of baseline and 30 seconds of stability. Otherwise report outcome not
verified; scaling is not guaranteed to fix latency on a single laptop node.
The load is synthetic test traffic, but its resource/latency measurements are real.
Offline mocks are NOT proof of load balancing or measured live recovery.
