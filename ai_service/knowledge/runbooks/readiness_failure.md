# Kubernetes Readiness Probe Failure Runbook

Source: https://kubernetes.io/docs/tasks/configure-pod-container/configure-liveness-readiness-startup-probes/
Category: kubernetes
Type: reviewed_runbook

## Symptoms
Pod is Running but Ready is False.
Service traffic may not reach the affected pod.

## Investigation
Check readiness probe path, port, timeout, application response,
startup behavior, dependencies, and Kubernetes events.

## Commands
kubectl describe pod POD_NAME -n opspilot
kubectl logs POD_NAME -n opspilot --tail=100
kubectl get endpointslices -n opspilot

## Recommended Action
Fix the underlying failure or incorrect probe configuration.
Verify readiness and service endpoints.
