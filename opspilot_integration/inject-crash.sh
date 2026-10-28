#!/usr/bin/env bash
# Controlled recoverable crash: marker lasts only for this pod's emptyDir.
# A fresh pod after APPROVED rollout restart does not have the marker.
set -Eeuo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
[[ "$(kubectl config current-context)" == 'opspilot' ]] || { echo 'STOP: wrong Kubernetes context'; exit 1; }
image="$(kubectl --context opspilot -n opspilot get deployment opspilot-demo -o jsonpath='{.spec.template.spec.containers[0].image}')"
[[ "$image" == 'opspilot-demo:integrated-v1' ]] || { echo "STOP: wrong image ($image), refusing crash injection"; exit 1; }
ready="$(kubectl --context opspilot -n opspilot get deployment opspilot-demo -o jsonpath='{.status.readyReplicas}')"
[[ "$ready" == '1' ]] || { echo "STOP: demo not healthy ($ready ready)"; exit 1; }
read -r -p 'Temporarily crash ONLY opspilot-demo for a controlled approval/recovery demonstration? Type CRASH: ' answer
[[ "$answer" == 'CRASH' ]] || { echo 'Cancelled'; exit 0; }
pod="$(kubectl --context opspilot -n opspilot get pods -l app=opspilot-demo -o jsonpath='{.items[0].metadata.name}')"
[[ "$pod" == opspilot-demo-* ]] || { echo 'STOP: unexpected pod'; exit 1; }
echo "Injecting intentional crash into demo pod $pod (not the main app)."
kubectl --context opspilot -n opspilot exec "$pod" -- sh -c 'touch /runtime/crash.flag && kill -TERM 1' || true
echo 'The same pod will CrashLoopBackOff. The local watcher should send Telegram Approve/Reject.'
echo 'The approval executor can restart ONLY this demo deployment; the fresh pod clears the crash marker.'
echo 'If you do not approve, the demo will stay down until manually restored using: kubectl --context opspilot -n opspilot rollout restart deployment/opspilot-demo'
