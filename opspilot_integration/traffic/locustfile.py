"""Real HTTP workload inside Kubernetes; no /slow, /error or fault toggles."""
import json
import time
import gevent
from locust import HttpUser, LoadTestShape, constant, task, events


@events.test_start.add_listener
def report_state(environment, **kwargs):
    started = time.monotonic()
    def publish():
        previous = (time.monotonic(), 0, 0)
        while environment.runner and environment.runner.state not in ("stopped", "stopping", "cleanup"):
            elapsed = time.monotonic() - started
            total = environment.stats.total
            at = time.monotonic()
            count, failures = total.num_requests, total.num_failures
            delta = count - previous[1]
            print("OPSPILOT_LOAD_STATE " + json.dumps({
                "timestamp": time.time(), "users": environment.runner.user_count,
                "elapsed": elapsed, "burst": 110 <= elapsed < 1100,
                "state": environment.runner.state,
                "client_rps": delta / max(at - previous[0], .001),
                "client_error_ratio": (failures - previous[2]) / delta if delta > 0 else None,
                "client_p95_ms": total.get_current_response_time_percentile(.95)}), flush=True)
            previous = (at, count, failures)
            gevent.sleep(5)
    gevent.spawn(publish)


class Traffic(HttpUser):
    wait_time = constant(.05)

    @task
    def processing(self):
        # New TCP connection per request lets Service routing select backends.
        # This avoids measuring just one backend behind one pinned forward.
        with self.client.get("/work", headers={"Connection": "close"},
                             timeout=10, catch_response=True) as response:
            if response.status_code == 200:
                try:
                    data = response.json()
                    if not isinstance(data.get("result"), str) or not data.get("instance"):
                        response.failure("Invalid processing response")
                except ValueError:
                    response.failure("Invalid JSON")


class BaselineThenBurst(LoadTestShape):
    def tick(self):
        elapsed = self.get_run_time()
        if elapsed < 90:
            return (1, 1)
        if elapsed < 1200:
            return (8, 2)
        return None
