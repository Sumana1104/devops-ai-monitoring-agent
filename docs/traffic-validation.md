# Traffic extension validation handoff

This records checks in the development workspace, not a live result from Sumi's
laptop. No Kubernetes workload, Terraform resource, paid Groq request, Telegram
message or GitHub branch was changed by these checks.

## Completed here

* Root unittest regression suite: 328 tests, OK with one optional fixture skipped.
  Existing crash/OOM/readiness tests are retained. The count includes inherited
  store cases exercised again through the transport-integration test class;
  it is not a count of 328 distinct live incidents.
* Separate FastAPI API tests: 2 passed, with test-time telemetry exports off.
* Installer tests in disposable Git checkouts: 8 passed (read-only preview,
  exact installation, idempotence, unrelated/private source preservation,
  unknown edit rejection, staged-work preservation, branch/origin guards and
  symlink refusal). It does not commit or push the user's repository.
* Native LangGraph execution, schema validation and FastAPI/SQLite callback
  tests use real local libraries, with external provider/Kubernetes/Telegram
  boundaries mocked. Negative cases cannot reach a mutation.
* Kubernetes fixture tests validate owner and Service binding, HPA conflict,
  stale data, unexpected templates, unready/restarted Pods and replica ceiling.
* Recovery tests require both backends receiving work and unchanged active
  Locust identity/configuration, improved latency/error/rate and a stable window.
* All eight PromQL expressions (seven retained rules plus traffic overload)
  parsed locally; YAML and the eleven-panel Grafana JSON parsed successfully.
* Python syntax and tracked whitespace checks passed. Credential-pattern scan
  of the 62 reviewed upload paths found no matching common token/key patterns.
  This is best effort, not a security audit or guarantee of no secrets.

## Real local HTTP smoke test, with a limitation

Locust 2.32.1 was installed into an isolated test dependency directory. The actual
new Uvicorn app and the actual unchanged load script ran as separate subprocesses
on a temporary localhost port. Over about 18 seconds, the baseline produced
207 successful `/work` requests, no queue-full rejections and four live client
state reports; the last client p95 report was 40ms.

Locust's own process-CPU monitor also reported `psutil.NoSuchProcess` because its
PID lookup was unavailable in this sandbox. Requests and client telemetry
continued, but this is **not a clean complete Locust run**, a Kubernetes routing
test, an eight-user overload measurement or proof of two-replica recovery.
No runtime safety code was disabled to suppress that warning. WSL/Minikube must
be checked independently; real Locust Job errors must not be ignored there.

## Not established here

Docker/Minikube are unavailable in this workspace. The new image build, Locust
container-image pull, real Service distribution, metrics-server observations,
Prometheus/Alertmanager runtime, public callback connectivity, real Groq output
and measured one-to-two-replica improvement all need the user's live run.
PromQL parsing is not the same as a promtool time-series unit test or a firing
Prometheus rule. Dashboard JSON validity is not a screenshot of running Grafana.

Tests used Python 3.12 here. The user's Python 3.14 and CI Python 3.10/3.14 runs
remain separate checks. Ordinary FastAPI lifespan deprecation warnings remain;
the extension did not refactor the verified bridge lifecycle merely to hide them.
The optional native RAG fixture's skip is not a successful live MiniLM test.

## Safe next step

Install source and rerun offline tests first. Do not generate load or enable
scaling during installation. Then follow `docs/traffic-capacity.md` to prepare
the isolated target, enable just its gate, run real clients and examine actual
before/after results. If the laptop does not cross the thresholds, report that;
do not change parameters between before/after measurements to manufacture proof.
