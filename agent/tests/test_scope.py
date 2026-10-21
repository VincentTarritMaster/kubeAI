"""The monitoring boundary must run before any collection or model request."""

import json

from fastapi.testclient import TestClient
import httpx
import pytest

from app.config import Settings
from app.main import create_app


TEST_API_KEY = "unit-scope-key-0123456789abcdef0123456789"
INCIDENT = ("Analyse l'incident du pod kubeai-demo-incident dans le namespace kubeai "
            "le 18 octobre 2026 entre 11:00 et 11:30 UTC.")


def scope_client(provider):
    calls = {"kubernetes": 0, "mimir": 0, "loki": 0, "llm": 0}

    def kube(request):
        calls["kubernetes"] += 1
        return httpx.Response(200, json={"items": []})

    def mimir(request):
        calls["mimir"] += 1
        return httpx.Response(200, json={"status": "success", "data": {
            "resultType": "matrix" if "query_range" in request.url.path else "vector", "result": []}})

    def loki(request):
        calls["loki"] += 1
        return httpx.Response(200, json={"status": "success", "data": {
            "resultType": "streams", "result": []}})

    def llm(request):
        calls["llm"] += 1
        payload = json.loads(request.content)
        content = '{"priority":"none"}' if payload.get("response_format") else "Données insuffisantes."
        if payload.get("stream"):
            chunks = [{"choices": [{"index": 0, "delta": {"content": content},
                                    "finish_reason": None}]},
                      {"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]}]
            return httpx.Response(200, text="".join("data: " + json.dumps(chunk) + "\n\n"
                                                  for chunk in chunks) + "data: [DONE]\n\n",
                                  headers={"content-type": "text/event-stream"})
        return httpx.Response(200, json={"choices": [{"message": {"content": content},
                                                     "finish_reason": "stop"}]})

    settings = Settings(ca_file="", token_file="", llm_provider=provider,
                        llm_api_key="fake-scope-key", llm_api_key_file="", api_key=TEST_API_KEY)
    client = TestClient(create_app(settings,
        kubernetes_transport=httpx.MockTransport(kube), prometheus_transport=httpx.MockTransport(mimir),
        loki_transport=httpx.MockTransport(loki), llm_transport=httpx.MockTransport(llm)),
        headers={"Authorization": "Bearer " + TEST_API_KEY})
    return client, calls


def ask(client, question, surface, *, history=()):
    if surface == "diagnose":
        return client.post("/diagnose", json={"question": question})
    return client.post("/v1/chat/completions", json={"model": "kubeai", "stream": surface == "sse",
        "messages": [*history, {"role": "user", "content": question}]})


def text_of(response, surface):
    assert response.status_code == 200
    if surface == "diagnose":
        return response.json()["answer"]
    if surface == "json":
        return response.json()["choices"][0]["message"]["content"]
    assert "text/event-stream" in response.headers["content-type"]
    frames = [line[6:] for line in response.text.splitlines() if line.startswith("data: ")]
    assert frames[-1] == "[DONE]"
    parsed = [json.loads(frame) for frame in frames[:-1]]
    assert all("error" not in frame for frame in parsed)
    assert parsed[-1]["choices"][0]["finish_reason"] == "stop"
    return "".join(frame["choices"][0]["delta"].get("content", "") for frame in parsed)


@pytest.mark.parametrize("provider", ["mistral-api", "local"])
@pytest.mark.parametrize("surface", ["json", "sse", "diagnose"])
@pytest.mark.parametrize("question", [
    "Quelle est la météo aujourd'hui à Paris ?",
    "Quelle température fait-il aujourd'hui ?",
    "Qui a gagné le match de football ?",
    "Donne-moi une recette de pâtes.",
])
def test_out_of_scope_is_explicit_and_never_contacts_sources_or_provider(provider, surface, question):
    client, calls = scope_client(provider)
    with client:
        text = text_of(ask(client, question, surface), surface)
    assert "monitoring Kubernetes" in text
    assert not any(calls.values()), calls


@pytest.mark.parametrize("surface", ["json", "sse"])
def test_unrelated_weather_does_not_inherit_an_incident(surface):
    client, calls = scope_client("mistral-api")
    history = [{"role": "user", "content": INCIDENT},
               {"role": "assistant", "content": "Analyse de l'incident, preuves disponibles."}]
    with client:
        text = text_of(ask(client, "Et la météo aujourd'hui ?", surface, history=history), surface)
    assert "monitoring Kubernetes" in text
    assert not any(calls.values()), calls


@pytest.mark.parametrize("surface", ["json", "sse", "diagnose"])
def test_hardware_temperature_is_within_kubernetes_monitoring(surface):
    client, calls = scope_client("mistral-api")
    with client:
        text = text_of(ask(client, "Surveille le CPU, la mémoire et la température des nœuds Kubernetes.",
                           surface), surface)
    assert "kubernetes" in text.lower() or "données" in text.lower()
    assert calls["kubernetes"] > 0


@pytest.mark.parametrize("surface", ["json", "sse"])
@pytest.mark.parametrize("question", ["Pourquoi ?", "Quelles preuves ?"])
def test_short_followup_with_incident_context_remains_in_scope(surface, question):
    client, calls = scope_client("mistral-api")
    history = [{"role": "user", "content": INCIDENT},
               {"role": "assistant", "content": "Analyse de l'incident, preuves disponibles."}]
    with client:
        text = text_of(ask(client, question, surface, history=history), surface)
    assert "monitoring Kubernetes" not in text
    assert calls["kubernetes"] > 0
