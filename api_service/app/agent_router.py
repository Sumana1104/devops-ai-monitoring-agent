import os
import requests
import logging
from fastapi import APIRouter, Request

router = APIRouter()

# ============================================================
# Logging
# ============================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s"
)

logger = logging.getLogger("api-service")

# ============================================================
# Telegram
# ============================================================

TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
BASE_URL = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"

def telegram_send(chat_id, text):
    url = f"{BASE_URL}/sendMessage"
    data = {"chat_id": chat_id, "text": text}
    requests.post(url, json=data)

def process_ai_agent(user_text):
    response = requests.post(
        "http://127.0.0.1:8000/ai",   # CHANGE THIS TO RENDER URL LATER
        json={"message": user_text}
    )
    return response.json().get("reply", "No response")

@router.get("/telegram/webhook")
async def telegram_webhook_get():
    return {"ok": True}

@router.post("/telegram/webhook")
async def telegram_webhook(request: Request):
    data = await request.json()
    message = data.get("message", {})
    chat_id = message.get("chat", {}).get("id")
    text = message.get("text", "")

    if chat_id and text:
        reply = process_ai_agent(text)
        telegram_send(chat_id, reply)

    return {"ok": True}
