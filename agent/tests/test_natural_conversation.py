"""Natural incident questions use only bounded, read-only source searches."""

from datetime import datetime, timedelta, timezone
import json

import httpx
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.models import ChatRequest
from app.services.incident_timeline import chat_components
from test_incident_timeline import pod

KEY = "unit-api-key-0123456789abcdef0123456789"


def recent_october_19():
    now = datetime.now(timezone.utc)
    day = datetime(now.year, 10, 19, tzinfo=timezone.utc)
    return day if day <= now else day.replace(year=now.year - 1)


def make_client(*, pods=None, event_present=True, logs_present=True, metrics_present=True,
                mistral_key=""):
    day = recent_october_19()
    incident_time = day + timedelta(hours=18, minutes=30)
    calls = {"kubernetes": [], "mimir": [], "loki": [], "llm": []}
    pods = [pod("sample-incident", "operations")] if pods is None else pods
    events = ([{"metadata": {"name": "failure", "namespace": "operations",
                            "creationTimestamp": incident_time.isoformat()},
                "type": "Warning", "reason": "Failed", "message": "observed failure",
                "involvedObject": {"kind": "Pod", "name": "sample-incident", "namespace": "operations"}}]
              if event_present else [])

    def kubernetes(request):
        calls["kubernetes"].append(request)
        data = {"/api/v1/nodes": [], "/api/v1/pods": pods, "/api/v1/events": events}
        return httpx.Response(200, json={"metadata": {}, "items": data[request.url.path]})

    def mimir(request):
        calls["mimir"].append(request)
        if request.url.path == "/prometheus/api/v1/query":
            return httpx.Response(200, json={"status": "success", "data": {"resultType": "vector", "result": []}})
        assert request.url.path == "/prometheus/api/v1/query_range"
        start, end = (float(request.url.params[key]) for key in ("start", "end"))
        assert 0 < end - start <= 6 * 3600
        assert 'namespace="operations"' in request.url.params["query"]
        assert 'pod="sample-incident"' in request.url.params["query"]
        rows = ([{"metric": {"namespace": "operations", "pod": "sample-incident"},
                  "values": [[start + 60, "0.02"]]}] if metrics_present else [])
        return httpx.Response(200, json={"status": "success", "data": {"resultType": "matrix", "result": rows}})

    def loki(request):
        calls["loki"].append(request)
        assert request.url.params["query"] == '{namespace="operations",pod="sample-incident"}'
        start, end = (int(request.url.params[key]) for key in ("start", "end"))
        assert 0 < end - start <= 6 * 3600 * 10**9 + 1
        values = ([[str(int(incident_time.timestamp() * 10**9)), "application error observed"]]
                  if logs_present and start <= int(incident_time.timestamp() * 10**9) <= end else [])
        return httpx.Response(200, json={"status": "success", "data": {"resultType": "streams", "result": [
            {"stream": {"namespace": "operations", "pod": "sample-incident", "container": "app"},
             "values": values}]}})

    def llm(request):
        calls["llm"].append(request)
        assert request.url.scheme == "https"
        assert request.headers["authorization"] == "Bearer " + mistral_key
        assert "observed failure" not in request.content.decode()
        return httpx.Response(200, json={"choices": [{"message": {"content": '{"focus":"loki"}'}}]})

    app = create_app(Settings(ca_file="", token_file="", api_key=KEY,
                              llm_api_key=mistral_key, llm_api_key_file=""),
                     kubernetes_transport=httpx.MockTransport(kubernetes),
                     prometheus_transport=httpx.MockTransport(mimir),
                     loki_transport=httpx.MockTransport(loki), llm_transport=httpx.MockTransport(llm))
    return TestClient(app, headers={"Authorization": "Bearer " + KEY}), calls, day


def ask(api, messages, *, stream=False):
    response = api.post("/v1/chat/completions", json={"model": "kubeai", "messages": messages, "stream": stream})
    assert response.status_code == 200
    if not stream:
        return response.json()["choices"][0]["message"]["content"]
    frames = [line[6:] for line in response.text.splitlines() if line.startswith("data: ")]
    assert frames[-1] == "[DONE]"
    return "".join(json.loads(item)["choices"][0]["delta"].get("content", "")
                   for item in frames[:-1])


def test_b_pod_and_date_only_resolves_namespace_and_searches_then_refines():
    api, calls, day = make_client()
    with api:
        answer = ask(api, [{"role": "user", "content": f"investigation de l'incident sample-incident du 19 octobre {day.year}"}])
    assert "operations/sample-incident" in answer
    assert "Chronologie" in answer
    assert "balayage en fenêtres de six heures" in answer
    assert "signal daté" in answer
    assert str(day.year) in answer
    assert len(calls["mimir"]) == 20 and len(calls["loki"]) == 5
    assert calls["llm"] == []


def test_b_complete_iso_window_still_uses_single_bounded_query():
    api, calls, day = make_client()
    start, end = day + timedelta(hours=18), day + timedelta(hours=19)
    question = (f"Analyse l'incident début={start.isoformat()} fin={end.isoformat()} "
                "namespace=operations pod=sample-incident")
    with api:
        answer = ask(api, [{"role": "user", "content": question}])
    assert "operations/sample-incident" in answer
    assert "balayage de quatre fenêtres" not in answer
    assert len(calls["mimir"]) == 8 and len(calls["loki"]) == 1


