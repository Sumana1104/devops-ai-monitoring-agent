
import os
import requests


def send_approval_request(incident_id, action, summary):
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")

    if not token or not chat_id:
        raise RuntimeError(
            "Telegram bot token or chat ID not configured"
        )

    text = (
        "🚨 OpsPilot Incident\n\n"
        f"Incident ID: {incident_id}\n"
        f"Finding: {summary[:1000]}\n"
        f"Proposed action: {action}\n\n"
        "Approval required before remediation."
    )

    keyboard = {
        "inline_keyboard": [
            [
                {
                    "text": "✅ Approve",
                    "callback_data": f"approve:{incident_id}",
                },
                {
                    "text": "❌ Reject",
                    "callback_data": f"reject:{incident_id}",
                },
            ]
        ]
    }

    response = requests.post(
        f"https://api.telegram.org/bot{token}/sendMessage",
        json={
            "chat_id": chat_id,
            "text": text,
            "reply_markup": keyboard,
        },
        timeout=15,
    )

    response.raise_for_status()
    data = response.json()

    if not data.get("ok"):
        raise RuntimeError("Telegram rejected the message")

    return data["result"]["message_id"]


def send_manual_review_notification(incident):
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")

    if not token or not chat_id:
        raise RuntimeError("Telegram configuration missing")

    text = (
        "🚨 OpsPilot Incident — Manual Review\n\n"
        f"Root cause: {incident['root_cause']}\n"
        f"Severity: {incident['severity']}\n"
        f"Confidence: {incident['confidence']}\n\n"
        "AI recommendation requires manual investigation.\n"
        "No Kubernetes remediation will execute."
    )

    response = requests.post(
        f"https://api.telegram.org/bot{token}/sendMessage",
        json={"chat_id": chat_id, "text": text},
        timeout=15,
    )
    response.raise_for_status()

    result = response.json()
    if not result.get("ok"):
        raise RuntimeError("Telegram rejected notification")

    return result["result"]["message_id"]
