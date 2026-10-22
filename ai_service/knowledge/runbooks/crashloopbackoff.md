# Kubernetes CrashLoopBackOff Runbook

Source: https://kubernetes.io/docs/tasks/debug/debug-application/debug-running-pod/
Category: kubernetes
Type: reviewed_runbook

## Symptoms
Container repeatedly crashes and restarts.

## Investigation
Check container exit codes, previous logs, application startup errors,
missing configuration, failed probes, and OOMKilled status.

## Commands
kubectl describe pod POD_NAME -n opspilot
kubectl logs POD_NAME -n opspilot --previous --tail=100

## Recommended Action
Fix the verified application or configuration problem.
Do not repeatedly restart a container without addressing the cause.
