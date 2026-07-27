# ============================================================
# Imports
# ============================================================
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import Response, JSONResponse

import os
import json
import time
import logging
import urllib.parse

from dotenv import load_dotenv
load_dotenv()

# Groq
from groq import Groq

# Slack
from slack_sdk import WebClient
from slack_sdk.signature import SignatureVerifier

# Event handler (your event.py)
from app.slackbot.events import handle_event

# OpenTelemetry
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.requests import RequestsInstrumentor
from opentelemetry.trace import get_current_span

from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter

# OTEL Metrics
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import OTLPMetricExporter
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader

# Prometheus
from prometheus_client import (
    Counter,
    Histogram,
    Gauge,
    generate_latest,
    CONTENT_TYPE_LATEST,
    start_http_server
)

# ============================================================
# Logging
# ============================================================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger("ai-service")

# ============================================================
# FastAPI App
# ============================================================
app = FastAPI()

@app.get("/")
def home():
    return {"status": "AI Monitoring Agent Running"}

FastAPIInstrumentor().instrument_app(app)
RequestsInstrumentor().instrument()

# ============================================================
# Groq Client
# ============================================================
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
if not GROQ_API_KEY:
    raise RuntimeError("GROQ_API_KEY missing")

client = Groq(api_key=GROQ_API_KEY)

# ============================================================
# Slack Setup
# ============================================================
SLACK_BOT_TOKEN = os.getenv("SLACK_BOT_TOKEN")
SLACK_SIGNING_SECRET = os.getenv("SLACK_SIGNING_SECRET")

slack_client = WebClient(token=SLACK_BOT_TOKEN)
signature_verifier = SignatureVerifier(SLACK_SIGNING_SECRET)

# ============================================================
# Prometheus Metrics
# ============================================================
AI_REQUEST_COUNT = Counter("ai_requests_total", "Total number of AI requests")
AI_LATENCY = Histogram("ai_request_latency_seconds", "Latency of AI processing")
AI_ERRORS = Counter("ai_errors_total", "Total number of AI errors")
AI_IN_PROGRESS = Gauge("ai_requests_in_progress", "AI requests in progress")

if __name__ == "__main__":
    start_http_server(9100)

# ============================================================
# OTEL Tracing
# ============================================================
resource = Resource(attributes={"service.name": "ai-service"})
trace_provider = TracerProvider(resource=resource)

trace_exporter = OTLPSpanExporter(endpoint="http://otel-collector:4317", insecure=True)
trace_provider.add_span_processor(BatchSpanProcessor(trace_exporter))

# ============================================================
# OTEL Metrics
# ============================================================
metric_exporter = OTLPMetricExporter(endpoint="http://otel-collector:4317", insecure=True)
metric_reader = PeriodicExportingMetricReader(metric_exporter)

meter_provider = MeterProvider(resource=resource, metric_readers=[metric_reader])
meter = meter_provider.get_meter("ai-service")

otel_ai_latency = meter.create_histogram(
    name="otel_ai_inference_latency_seconds",
    description="AI inference latency",
    unit="s"
)

# ============================================================
# Metrics Endpoint
# ============================================================
@app.get("/metrics")
def metrics():
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)

# ============================================================
# Health Check
# ============================================================
@app.get("/health")
def health():
    return {"status": "ok"}

# ============================================================
# AI Inference Endpoint
# ============================================================
@app.post("/ask")
def ask(payload: dict):
    prompt = payload.get("prompt")
    if not prompt:
        raise HTTPException(status_code=400, detail="Missing 'prompt'")

    AI_REQUEST_COUNT.inc()
    AI_IN_PROGRESS.inc()
    start = time.time()

    try:
        span = get_current_span()
        ctx = span.get_span_context()

        trace_id = f"{ctx.trace_id:032x}"
        span_id = f"{ctx.span_id:016x}"

        logger.info(f"AI prompt trace_id={trace_id} span_id={span_id}")

        response = client.chat.completions.create(
            model="llama-3.1-8b-instant",
            messages=[{"role": "user", "content": prompt}]
        )

        answer = response.choices[0].message.content
        return {"response": answer}

    except Exception as e:
        AI_ERRORS.inc()
        logger.error(f"AI error: {e}")
        raise HTTPException(status_code=500, detail="AI processing failed")

    finally:
        duration = time.time() - start
        AI_LATENCY.observe(duration)
        otel_ai_latency.record(duration)
        AI_IN_PROGRESS.dec()

# ============================================================
# Slack Events Endpoint (FINAL WORKING VERSION)
# ============================================================
@app.post("/slack/events")
async def slack_events(request: Request):
    logger.info("===== SLACK REQUEST RECEIVED =====")

    raw_body = await request.body()
    body_str = raw_body.decode("utf-8")

    # Decode URL-encoded payload
    body_str = urllib.parse.unquote(body_str)

    # Slack sometimes sends: payload=<json>
    if body_str.startswith("payload="):
        body_str = body_str.replace("payload=", "")

    if body_str.startswith("command="):
    data = dict(urllib.parse.parse_qsl(body_str))
    else:
    data = json.loads(body_str)

    logger.info(f"Slack payload: {data}")

    # ------------------------------------------------------------
    # 1. Slack Challenge (must be first)
    # ------------------------------------------------------------
    if data.get("type") == "url_verification":
        return JSONResponse(content={"challenge": data["challenge"]})

    # ------------------------------------------------------------
    # 2. Slash Commands (Slack does NOT send signatures)
    # ------------------------------------------------------------
    if "command" in data:
        command = data["command"]
        text = data.get("text", "")
        channel = data.get("channel_id")

        reply_text = f"Slash command {command} received: {text}"
        slack_client.chat_postMessage(channel=channel, text=reply_text)

        return Response("OK", media_type="text/plain")

    # ------------------------------------------------------------
    # 3. Signature Verification (only for events)
    # ------------------------------------------------------------
    if not signature_verifier.is_valid_request(raw_body, request.headers):
        raise HTTPException(status_code=400, detail="Invalid Slack signature")

    # ------------------------------------------------------------
    # 4. Slack Events (app_mention, messages)
    # ------------------------------------------------------------
    slack_event = data.get("event", {})
    handle_event(slack_event)

    return Response("OK", media_type="text/plain")
