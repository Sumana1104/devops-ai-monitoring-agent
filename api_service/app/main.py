from fastapi import FastAPI, Request
import requests
import os

app = FastAPI()

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
AI_SERVICE_URL = os.getenv("AI_SERVICE_URL")

@app.post("/telegram/webhook")
async def telegram_webhook(request: Request):
    data = await request.json()

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
