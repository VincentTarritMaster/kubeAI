"""Audit and quota behavior for the single-replica local agent."""
import json
import logging

from fastapi.testclient import TestClient
import httpx

from app.config import Settings
from app.main import create_app
from app.services.limits import RateLimiter


KEY = "unit-audit-quota-key-0123456789abcdef"
AUTH = {"Authorization": "Bearer " + KEY}


def test_rate_limiter_rejects_a_burst_and_refills():
    limiter = RateLimiter(per_minute=60, burst=2)
    assert limiter.allow("key", now=0)[0] is True
    assert limiter.allow("key", now=0)[0] is True
    allowed, retry = limiter.allow("key", now=0)
    assert allowed is False and retry >= 1
    assert limiter.allow("key", now=1)[0] is True


def test_api_quota_returns_429_with_retry_after():
    settings = Settings(
        chat_mode="agent", api_key=KEY, namespace="applications",
        kubernetes_access_mode="in-cluster", ca_file="", token_file="",
        rate_limit_per_minute=2, rate_limit_burst=2, audit_enabled=False,
        llm_provider="local", llm_api_key_file="",
    )
    app = create_app(
        settings,
        kubernetes_transport=httpx.MockTransport(lambda request: httpx.Response(200, json={"items": []})),
        prometheus_transport=httpx.MockTransport(lambda request: httpx.Response(200, json={
            "status": "success", "data": {"resultType": "matrix", "result": []}})),
        loki_transport=httpx.MockTransport(lambda request: httpx.Response(200, json={
            "status": "success", "data": {"resultType": "streams", "result": []}})),
        llm_transport=httpx.MockTransport(lambda request: httpx.Response(500)),
    )
    with TestClient(app) as client:
        assert client.post("/diagnose", headers=AUTH, json={}).status_code != 429
        assert client.post("/diagnose", headers=AUTH, json={}).status_code != 429
        response = client.post("/diagnose", headers=AUTH, json={})
        assert response.status_code == 429
        assert response.headers["retry-after"].isdigit()


def test_audit_line_contains_fingerprint_and_not_the_key(caplog):
    caplog.set_level(logging.INFO, logger="kubeai.audit")
    settings = Settings(
        chat_mode="agent", api_key=KEY, namespace="applications",
        kubernetes_access_mode="in-cluster", ca_file="", token_file="",
        audit_enabled=True, rate_limit_per_minute=0, rate_limit_burst=0,
        llm_provider="local", llm_api_key_file="",
    )
    app = create_app(
        settings,
        kubernetes_transport=httpx.MockTransport(lambda request: httpx.Response(200, json={"items": []})),
        prometheus_transport=httpx.MockTransport(lambda request: httpx.Response(200, json={
            "status": "success", "data": {"resultType": "matrix", "result": []}})),
        loki_transport=httpx.MockTransport(lambda request: httpx.Response(204)),
        llm_transport=httpx.MockTransport(lambda request: httpx.Response(500)),
    )
    with TestClient(app) as client:
        assert client.get("/v1/models", headers=AUTH).status_code == 200
    records = [json.loads(record.message) for record in caplog.records
               if record.name == "kubeai.audit"]
    assert records
    request = next(record for record in records if record["event"] == "request")
    assert request["endpoint"] == "/v1/models"
    assert request["key_id"].startswith("sha256:")
    assert KEY not in json.dumps(records)
