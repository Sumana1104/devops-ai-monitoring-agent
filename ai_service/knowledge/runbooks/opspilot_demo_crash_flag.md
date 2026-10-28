# Controlled OpsPilot demo: crash.flag and pod-scoped emptyDir

This is runbook guidance, NOT evidence of a current or past incident.
Applies ONLY to namespace opspilot, deployment opspilot-demo, context opspilot.

This demo's known startup command checks /runtime/crash.flag. If it exists,
the command logs "Intentional demo CrashLoopBackOff" and exits with status 1.
The /runtime mount uses emptyDir, volume ephemeral-demo-state. The flag
survives CONTAINER retries within the same pod. Replacing the pod with a
deployment rollout restart gives the replacement pod an empty directory.

Before proposing restart_deployment: inspect current pod/container state,
previous logs, events and the deployment's actual volume configuration.
An active CrashLoopBackOff with previous Error/nonzero exit and NotReady
must be verified by code; a log line alone is not enough. Only propose the
allowlisted demo restart, never a shell command chosen by an LLM.

The operator must approve a fresh, expiring request. Execution must claim
that approval once, bind it to the pod UID/evidence, and recheck the live
crash immediately before restarting. Verify rollout and ready replicas.
Do not claim HTTP behavior was verified unless an HTTP check was performed.

Do NOT generalize this recovery to broken images, OOMKilled, corrupt
persistent data, or arbitrary CrashLoopBackOff causes. Those require diagnosis.
