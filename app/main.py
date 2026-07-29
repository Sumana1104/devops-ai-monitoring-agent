# ============================================================
# Imports
# ============================================================

from fastapi import FastAPI, Request
from fastapi.responses import Response, JSONResponse

import os
import json
import logging
import urllib.parse

from dotenv import load_dotenv
load_dotenv()

from groq import Groq

from slack_sdk import WebClient
from slack_sdk.signature import SignatureVerifier

from app.slackbot.events import handle_event


# ============================================================
# Logging
# ============================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)

logger = logging.getLogger("ai-service")


# ============================================================
# FastAPI
# ============================================================

app = FastAPI()


@app.get("/")
def home():
    return {
        "status": "AI Monitoring Agent Running"
    }


# ============================================================
# Groq
# ============================================================

GROQ_API_KEY = os.getenv("GROQ_API_KEY")

if not GROQ_API_KEY:
    raise RuntimeError("Missing GROQ_API_KEY")


groq_client = Groq(
    api_key=GROQ_API_KEY
)


# ============================================================
# Slack
# ============================================================

SLACK_BOT_TOKEN = os.getenv("SLACK_BOT_TOKEN")
SLACK_SIGNING_SECRET = os.getenv("SLACK_SIGNING_SECRET")


slack_client = WebClient(
    token=SLACK_BOT_TOKEN
)


signature_verifier = SignatureVerifier(
    SLACK_SIGNING_SECRET
)


# ============================================================
# Slack Events
# ============================================================

@app.post("/slack/events")
async def slack_events(request: Request):

    logger.info(
        "===== SLACK REQUEST RECEIVED ====="
    )


    raw_body = await request.body()

    body_str = raw_body.decode(
        "utf-8"
    )


    logger.info(
        f"RAW BODY: {body_str}"
    )


    # --------------------------------------------------------
    # Slash command support
    # --------------------------------------------------------

    if "command=" in body_str:

        data = dict(
            urllib.parse.parse_qsl(body_str)
        )

        text = data.get(
            "text",
            ""
        )


        try:

            response = groq_client.chat.completions.create(
                model="llama-3.1-8b-instant",
                messages=[
                    {
                        "role": "user",
                        "content": text
                    }
                ]
            )


            answer = (
                response
                .choices[0]
                .message
                .content
            )


        except Exception as e:

            answer = (
                f"AI Error: {e}"
            )


        return JSONResponse(
            {
                "response_type": "in_channel",
                "text":
                f"🚀 *Rocket AI Agent*\n\n"
                f"*You said:* {text}\n\n"
                f"*AI says:* {answer}"
            }
        )


    # --------------------------------------------------------
    # JSON Events API
    # --------------------------------------------------------

    try:

        data = json.loads(
            body_str
        )

    except Exception:

        logger.error(
            "Invalid JSON"
        )

        return Response(
            "Invalid JSON",
            status_code=400
        )


    logger.info(
        f"EVENT DATA: {data}"
    )


    # Slack verification

    if data.get("type") == "url_verification":

        return JSONResponse(
            {
                "challenge":
                data["challenge"]
            }
        )


    # --------------------------------------------------------
    # Signature verification
    # --------------------------------------------------------

    if not signature_verifier.is_valid_request(
        raw_body,
        request.headers
    ):

        logger.warning(
            "Invalid Slack signature"
        )

        return Response(
            "OK",
            status_code=200
        )


    # --------------------------------------------------------
    # Extract event
    # --------------------------------------------------------

    slack_event = data.get(
        "event",
        {}
    )


    # Ignore bot messages

    if slack_event.get(
        "subtype"
    ) == "bot_message":

        logger.info(
            "Ignoring bot message"
        )

        return Response(
            "OK",
            status_code=200
        )


    # --------------------------------------------------------
    # Send event to handler
    # --------------------------------------------------------

    try:

        handle_event(
            slack_event
        )

    except Exception as e:

        logger.exception(
            f"Handler failed: {e}"
        )


    return Response(
        "OK",
        status_code=200
    )