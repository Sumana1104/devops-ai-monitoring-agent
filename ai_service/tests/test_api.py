"""Check startup and the AI request path without credentials or paid API calls."""

from unittest.mock import Mock
from fastapi.testclient import TestClient
from app import main


def test_service_starts_and_health_endpoint_responds():
    # Importing main also catches undeclared runtime dependencies.
    with TestClient(main.app) as client:
        response = client.get("/")
    assert response.status_code == 200
    assert response.json()["status"] == "AI Monitoring Agent Running"


def test_ai_forwards_message_and_returns_provider_reply(monkeypatch):
    upstream = Mock()
    upstream.json.return_value = {
        "choices": [{"message": {"content": "Check the pod's previous logs."}}]
    }
    post = Mock(return_value=upstream)
    monkeypatch.setattr(main.requests, "post", post)
    monkeypatch.setenv("GROQ_API_KEY", "unit-test-placeholder")
    with TestClient(main.app) as client:
        response = client.post("/ai", json={"message": "Why did my pod restart?"})
    assert response.status_code == 200
    assert response.json() == {"reply": "Check the pod's previous logs."}
    post.assert_called_once()
    assert post.call_args.kwargs["json"]["messages"][-1] == {
        "role": "user", "content": "Why did my pod restart?"
    }
