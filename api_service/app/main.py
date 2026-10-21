from fastapi import FastAPI, Request
import hmac
from fastapi import HTTPException	
import requests
import os
from api_service.app.approval_store import decide_approval

app = FastAPI()

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_WEBHOOK_SECRET = os.getenv("TELEGRAM_WEBHOOK_SECRET")
AI_SERVICE_URL = os.getenv("AI_SERVICE_URL")

@app.post("/telegram/webhook")
async def telegram_webhook(request: Request):
    received_secret = request.headers.get(
        "X-Telegram-Bot-Api-Secret-Token", ""
    )

    if (
        not TELEGRAM_WEBHOOK_SECRET
        or not hmac.compare_digest(received_secret, TELEGRAM_WEBHOOK_SECRET)
    ):
        raise HTTPException(
            status_code=403,
            detail="Unauthorized webhook"
        )
    data = await request.json()



    # Handle Telegram Approve/Reject button clicks
    callback = data.get("callback_query")

    if callback:
        user_id = callback.get("from", {}).get("id")
        chat_id = callback.get("message", {}).get("chat", {}).get("id")
        action = callback.get("data", "")

        expected_user = os.getenv("TELEGRAM_APPROVER_USER_ID")
        expected_chat = os.getenv("TELEGRAM_CHAT_ID")

        if (
            not expected_user
            or not expected_chat
            or str(user_id) != expected_user
            or str(chat_id) != expected_chat
        ):
            return {"status": "unauthorized_approver"}

                # Callback example: approve:5bbe9156e2a00aee
        try:
            decision, incident_id = action.split(":", 1)
        except ValueError:
            return {"status": "invalid_callback"}

        if decision not in {"approve", "reject"}:
            return {"status": "invalid_callback"}

        accepted = decide_approval(incident_id, decision)

        if not accepted:
            return {"status": "invalid_or_expired_approval"}

        print("Incident ID:", incident_id)
        print("Decision:", decision)

        return {"status": "decision_recorded"}





    # Telegram sends message inside "message"
    message = data.get("message", {})
    text = message.get("text", "")

    # If no text, ignore
    if not text:
        return {"status": "ignored"}

    # Call AI-service
    try:
        ai_response = requests.post(
            AI_SERVICE_URL,
            json={"message": text},
            headers={"Content-Type": "application/json"}
        ).json()

        reply_text = ai_response.get("reply", "AI Error")

        # Send reply back to Telegram
        chat_id = message["chat"]["id"]

        requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
            json={"chat_id": chat_id, "text": reply_text}
        )

        return {"status": "ok"}

    except Exception as e:
        return {"status": f"error: {str(e)}"}
