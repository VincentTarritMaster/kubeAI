"""Capabilities report with mocked upstreams; no LLM or real network call."""

import json

import httpx
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app

KEY = "unit-api-key-0123456789abcdef0123456789"


def make_client(*, mimir_code=200, ksm="1", cadvisor="1", http_count="10", key=""):
    calls = {"mimir": [], "llm": []}

    def kubernetes(request):
        return httpx.Response(200, json={"metadata": {}, "items": []})

    def mimir(request):
        calls["mimir"].append(request)
        if mimir_code != 200:
            return httpx.Response(mimir_code)
        query = request.url.params["query"]
        if "vector(1)" in query:
            value = "1"
        elif 'job="kube-state-metrics"' in query:
            value = ksm
        elif 'job="kubernetes-cadvisor"' in query:
            value = cadvisor
        elif "http_requests_total" in query:
            value = http_count
        else:
            value = "0"
        result = [] if value is None else [{"metric": {}, "value": [1234.5, value]}]
        return httpx.Response(200, json={
            "status": "success", "data": {"resultType": "vector", "result": result},
        })

    def loki(request):
        return httpx.Response(200, json={
            "status": "success", "data": {"resultType": "streams", "result": []},
        })

    def llm(request):
        calls["llm"].append(request)
        return httpx.Response(500)

    app = create_app(
        Settings(ca_file="", token_file="", llm_api_key_file="", llm_api_key=key, api_key=KEY),
        kubernetes_transport=httpx.MockTransport(kubernetes),
        prometheus_transport=httpx.MockTransport(mimir),
        loki_transport=httpx.MockTransport(loki),
        llm_transport=httpx.MockTransport(llm),
    )
    return TestClient(app), calls


def test_capabilities_reports_sources_and_optional_metrics_without_llm_call():
    api, calls = make_client()
    with api:
        body = api.get("/capabilities").json()
    assert body["namespace"] == "kubeai"
    assert body["read_only"] is True
    assert body["sources"]["mimir"] == "available"
    assert body["metrics"] == {
        "kube_state_metrics": "available",
        "cadvisor": "available",
        "application_http": "available",
    }
    assert body["llm"]["provider"] == "mistral-api"
    assert body["llm"]["framework"] == "langchain"
    assert body["llm"]["configured"] is False
    assert calls["llm"] == []
    assert any("last_over_time(http_requests_total[1h])" in request.url.params["query"]
               for request in calls["mimir"])


def test_capabilities_reports_missing_cadvisor_and_http_metrics():
    api, _ = make_client(cadvisor=None, http_count=None)
    with api:
        body = api.get("/capabilities").json()
    assert body["metrics"]["cadvisor"] == "unavailable"
    assert body["metrics"]["application_http"] == "unavailable"


def test_capabilities_reports_mimir_unavailable_without_failing():
    api, _ = make_client(mimir_code=503)
    with api:
        response = api.get("/capabilities")
    assert response.status_code == 200
    body = response.json()
    assert body["sources"]["mimir"] == "unavailable"
    assert body["metrics"]["kube_state_metrics"] == "unknown"
    assert body["metrics"]["cadvisor"] == "unknown"
    assert body["metrics"]["application_http"] == "unavailable"


def test_capabilities_reports_llm_configured_without_exposing_key():
    secret = "unit-secret-123456"
    api, calls = make_client(key=secret)
    with api:
        text = api.get("/capabilities").text
    assert '"configured":true' in text.replace(" ", "")
    assert secret not in text
    assert json.loads(text)["llm"]["model"] == "mistral-small-2603"
    assert calls["llm"] == []
