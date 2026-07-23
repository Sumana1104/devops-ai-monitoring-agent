from app.agent_router import Router
from app.llm_node import DevOpsAgent
from app.slackbot.bot import send_message

from app.k8s_tools import (
    k_get_pods,
    k_describe_pod,
    k_logs,
    k_logs_follow,
    k_delete_pod,
    k_restart_deployment
)

router = Router()

def handle_event(event: dict):
    text = event.get("text")
    channel = event.get("channel")

    if not text:
        return

    # Kubernetes Commands
    if text.startswith("k get pods"):
        send_message(channel, k_get_pods())
        return

    if text.startswith("k describe pod"):
        _, _, _, pod = text.split()
        send_message(channel, k_describe_pod(pod))
        return

    if text.startswith("k logs -f"):
        _, _, _, pod = text.split()
        send_message(channel, k_logs_follow(pod))
        return

    if text.startswith("k logs"):
        _, _, pod = text.split()
        send_message(channel, k_logs(pod))
        return

    if text.startswith("k delete pod"):
        _, _, _, pod = text.split()
        send_message(channel, k_delete_pod(pod))
        return

    if text.startswith("k restart deploy"):
        _, _, _, deploy = text.split()
        send_message(channel, k_restart_deployment(deploy))
        return

    # Router (Natural Language → Tools)
    routed = router.route(text)
    if routed:
        send_message(channel, routed)
        return

    # LLM fallback
    agent = DevOpsAgent()
    answer = agent.ask(text)
    send_message(channel, answer)
