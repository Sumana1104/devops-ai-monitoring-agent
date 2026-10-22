# Kubernetes ImagePullBackOff Runbook

Source: https://kubernetes.io/docs/tasks/debug/debug-application/
Category: kubernetes
Type: reviewed_runbook

## Symptoms
Pod shows ErrImagePull or ImagePullBackOff.

## Investigation
Check container image name, tag, registry availability,
imagePullSecrets, registry permissions, and Kubernetes events.

## Commands
kubectl describe pod POD_NAME -n opspilot
kubectl get deployment DEPLOYMENT_NAME -n opspilot -o yaml

## Recommended Action
Correct the verified image reference or registry access issue.
Verify that the image pulls and the pod becomes Ready.
