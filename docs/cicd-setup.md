# Learn Kubernetes with your monitoring agent

Start with a local Minikube cluster in WSL Ubuntu. Deploy the application manually once so you can see what a Deployment and Service do, then connect GitHub Actions to that cluster. Minikube is a learning environment on your laptop, not a continuously available production service.

## 1. Check Docker in WSL Ubuntu

Open the **Ubuntu terminal** and run:

```bash
docker version
docker info --format '{{.OSType}}'
uname -m
```

Docker must report a running server and `linux`. If it cannot connect, start Docker Desktop and enable **Settings → Resources → WSL Integration → Ubuntu**. Use Linux containers. The installation commands below assume `uname -m` returns `x86_64`.

Minikube needs at least 2 CPUs, 2 GB of free memory, and 20 GB of free disk space. This lab requests 3 GB of memory for Kubernetes and the app.

## Apply the accompanying patch

Copy `opspilot-cicd.patch` into your repository root. Check that it applies cleanly before changing files:

```bash
git apply --check opspilot-cicd.patch
git apply opspilot-cicd.patch
git status --short
git diff --stat
```

The patch was prepared against commit `574fef88934824dce1accd538e0619b6a70c737a`. If the check reports a conflict, stop and share that error so the patch can be adapted to your current files. Applying the patch does not push it to GitHub; you can complete the local lesson first.

## 2. Install tools and start the cluster

Install Minikube in Ubuntu if it is not already installed:

```bash
curl -fLo minikube-linux-amd64 https://github.com/kubernetes/minikube/releases/latest/download/minikube-linux-amd64
sudo install minikube-linux-amd64 /usr/local/bin/minikube
rm minikube-linux-amd64
minikube version
minikube start --profile opspilot --driver=docker --cpus=2 --memory=3072
minikube --profile opspilot kubectl -- get nodes
```

Expect a node with status `Ready`. To install a standalone `kubectl` matching this cluster, download its Kubernetes version and verify the checksum:

```bash
opspilot_k8s_version=$(minikube --profile opspilot kubectl -- version -o json | python3 -c 'import json,sys; print(json.load(sys.stdin)["serverVersion"]["gitVersion"])')
curl -fLo kubectl "https://dl.k8s.io/release/${opspilot_k8s_version}/bin/linux/amd64/kubectl"
curl -fLo kubectl.sha256 "https://dl.k8s.io/release/${opspilot_k8s_version}/bin/linux/amd64/kubectl.sha256"
echo "$(cat kubectl.sha256)  kubectl" | sha256sum --check
```

Only after the checksum passes:

```bash
sudo install -m 0755 kubectl /usr/local/bin/kubectl
rm kubectl kubectl.sha256
kubectl --context opspilot get nodes
```

Use the same Ubuntu user for Docker, Minikube, and the runner. Do not start Minikube as root.

## 3. Deploy manually for the first lesson

From the repository root in Ubuntu, after applying the accompanying patch:

```bash
docker build --pull -t opspilot:lesson-1 ./ai_service
minikube --profile opspilot image load opspilot:lesson-1
kubectl --context opspilot apply -f k8s/namespace.yaml
kubectl set image --local -f k8s/deployment.yaml opspilot=opspilot:lesson-1 -o yaml | kubectl --context opspilot apply -f -
kubectl --context opspilot apply -f k8s/service.yaml
kubectl --context opspilot -n opspilot rollout status deployment/opspilot --timeout=180s
kubectl --context opspilot -n opspilot get deployments,pods,services
kubectl --context opspilot -n opspilot port-forward service/opspilot 9000:9000
```

Leave port-forward running and open **http://localhost:9000/docs** in your browser. This manual lesson is independent of the CI security gate; the automated pipeline still blocks publishing and deployment when scans fail.

| Resource | What it does in this app |
| --- | --- |
| Namespace | Groups this lab's resources under `opspilot` |
| Deployment | Maintains the requested number of app Pods and manages updates |
| Pod | Runs your application container |
| Service | Gives the Pods a stable network address |
| Probes | Check startup, readiness, and whether the app is still responding |
| Secret | Supplies the OpenRouter API key without adding it to Git |

The root endpoint works without an API key. To use `/ai`, create the Secret from a masked local prompt:

```bash
read -rsp 'OpenRouter API key: ' opspilot_key
echo
kubectl --context opspilot -n opspilot create secret generic opspilot-api --from-literal=GROQ_API_KEY="$opspilot_key" --dry-run=client -o yaml | kubectl --context opspilot apply -f -
unset opspilot_key
kubectl --context opspilot -n opspilot rollout restart deployment/opspilot
kubectl --context opspilot -n opspilot rollout status deployment/opspilot --timeout=180s
```

## 4. Connect GitHub Actions to Minikube

In your repository, open **Settings → Actions → Runners → New self-hosted runner**. Select Linux and your architecture. Run the displayed download and configuration commands in WSL Ubuntu, in a runner directory outside the repository. Add the custom label **`opspilot-minikube`** during configuration. If GitHub's command does not include labels, add `--labels opspilot-minikube` to `./config.sh`.

Start it with the displayed `./run.sh` command. Keep that terminal, Docker Desktop, and Minikube running. A job can remain queued when the laptop or runner is offline. Shell aliases are not available to the runner, so install the standalone `kubectl` from step 2.

Then open **Settings → Secrets and variables → Actions → Variables → New repository variable**:

| Name | Value |
| --- | --- |
| `DEPLOY_TARGET` | `minikube` |

