"""Deliberately safe, instrumented demo used to show traffic, latency and 5xx."""
import asyncio
import time

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import Response
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest

app = FastAPI(title="OpsPilot demo target")
REQUESTS = Counter(
    "opspilot_http_requests_total", "HTTP responses by path and status",
    ["method", "path", "status"],
)
DURATION = Histogram(
    "opspilot_http_request_duration_seconds", "Request duration seconds",
    ["method", "path"],
    buckets=[.005, .01, .025, .05, .1, .2, .4, .75, 1, 2, 5],
)

@app.middleware("http")
async def record(request: Request, call_next):
    start = time.monotonic()
    status = 500
    try:
        response = await call_next(request)
        status = response.status_code
        return response
    finally:
        route = request.scope.get("route")
        path = getattr(route, "path", "unknown")
        if path != "/metrics":
            REQUESTS.labels(request.method, path, str(status)).inc()
            DURATION.labels(request.method, path).observe(time.monotonic() - start)

@app.get("/")
def home():
    return {"status": "healthy", "application": "opspilot-demo"}

@app.get("/healthz")
def healthz():
    return {"ok": True}

@app.get("/slow")
async def slow():
    await asyncio.sleep(0.55)
    return {"status": "slow-demo", "delay_seconds": 0.55}

@app.get("/error")
def error():
    raise HTTPException(status_code=500, detail="Intentional demo HTTP 500")

@app.get("/metrics")
def metrics():
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)
