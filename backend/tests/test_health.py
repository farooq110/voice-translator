"""Health endpoint contract."""

from __future__ import annotations

from fastapi.testclient import TestClient


def test_health_ok(client: TestClient) -> None:
    res = client.get("/health")
    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "ok"
    assert body["app"] == "ai-voice-translator"
    assert body["protocol"] == 1
    assert body["audio"] == {"encoding": "pcm_s16le", "sample_rate": 16000, "channels": 1}
    assert body["stt"]["provider"] == "fake-stt"
    assert body["stt"]["loaded"] is True
    assert body["sessions"] == {"active": 0, "max": 50}
    assert "version" in body
