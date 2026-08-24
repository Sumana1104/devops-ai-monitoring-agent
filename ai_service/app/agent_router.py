from app.k8s_tools import (
    k_get_pods,
    k_describe_pod,
    k_logs,
    k_restart_deployment
)

from app.cicd_tools import (
    gh_latest_run,
    gh_latest_logs,
    gh_trigger_workflow
)

class Router:
    """
    Layer 2: Decision-making logic.
    This decides WHICH Kubernetes or CI/CD tool to call based on user text.
    """

    def route(self, text: str):
        t = text.lower().strip()
        words = t.split()

        # -----------------------------
        # CrashLoopBackOff detection
        # -----------------------------
        if "crashloop" in t or "crash loop" in t:
            pod = words[-1]
            return k_logs(pod)

        # -----------------------------
        # Pod stuck in Pending
        # -----------------------------
        if "pending" in t:
            pod = words[-1]
            return k_describe_pod(pod)

        # -----------------------------
        # Pod down / not running
        # -----------------------------
        if "pod down" in t or "not running" in t:
            pod = words[-1]
            return k_describe_pod(pod)

        # -----------------------------
        # Restart deployment
        # -----------------------------
        if "restart" in t and "deploy" in t:
            deploy = words[-1]
            return k_restart_deployment(deploy)

        # -----------------------------
        # List pods
        # -----------------------------
        if "show pods" in t or "list pods" in t:
            return k_get_pods()

        # =====================================================
        # CI/CD ROUTING (GitHub Actions)
        # =====================================================

        # -----------------------------
        # CI/CD: latest build status
        # -----------------------------
        if "build status" in t or "latest build" in t:
            return gh_latest_run()

        # -----------------------------
        # CI/CD: latest build logs
        # -----------------------------
        if "build logs" in t or "latest logs" in t:
            return gh_latest_logs()

        # -----------------------------
        # CI/CD: trigger deploy
        # -----------------------------
        if "redeploy" in t or "trigger deploy" in t:
            return gh_trigger_workflow()

        # -----------------------------
        # No match → return None
        # LLM will handle it
        # -----------------------------
        return None
