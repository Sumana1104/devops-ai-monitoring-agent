import os
import time
import requests
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
BASE_URL = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"

def telegram_send(chat_id, text):
    url = f"{BASE_URL}/sendMessage"
    data = {"chat_id": chat_id, "text": text}
    requests.post(url, json=data)

def process_ai_agent(user_text):
    # Call your FastAPI AI endpoint
    response = requests.post(
        "http://127.0.0.1:8000/ai",
        json={"message": user_text}
    )
    return response.json().get("reply", "No response")

def telegram_polling():
    offset = None

    while True:
        updates = requests.get(
            f"{BASE_URL}/getUpdates",
            params={"offset": offset}
        ).json()

        for update in updates.get("result", []):
            message = update.get("message", {})
            chat_id = message["chat"]["id"]
            text = message.get("text", "")

            reply = process_ai_agent(text)
            telegram_send(chat_id, reply)

            offset = update["update_id"] + 1

        time.sleep(1)

if __name__ == "__main__":
    telegram_polling()
