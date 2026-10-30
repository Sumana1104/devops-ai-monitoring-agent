"""Bounded real CPU work. No artificial sleeps, fault flags, or forced errors."""
import asyncio
import hashlib
import math
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from fastapi import FastAPI
from fastapi.responses import JSONResponse

app = FastAPI(title="OpsPilot traffic capacity experiment")
POOL = ThreadPoolExecutor(max_workers=1)
LOCK = threading.Lock()
BUCKETS = (.025, .05, .1, .2, .4, .8, 1.6, 3.2, 6.4, math.inf)
COUNTS = {"200": 0, "503": 0}
HISTOGRAM = [0] * len(BUCKETS)
TOTAL = 0.0
ADMITTED = 0
INSTANCE = os.getenv("HOSTNAME", "local")


def compute():
    # Fixed input/iterations, about 32 bytes output; no growing retained data.
    return hashlib.pbkdf2_hmac("sha256", b"opspilot-work", b"capacity-study", 200000).hex()


def observe(status, duration):
    global TOTAL
    with LOCK:
        COUNTS[status] += 1
        TOTAL += duration
        for index, bound in enumerate(BUCKETS):
            HISTOGRAM[index] += int(duration <= bound)


@app.get("/work")
async def work():
    global ADMITTED
    start = time.monotonic()
    with LOCK:
        if ADMITTED >= 24:
            overloaded = True
        else:
            ADMITTED += 1
            overloaded = False
    if overloaded:
        observe("503", time.monotonic() - start)
        return JSONResponse({"detail": "Processing queue at its bounded capacity"}, status_code=503)
    try:
        future = POOL.submit(compute)
        def release(_):
            global ADMITTED
            with LOCK:
                ADMITTED -= 1
        future.add_done_callback(release)
        value = await asyncio.wrap_future(future)
        observe("200", time.monotonic() - start)
        return {"result": value, "instance": INSTANCE}
    except BaseException:
        # Actual completion, not client disconnect, releases admission.
        raise


@app.get("/healthz")
async def health():
    return {"status": "healthy", "application": "opspilot-traffic", "instance": INSTANCE}


@app.get("/telemetry")
async def telemetry():
    with LOCK:
        return {"counts": dict(COUNTS), "buckets": list(HISTOGRAM),
                "duration_sum": TOTAL, "active": ADMITTED, "instance": INSTANCE}