def test_date_only_b_streams_and_mistral_only_prioritizes_verified_sources():
    api, calls, day = make_client(mistral_key="unit-secret-123456")
    with api:
        answer = ask(api, [{"role": "user", "content": f"incident du pod sample-incident le 19 octobre {day.year}"}],
                     stream=True)
    assert "Chronologie" in answer and "operations/sample-incident" in answer
    assert len(calls["llm"]) == 1
    assert "Mistral API" in answer


def test_b_multiple_matching_pods_asks_one_targeted_question():
    api, calls, day = make_client(pods=[pod("sample-incident", "operations"),
                                     pod("sample-incident", "production")], event_present=False)
    with api:
        answer = ask(api, [{"role": "user", "content": f"incident du pod sample-incident le 19 octobre {day.year}"}])
    assert "Plusieurs pods" in answer
    assert "operations/sample-incident" in answer and "production/sample-incident" in answer
    assert calls["mimir"] == [] and calls["loki"] == []


def test_b_expired_historical_sources_are_reported_without_guessing_time():
    api, calls, day = make_client(event_present=False, logs_present=False, metrics_present=False)
    with api:
        answer = ask(api, [{"role": "user", "content": f"incident du pod sample-incident le 19 octobre {day.year}"}])
    assert "aucun log pertinent conservé" in answer
    assert "expiration ou absence possible" in answer
    assert "Aucune anomalie horodatée" in answer
    assert len(calls["mimir"]) == 12 and len(calls["loki"]) == 4


def test_b_a_c_and_short_followups_keep_incident_context_with_long_answers():
    api, _, day = make_client()
    incident = f"Analyse l'incident du pod sample-incident le 19 octobre {day.year}."
    history = [{"role": "user", "content": incident}]
    with api:
        first = ask(api, history)
        assert "Chronologie" in first
        history += [{"role": "assistant", "content": "longue réponse B " * 1300},
                    {"role": "user", "content": "état de santé actuel du cluster"}]
        assert "État global" in ask(api, history, stream=True)
        history += [{"role": "assistant", "content": "longue réponse A " * 1300},
                    {"role": "user", "content": "corrélation des logs et métriques de l'incident précédent, avec hypothèses de cause"}]
        correlated = ask(api, history, stream=True)
        assert "Corrélation de l'incident" in correlated
        assert "operations/sample-incident" in correlated
        history += [{"role": "assistant", "content": correlated},
                    {"role": "user", "content": "Quelles preuves ?"}]
        assert "Corrélation de l'incident" in ask(api, history)
        history += [{"role": "assistant", "content": "résumé"},
                    {"role": "user", "content": "Pourquoi ?"}]
        assert "Corrélation de l'incident" in ask(api, history)


def test_b_then_c_immediately_and_short_followups_without_repeating_parameters():
    api, _, day = make_client()
    history = [{"role": "user", "content": f"Analyse l'incident du pod sample-incident le 19 octobre {day.year}."}]
    with api:
        first = ask(api, history)
        history += [{"role": "assistant", "content": first},
                    {"role": "user", "content": "Quelles preuves ?"}]
        assert "Chronologie" in ask(api, history)
        history += [{"role": "assistant", "content": "Résumé des preuves"},
                    {"role": "user", "content": "Corrèle ses logs et métriques."}]
        assert "Corrélation de l'incident" in ask(api, history)
        history += [{"role": "assistant", "content": "Résumé"},
                    {"role": "user", "content": "Quelle est la cause probable ?"}]
        assert "Corrélation de l'incident" in ask(api, history)


def test_c_after_failed_b_does_not_pretend_a_window_was_found():
    api, calls, day = make_client(pods=[pod("sample-incident", "operations"),
                                     pod("sample-incident", "production")], event_present=False)
    history = [{"role": "user", "content": f"Analyse l'incident du pod sample-incident le 19 octobre {day.year}"}]
    with api:
        first = ask(api, history)
        assert "Plusieurs pods" in first
        history += [{"role": "assistant", "content": first},
                    {"role": "user", "content": "Quelle est la cause probable de cet incident ?"}]
        second = ask(api, history)
    assert "Plusieurs pods" in second
    assert "Corrélation de l'incident" not in second
    assert calls["mimir"] == [] and calls["loki"] == []


def test_c_after_b_without_historical_evidence_rechecks_and_keeps_uncertainty():
    api, calls, day = make_client(event_present=False, logs_present=False, metrics_present=False)
    history = [{"role": "user", "content": f"Analyse l'incident du pod sample-incident le 19 octobre {day.year}"}]
    with api:
        first = ask(api, history)
        history += [{"role": "assistant", "content": first},
                    {"role": "user", "content": "Corrèle les preuves de l'incident précédent"}]
        second = ask(api, history)
    assert "Aucune anomalie horodatée" in first
    assert "cause indéterminée" in second
    assert "aucun log pertinent conservé" in second
    assert len(calls["mimir"]) == 24 and len(calls["loki"]) == 8


def test_followup_never_combines_two_different_incidents():
    chat = ChatRequest(messages=[
        {"role": "user", "content": "incident du pod first-pod le 19 octobre 2026"},
        {"role": "assistant", "content": "ancienne réponse"},
        {"role": "user", "content": "incident du pod second-pod"},
        {"role": "assistant", "content": "Précisez la date"},
        {"role": "user", "content": "Quelle est la cause de cet incident ?"},
    ])
    parts = chat_components(chat)
    assert parts["pod"] == "second-pod"
    assert parts["date"] is None
