import os
import sys
from pathlib import Path
from unittest.mock import Mock

os.environ["OPSPILOT_DISABLE_WATCHER"] = "1"
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from fastapi.testclient import TestClient
from opspilot_integration import bridge


def test_health():
    with TestClient(bridge.app) as client:
        r = client.get('/health')
        assert r.status_code == 200
        assert r.json()['local_bridge'] is True


def test_alert_requires_authorization(monkeypatch):
    monkeypatch.setattr(bridge, 'alert_token', lambda: 'private-key')
    with TestClient(bridge.app) as client:
        r = client.post('/alerts', json={'alerts': []})
        assert r.status_code == 403
        r = client.post('/alerts', headers={'Authorization': 'Bearer private-key'}, json={'alerts': []})
        assert r.status_code == 200


def test_firing_alert_invokes_pipeline_once(monkeypatch):
    monkeypatch.setattr(bridge, 'alert_token', lambda: 'private-key')
    seen = []
    monkeypatch.setattr(bridge, 'process_alert', lambda alert: seen.append(alert['fingerprint']))
    bridge._seen.clear()
    event={'status': 'firing', 'labels': {'alertname': 'OpsPilotHighErrorRate'}, 'fingerprint': 'test-123'}
    with TestClient(bridge.app) as client:
        for expected in (1, 0):
            response=client.post('/alerts', headers={'Authorization':'Bearer private-key'}, json={'alerts':[event]})
            assert response.json()['accepted'] == expected
    assert seen == ['test-123']


def test_telegram_requires_secret(monkeypatch):
    monkeypatch.setenv('TELEGRAM_WEBHOOK_SECRET', 'secret')
    with TestClient(bridge.app) as client:
        r=client.post('/telegram/webhook',json={})
        assert r.status_code == 403
        r=client.post('/telegram/webhook',headers={'X-Telegram-Bot-Api-Secret-Token':'secret'},json={})
        assert r.status_code == 200


def test_approval_never_automatically_for_http_spike(monkeypatch):
    monkeypatch.setattr(bridge, 'run_ai_investigation', lambda msg: ({'root_cause':'Test', 'severity':'high', 'confidence':.9, 'evidence':[]},None))
    sent=[]
    monkeypatch.setattr(bridge,'telegram_text',lambda msg:sent.append(msg))
    monkeypatch.setattr(bridge,'inspect_crash', lambda: {'confirmed':False})
    monkeypatch.setattr(bridge,'get_prometheus_snapshot',lambda: '5xx observed')
    monkeypatch.setattr(bridge,'process_crash_evidence',lambda *a: (_ for _ in ()).throw(AssertionError('Must not request approval for HTTP spike')))
    bridge.process_alert({'labels':{'alertname':'OpsPilotHighErrorRate'},'annotations':{'summary':'error spike'}})
    assert len(sent)==1 and 'No remediation executed' in sent[0]
