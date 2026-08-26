from fastapi import FastAPI, Request
from fastapi.responses import Response, JSONResponse, PlainTextResponse

import os
import requests
import json
import logging
import urllib.parse

from dotenv import load_dotenv
load_dotenv()

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
        "http://127.0.0.1:8000/ai",
        json={"message": user_text}
    )
    return response.json().get("reply", "No response")


# ============================================================
# Groq
# ============================================================

from groq import Groq

GROQ_API_KEY = os.getenv("GROQ_API_KEY")
if not GROQ_API_KEY:
    raise Exception("Missing GROQ_API_KEY")

groq_client = Groq(api_key=GROQ_API_KEY)


# ============================================================
# Logging
# ============================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s"
)

logger = logging.getLogger("ai-service")


# ============================================================
# FastAPI
# ============================================================

app = FastAPI()


@app.get("/")
def home():
    return {"status": "AI Monitoring Agent Running"}

@app.post("/ai")
async def ai_endpoint(request: Request):
    data = await request.json()
    user_text = data.get("message", "")

    # Call Groq
    try:
        response = groq_client.chat.completions.create(
            model="llama-3.1-8b-instant",
            messages=[{"role": "user", "content": user_text}]
        )
        answer = response.choices[0].message.content

    except Exception as e:
        answer = f"AI Error: {e}"

    return {"reply": answer}



# ============================================================
# Slack Events Endpoint
# ============================================================

@app.post("/slack/events")
async def slack_events(request: Request):

    logger.info("===== SLACK REQUEST RECEIVED =====")

    raw_body = await request.body()
    body = raw_body.decode("utf-8")

    logger.info(f"RAW BODY: {body}")

    # Slash command accidentally sent here
    if body.startswith("token=") or "command=" in body:

        data = dict(urllib.parse.parse_qsl(body))
        text = data.get("text", "")

        logger.info(f"COMMAND TEXT: {text}")

        try:
            response = groq_client.chat.completions.create(
                model="llama-3.1-8b-instant",
                messages=[{"role": "user", "content": text}]
            )
            answer = response.choices[0].message.content

        except Exception as e:
            answer = f"AI Error: {e}"

        return JSONResponse({
            "response_type": "in_channel",
            "text": f"🚀 *Rocket AI Agent*\n\n*You said:* {text}\n\n*AI says:* {answer}"
        })

    # Slack Events API JSON
    try:
        data = json.loads(body)
    except Exception:
        logger.error("Invalid JSON")
        return Response("Bad Request", status_code=400)

    logger.info(f"EVENT DATA: {data}")

    # Slack URL verification
    if data.get("type") == "url_verification":
        return PlainTextResponse(data["challenge"])

    # Signature validation
    if not signature_verifier.is_valid_request(raw_body, request.headers):
        logger.warning("Invalid Slack signature")
        return Response("OK", status_code=200)

    # Event processing
    event = data.get("event", {})

    if event.get("bot_id"):
        logger.info("Ignoring bot message (bot_id detected)")
        return Response("OK", status_code=200)

    if event.get("subtype") == "bot_message":
        logger.info("Ignoring bot message (subtype bot_message)")
        return Response("OK", status_code=200)

    try:
        handle_event(event)
    except Exception as e:
        logger.exception(f"Handler error: {e}")

    return Response("OK", status_code=200)


# ============================================================
# Slack Slash Command Endpoint
# ============================================================

@app.post("/slack/command")
async def slack_command(request: Request):

    form = await request.form()
    text = form.get("text", "")

    logger.info(f"🔥 SLASH COMMAND RECEIVED: {text}")

    try:
        response = groq_client.chat.completions.create(
            model="llama-3.1-8b-instant",
            messages=[{"role": "user", "content": text}]
        )
        answer = response.choices[0].message.content

    except Exception as e:
        answer = f"AI Error: {e}"

    return JSONResponse({
        "response_type": "in_channel",
        "text": (
            f"🤵 *Gentleman Groq*\n\n"
            f"*You said:* {text}\n\n"
            f"*AI replies:* {answer}"
        )
    })
@app.get("/telegram/webhook")
async def telegram_webhook_get():
    return {"ok": True}



@app.post("/telegram/webhook")
async def telegram_webhook(request: Request):
    data = await request.json()
    message = data.get("message", {})
    chat_id = message.get("chat", {}).get("id")
    text = message.get("text", "")

    if chat_id and text:
        reply = process_ai_agent(text)
        telegram_send(chat_id, reply)

    return {"ok": True}

