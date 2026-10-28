# Prometheus demo metrics target down

This is reference guidance, not evidence of a real incident.
up{job="opspilot-demo"}=0 means a scrape failed. It does not alone prove
the application is crashing. Inspect Prometheus target lastError, the local
19110:8080 port forward, Docker-to-WSL host connectivity, and Kubernetes pod
state/events/logs. up{job="prometheus"}=1 only proves Prometheus self-scraping.

A network/port-forward error should lead to manual_review, not restarting
a healthy Kubernetes deployment. A separately confirmed eligible demo
crash may support a human-reviewed restart proposal with the crash-flag runbook.

After recovery, verify both Kubernetes readiness and target up=1. Do not
represent a healthy scrape as a successful end-user application request.
