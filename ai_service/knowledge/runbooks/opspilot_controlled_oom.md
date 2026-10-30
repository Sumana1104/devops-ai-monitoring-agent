# Operator-induced bounded OOM scenario, not a spontaneous production failure

Only the known local opspilot/opspilot-demo image opspilot-demo:v1-final-1
supports the explicit test env OPSPILOT_DEMO_MEMORY_MIB=320. Normal startup
without this env does not allocate the test block. Do not infer this env from
a generic OOM: require the explicit operator-demo log and current evidence.

The main Uvicorn process retains one fixed 320 MiB page-touched allocation.
At the known 256Mi cgroup limit the kernel may kill the main process, giving
current OOMKilled plus exit 137. Exit 137 alone is NOT proof of OOMKilled.
At 512Mi the block persists through the rollout, allowing an actual comparison
of the bounded memory-limit mitigation. The original 64Mi request is unchanged.

Prometheus observes the read-only opspilot_demo_oom_active gauge. A firing
OpsPilotDemoOOMKilled alert starts the existing LangGraph/RAG/Groq pipeline.
Fresh Pod/Deployment identities, current OOM evidence and no node memory
pressure must support any proposal. Only Python fixes the 256Mi -> 512Mi step;
it requires a fresh human Telegram approval and the OOM execution feature gate.

Success requires a new Ready Pod with zero restarts and the original Service
HTTP 200 for 30 seconds. This is a controlled test of temporary mitigation,
not proof that a production memory leak was fixed or will never recur.
After verified recovery the operator can explicitly remove the test env using
scenario cleanup-oom --confirm-demo-only. Cleanup is manual and must never be
indexed as AI recovery. It retains the current memory limit and other env vars.
