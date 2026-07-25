# ============================================================
# Imports: FastAPI, Groq client, logging, time, OS
# ============================================================
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import Response

from groq import Groq
import logging
import time
import os
import json


from dotenv import load_dotenv
load_dotenv()

# ============================================================
# Slack Imports
# ============================================================
from slack_sdk import WebClient
from slack_sdk.signature import SignatureVerifier

# ============================================================
# OpenTelemetry Instrumentation
# ============================================================
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.requests import RequestsInstrumentor
from opentelemetry.trace import get_current_span

from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter

# ============================================================
# OTEL Metrics
# ============================================================
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import OTLPMetricExporter
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader

# ============================================================
# Prometheus Metrics
# ============================================================
from prometheus_client import (
    Counter,
    Histogram,
    Gauge,
    generate_latest,
    CONTENT_TYPE_LATEST,
    start_http_server
)

# ============================================================
# Logging Setup
# ============================================================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger("ai-service")

# ============================================================
# FastAPI App Initialization
# ============================================================
app = FastAPI()

@app.get("/")
def home():
    return {"status": "AI Monitoring Agent Running"}


FastAPIInstrumentor().instrument_app(app)
RequestsInstrumentor().instrument()

# ============================================================
# Groq Client Setup
# ============================================================
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
if not GROQ_API_KEY:
    logger.error("GROQ_API_KEY is missing! App cannot start.")
    raise RuntimeError("GROQ_API_KEY is missing")

client = Groq(api_key=GROQ_API_KEY)

# ============================================================
# Slack Client Setup
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
AI_IN_PROGRESS = Gauge("ai_requests_in_progress", "Number of AI requests currently being processed")

# Prevent double-start under uvicorn reload
if __name__ == "__main__":
    start_http_server(9100)

# ============================================================
# OTEL Tracing Setup
# ============================================================
resource = Resource(attributes={"service.name": "ai-service"})
trace_provider = TracerProvider(resource=resource)

trace_exporter = OTLPSpanExporter(endpoint="http://otel-collector:4317", insecure=True)
trace_provider.add_span_processor(BatchSpanProcessor(trace_exporter))

# ============================================================
# OTEL Metrics Setup
# ============================================================
metric_exporter = OTLPMetricExporter(endpoint="http://otel-collector:4317", insecure=True)
metric_reader = PeriodicExportingMetricReader(metric_exporter)

meter_provider = MeterProvider(resource=resource, metric_readers=[metric_reader])
meter = meter_provider.get_meter("ai-service")

otel_ai_latency = meter.create_histogram(
    name="otel_ai_inference_latency_seconds",
    description="AI inference latency via OTEL",
    unit="s"
)

# ============================================================
# Prometheus Metrics Endpoint
# ============================================================
@app.get("/metrics")
def metrics():
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)

# ============================================================
# Health Check Endpoint
# ============================================================
@app.get("/health")
def health():
    return {"status": "ok"}

# ============================================================
# Main AI Inference Endpoint (/ask)
# ============================================================
@app.post("/ask")
def ask(payload: dict):

    prompt = payload.get("prompt")
    if not prompt:
        raise HTTPException(status_code=400, detail="Missing 'prompt' field")

    AI_REQUEST_COUNT.inc()
    AI_IN_PROGRESS.inc()
    start = time.time()

    try:
        span = get_current_span()
        ctx = span.get_span_context()

        trace_id = f"{ctx.trace_id:032x}"
        span_id = f"{ctx.span_id:016x}"

        logger.info(f"AI received prompt trace_id={trace_id} span_id={span_id}")

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


from fastapi import Form

@app.post("/slack/events")
async def slack_events(request: Request):
    raw_body = await request.body()
    body_str = raw_body.decode("utf-8")

    # Slack sends: payload=<json>
    if body_str.startswith("payload="):
        body_str = body_str.replace("payload=", "")

    # Now parse JSON
    data = json.loads(body_str)

    # 1. Handle Slack challenge BEFORE signature verification
    if data.get("type") == "url_verification":
        return JSONResponse({"challenge": data["challenge"]})

    # 2. Signature verification AFTER challenge
    if not signature_verifier.is_valid_request(raw_body, request.headers):
        raise HTTPException(status_code=400, detail="Invalid Slack signature")

    # 3. Normal event handling
    slack_event = data.get("event", {})
    event_type = slack_event.get("type")

    if event_type == "app_mention":
        user = slack_event.get("user")
        text = slack_event.get("text", "")
        channel = slack_event.get("channel")

        reply_text = f"Hello <@{user}>! You said: {text}"
        slack_client.chat_postMessage(channel=channel, text=reply_text)

    return Response("OK", media_type="text/plain")
