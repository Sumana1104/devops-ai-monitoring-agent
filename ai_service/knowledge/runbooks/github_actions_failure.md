# GitHub Actions CI/CD Failure Runbook

Source: https://docs.github.com/en/actions/how-tos/monitor-workflows/use-workflow-run-logs
Category: cicd
Type: reviewed_runbook

## Symptoms
GitHub Actions build, test, scan, or deployment job fails.

## Investigation
Find the failed job and step.
Inspect the first meaningful error in workflow logs.
Check dependency installation, tests, Docker build,
permissions, security findings, and deployment configuration.

## Commands
gh run list --limit 10
gh run view RUN_ID
gh run view RUN_ID --log-failed

## Recommended Action
Fix the root cause identified by the failing step.
Do not disable security checks merely to make the pipeline pass.
Verify the next workflow run succeeds.
