from fastapi.testclient import TestClient
from opspilot_integration.demo.app import app


def test_instrumented_app_exposes_5xx_and_latency():
    with TestClient(app) as client:
        assert client.get('/healthz').status_code == 200
        assert client.get('/error').status_code == 500
        assert client.get('/slow').status_code == 200
        metrics=client.get('/metrics')
        assert metrics.status_code == 200
        assert 'opspilot_http_requests_total' in metrics.text
        assert 'status="500"' in metrics.text
        assert 'opspilot_http_request_duration_seconds_bucket' in metrics.text
