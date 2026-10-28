#!/usr/bin/env bash
# kubectl port-forward is tied to one pod; reconnect after a rollout restart.
set -u
child=''
finish() {
  if [[ -n "$child" ]]; then kill "$child" 2>/dev/null || true; fi
  exit 0
}
trap finish TERM INT
while true; do
  kubectl --context opspilot -n opspilot port-forward --address 0.0.0.0 deployment/opspilot-demo 19110:8080 &
  child="$!"
  wait "$child" 2>/dev/null || true
  child=''
  sleep 2 & wait "$!" || true
done
