# Kubernetes OOMKilled Runbook

Source: https://kubernetes.io/docs/tasks/configure-pod-container/assign-memory-resource/
Category: kubernetes
Type: reviewed_runbook

## Symptoms
Container terminates with reason OOMKilled.

## Investigation
1. Check pod status and previous termination reason.
2. Inspect container memory limits and requests.
3. Compare memory usage against configured limits.
4. Review application logs before termination.
5. Investigate memory leaks or increased workload.

## Commands
kubectl describe pod POD_NAME -n opspilot
kubectl top pod POD_NAME -n opspilot
kubectl logs POD_NAME -n opspilot --previous --tail=100

## Recommended Action
Investigate the confirmed cause before changing memory limits.
Verify pod readiness and memory behavior after any approved correction.