GitHub-hosted runners run the tests and scans. Your local runner only receives the deployment job after a trusted push to `main` passes every gate. Pull request jobs never run on your local runner. The workflow loads the tested image archive into Minikube, so your cluster does not need GHCR credentials.

After reviewing the files and configuring commit signing for the existing policy described below, commit and push the changes to activate the workflow:

```bash
git add .github/workflows .github/actionlint.yaml ai_service/Dockerfile ai_service/.dockerignore ai_service/requirements-dev.txt ai_service/tests k8s docs/cicd-setup.md
git commit -S -m "Add CI checks and Minikube deployment"
git push origin main
```

For a protected `main` branch, push your working branch and open a pull request instead; deployment happens after its verified commit reaches `main`.

## 5. What happens on each push

Every branch push runs Python tests, a Docker build, a container HTTP check, Trivy, and CodeQL. Pull requests targeting `main` run checks too. GitHub Actions builds the image once and shares that exact image between scanning, publishing, and deployment.

Only pushes to `main` can publish to GitHub Container Registry (GHCR) or deploy. A manual workflow run performs checks only. Publication requires:

- Successful tests on Python 3.10 and 3.14, a successful Docker startup check, and successful scans.
- No HIGH or CRITICAL Trivy vulnerabilities, including vulnerabilities without available fixes.
- No open HIGH or CRITICAL CodeQL findings for `main`.
- A GitHub-verified commit signature, preserving the repository's existing authorization policy.
- The commit is still the latest `main` commit.

If a local Git commit is unsigned, this policy blocks publication. GitHub's web editor normally creates verified commits; for local commits, configure signing using GitHub's signing instructions below.

GHCR publication uses the workflow's `GITHUB_TOKEN`; no personal access token is required. The image tag is the full commit SHA. The publish job records its registry digest in the job summary. Minikube imports the same image archive, applies the resources, waits for the rollout, and checks HTTP through the Kubernetes Service. A failed rollout attempts to restore the previous Deployment revision; the first deployment has no previous revision to restore. The job remains failed even when rollback succeeds.

**Your current Trivy findings will continue to block automated publication and deployment until resolved.** This patch improves the pipeline and reports; it does not remediate those vulnerabilities or weaken the gate.

This workflow controls the Minikube lab. Your existing Render service has its own deployment settings; this patch does not connect Render to the authorization gate.

## 6. Find reports and troubleshoot

- **Actions → CI Pipeline → latest run:** jobs show which stage failed; the authorization summary lists all prerequisite results.
- **Actions run → Artifacts:** download `api-tests-python-*` for JUnit results and `trivy-reports-*` for the full JSON and SARIF vulnerability reports. Reports are saved when the vulnerability gate fails.
- **Security → Code scanning:** inspect Trivy and CodeQL findings if your repository has code scanning enabled.
- **Repository → Packages:** find the approved image after a successful publication.

Useful local commands:

```bash
kubectl --context opspilot -n opspilot get pods -o wide
kubectl --context opspilot -n opspilot logs deployment/opspilot --tail=100
kubectl --context opspilot -n opspilot describe deployment opspilot
kubectl --context opspilot -n opspilot get events --sort-by=.metadata.creationTimestamp
```

For `ErrImageNeverPull`, confirm the image tag was loaded into profile `opspilot` and exactly matches the Deployment. The lab uses `imagePullPolicy: Never` because it imports the archive instead of pulling from the registry.

To pause work and release laptop resources:

```bash
minikube stop --profile opspilot
```

Resume with the `minikube start` command from step 2 and restart the runner. You do not need to register the runner again.

## Learning sequence

1. Deploy the app, view Pods, read logs, and access its Service.
2. Scale to two replicas, delete one Pod, and watch Kubernetes replace it.
3. Change an image version, watch a rollout, and practice rollback.
4. Study Secrets, ConfigMaps, probes, and resource limits.
5. Enable the automated pipeline after resolving the security findings.
6. Add Kubernetes admission policies with OPA Gatekeeper, then explore Helm, GitOps, and GKE.

The existing `gatekeeper.yml` is a GitHub Actions authorization check. It does not install OPA Gatekeeper in Kubernetes.

## Official references

- [Minikube installation and requirements](https://minikube.sigs.k8s.io/docs/start/)
- [Minikube Docker driver and WSL notes](https://minikube.sigs.k8s.io/docs/drivers/docker/)
- [Docker Desktop WSL integration](https://docs.docker.com/desktop/features/wsl/)
- [Install kubectl and verify its checksum](https://kubernetes.io/docs/tasks/tools/install-kubectl-linux/)
- [Register a self-hosted GitHub runner](https://docs.github.com/en/actions/how-tos/manage-runners/self-hosted-runners/add-runners)
- [GitHub commit signing](https://docs.github.com/en/authentication/managing-commit-signature-verification/signing-commits)

## Validation of this patch

The API tests passed on Python 3.10 and 3.14, and the Dockerfile's server command passed a host HTTP startup check on Python 3.10. The workflow files passed actionlint. The authorization script passed 14 mocked policy scenarios, and the actual Trivy CLI correctly returned exit 1 for HIGH/CRITICAL fixtures and exit 0 for a LOW fixture. All three Kubernetes manifests passed strict Kubernetes 1.35 schema validation.

Docker builds, GitHub Actions, GHCR publication, and Minikube rollout require your actual runner and cluster; they have not been executed in the assistant workspace.
