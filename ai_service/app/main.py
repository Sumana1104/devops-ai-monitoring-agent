import logging
import os
import sys

import requests
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from opentelemetry.sdk._logs import LoggerProvider, LoggingHandler
from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
from opentelemetry.exporter.otlp.proto.http._log_exporter import OTLPLogExporter
from opentelemetry.sdk.resources import Resource


# Configure application logging.
service_name = os.getenv("OTEL_SERVICE_NAME", "opspilot")

collector_endpoint = os.getenv(
    "OTEL_EXPORTER_OTLP_ENDPOINT",
    "http://host.minikube.internal:4318"
).rstrip("/")

resource = Resource.create({
    "service.name": service_name
})

logger_provider = LoggerProvider(resource=resource)

log_exporter = OTLPLogExporter(
    endpoint=f"{collector_endpoint}/v1/logs"
)

logger_provider.add_log_record_processor(
    BatchLogRecordProcessor(log_exporter)
)

logger = logging.getLogger("opspilot.ai")
logger.setLevel(logging.INFO)
logger.propagate = False

# Display application logs in Kubernetes.
console_handler = logging.StreamHandler(sys.stdout)
console_handler.setLevel(logging.INFO)
logger.addHandler(console_handler)

# Send the same application logs to OpenTelemetry.
otel_handler = LoggingHandler(
    level=logging.INFO,
    logger_provider=logger_provider
)
logger.addHandler(otel_handler)


app = FastAPI()

logger.info("OpsPilot AI Service logging initialized")


class AIRequest(BaseModel):
    message: str = Field(min_length=1, max_length=4000)


@app.get("/")
def root():
    return {"status": "AI Monitoring Agent Running"}


@app.post("/ai")
def ai_endpoint(data: AIRequest):
    logger.info("OpsPilot AI request received")

    user_text = data.message.strip()

    if not user_text:
        logger.warning("AI request contained a blank message")
        raise HTTPException(
            status_code=422,
            detail="Message cannot be blank."
        )

    api_key = os.getenv("GROQ_API_KEY")

    if not api_key:
        logger.error("Groq API key is not configured")
        raise HTTPException(
            status_code=503,
            detail="AI is not configured: GROQ_API_KEY is missing."
        )

    try:
        response = requests.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json"
            },
            json={
                "model": os.getenv("GROQ_MODEL", "openai/gpt-oss-20b"),
                "max_completion_tokens": 1024,
                "messages": [
                    {"role": "user", "content": user_text}
                ]
            },
            timeout=(5, 30)
        )

        response.raise_for_status()

    except requests.Timeout:
        logger.error("Groq API request timed out")
        raise HTTPException(
            status_code=504,
            detail="AI provider timed out."
        ) from None

    except requests.RequestException:
        logger.error("Groq API request failed")
        raise HTTPException(
            status_code=502,
            detail="AI provider request failed. Check credentials and provider availability."
        ) from None

    try:
        result = response.json()
        reply = result["choices"][0]["message"]["content"]

        if not isinstance(reply, str) or not reply.strip():
            raise ValueError("Missing reply")

    except (ValueError, KeyError, IndexError, TypeError):
        logger.error("Groq returned an invalid response")
        raise HTTPException(
            status_code=502,
            detail="AI provider returned an invalid response."
        ) from None

    logger.info("OpsPilot AI request completed successfully")

    return {"reply": reply}
