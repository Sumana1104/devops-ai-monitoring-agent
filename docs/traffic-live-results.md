# Traffic scaling: live validation — 2026-10-30

Prometheus detected sustained overload. OpsPilot investigated it and sent
a fresh Telegram approval. Human approval authorized only the traffic
Deployment's replica change from one to two.

Verified:
- Two Ready Pods, zero restarts, both serving the same Locust Job.
- Server throughput: 1.38 -> 2.78 requests/second.
- Client p95 latency: 7.70 -> 5.10 seconds.
- Server histogram-estimated p95: 6.38 -> 6.17 seconds.
- Recorded server and client error ratios: zero.

Outcome: approved scaling succeeded and performance partially improved.
Full recovery was NOT verified: latency exceeded the 400 ms target,
and server-estimated p95 did not improve by the required 25 percent.
Both Pods remained near their individual 500m CPU limits.

The CPU-heavy workload was not calibrated to establish that this laptop
could meet the 400 ms target. This is a local closed-loop load experiment,
not production capacity proof. The three original recovery proofs remain
unchanged. No further automatic scaling or rollback was performed.

Code changes preserve failed-check diagnostics and allow an explicit,
guarded retry of a failed/manual-review investigation with a new request.
Human approval, live evidence validation, and recovery thresholds remain.

Local regression result: 334 tests, OK, one skipped.
Metrics-server sampling was changed locally from 60s to 15s; that runtime
setting must be checked when recreating the cluster.
