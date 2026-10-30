"""Deliberately safe, instrumented demo used to show traffic, latency and 5xx."""
import asyncio
import os
import time
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import Response
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Gauge, Histogram, generate_latest

def allocate_demo_memory(value, allocate=bytearray):
    """Fixed operator-only main-process load; never an unbounded allocator."""
    if value in (None, ""):
        return None
    if value != "320":
        raise ValueError("Only the explicit 320 MiB controlled OOM demo is supported")
    print("OPERATOR-INJECTED OOM DEMO: fixed 320 MiB memory demand; not a spontaneous production memory leak", flush=True)
    block = allocate(320 * 1024 * 1024)
    for offset in range(0, 320 * 1024 * 1024, 4096):
        block[offset] = 1
    return block


# Held by Uvicorn's main process; the explicit test env persists through rollout.
DEMO_MEMORY = allocate_demo_memory(os.getenv("OPSPILOT_DEMO_MEMORY_MIB"))
app = FastAPI(title="OpsPilot demo target")
READINESS_FLAG = Path("/runtime/readiness.flag")
READY = Gauge("opspilot_demo_ready", "Demo readiness: 0 while the controlled readiness flag exists")
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
        if path not in {"/metrics", "/healthz", "/readyz"}:
            REQUESTS.labels(request.method, path, str(status)).inc()
            DURATION.labels(request.method, path).observe(time.monotonic() - start)

@app.get("/")
def home():
    return {"status": "healthy", "application": "opspilot-demo"}

@app.get("/healthz")
def healthz():
    return {"ok": True}

@app.get("/readyz")
def readyz():
    if READINESS_FLAG.exists():
        raise HTTPException(status_code=503, detail="Intentional demo readiness flag")
    return {"ready": True}

@app.get("/slow")
async def slow():
    await asyncio.sleep(0.55)
    return {"status": "slow-demo", "delay_seconds": 0.55}

@app.get("/error")
def error():
    raise HTTPException(status_code=500, detail="Intentional demo HTTP 500")

@app.get("/metrics")
def metrics():
    READY.set(0 if READINESS_FLAG.exists() else 1)
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)
