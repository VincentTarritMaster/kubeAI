"""Natural incident dates and resources must resolve without invented values."""

import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace
import time

import httpx
import pytest
from fastapi.testclient import TestClient

from app.clients.loki_client import LokiClient
from app.config import Settings
from app.main import create_app
from app.services.incident_timeline import _components, parse_chat_window, parse_window, resolve_request
from test_incident_timeline import client, pod


@pytest.mark.parametrize("written", [
    "22.10.2026", "22/10/2026", "22-10-2026", "22 octobre 2026",
    "22 oct. 2026", "22 October 2026", "October 22, 2026", "2026-10-22",
])
def test_complete_local_dates_normalize_to_the_exact_zurich_civil_day(written):
    window, error = parse_window(f"Analyse l'incident du pod demo dans kubeai le {written}")
    assert error is None
    assert window["start"].isoformat() == "2026-10-21T22:00:00+00:00"
    assert window["end"].isoformat() == "2026-10-22T22:00:00+00:00"
    assert window["timezone"] == "Europe/Zurich"
    assert (window["namespace"], window["pod"]) == ("kubeai", "demo")


def test_local_hours_and_explicit_utc_keep_the_exact_requested_bounds():
    local, error = parse_window("incident pod demo dans kubeai le 22 octobre 2026 entre 09:40 et 09:55")
    assert error is None
    assert local["start"].isoformat() == "2026-10-22T07:40:00+00:00"
    assert local["end"].isoformat() == "2026-10-22T07:55:00+00:00"
    assert local["timezone"] == "Europe/Zurich"
    utc, error = parse_window("incident pod demo dans kubeai début=2026-10-22T07:57:45Z fin=2026-10-22T08:00:00Z")
    assert error is None
    assert utc["start"].isoformat() == "2026-10-22T07:57:45+00:00"
    assert utc["end"].isoformat() == "2026-10-22T08:00:00+00:00"
    assert utc["timezone"] == "UTC"


@pytest.mark.parametrize("written,hours", [("29 mars 2026", 23), ("25 octobre 2026", 25)])
def test_zurich_civil_days_follow_summer_and_winter_time(written, hours):
    window, error = parse_window("incident du pod demo dans kubeai le " + written)
    assert error is None
    assert (window["end"] - window["start"]).total_seconds() == hours * 3600


@pytest.mark.parametrize("written", ["29 mars 2026 entre 02:30 et 03:30",
                                     "25 octobre 2026 entre 02:30 et 03:30"])
def test_nonexistent_or_ambiguous_local_hour_requests_an_offset(written):
    window, error = parse_window("incident du pod demo dans kubeai le " + written)
    assert window is None and "décalage UTC explicite" in error


@pytest.mark.parametrize("question,expected", [
    ("incident du pod demo le 22 octobre entre 09:40 et 09:55", "année"),
    ("incident du pod demo à 09:40", "date complète"),
    ("incident du pod demo ce matin", "date complète"),
    ("incident du pod demo le 2026-10-22T07:57:45Z", "fin"),
])
def test_missing_calendar_facts_are_requested_instead_of_guessed(question, expected):
    window, error = parse_window(question)
    assert window is None and expected in error


def test_year_clarification_keeps_the_original_clock_and_target():
    messages = [SimpleNamespace(role="user", content="Incident du pod demo dans kubeai le 22 octobre entre 09:40 et 09:55"),
                SimpleNamespace(role="assistant", content="Quelle année pour cette date ?"),
                SimpleNamespace(role="user", content="2026")]
    window, error = parse_chat_window(SimpleNamespace(messages=messages))
    assert error is None
    assert window["start"] == datetime(2026, 10, 22, 7, 40, tzinfo=timezone.utc)
    assert window["end"] == datetime(2026, 10, 22, 7, 55, tzinfo=timezone.utc)
    assert (window["namespace"], window["pod"]) == ("kubeai", "demo")


def test_pod_forms_never_turn_filler_words_into_kubernetes_names():
    singular = _components("Analyse l'incident du pod open-webui-xxxxx dans kubeai le 22 octobre 2026")
    plural = _components("Analyse les pods open-webui dans le namespace kubeai le 22 octobre 2026")
    filler = _components("les pods concernés et leurs logs le 22 octobre 2026")
    assert (singular["namespace"], singular["pod"]) == ("kubeai", "open-webui-xxxxx")
    assert (plural["namespace"], plural["pod"], plural["pod_family"]) == ("kubeai", "open-webui", True)
    assert filler["pod"] is None
    assert _components("ressources concernes/et le 22 octobre 2026")["pod"] is None


@pytest.mark.parametrize("question", [
    "Analyse l'incident du pod open-webui-xxxxx dans kubeai, le 22 octobre 2026 entre 09:40 et 09:55",
    "Analyse les pods open-webui dans le namespace kubeai, le 22 octobre 2026 entre 09:40 et 09:55",
])
def test_kubernetes_inventory_resolves_the_named_pod_family(question):
    start = datetime(2026, 10, 22, 7, 40, tzinfo=timezone.utc).timestamp()
    end = datetime(2026, 10, 22, 7, 55, tzinfo=timezone.utc).timestamp()
    api, calls = client(pods=[pod("open-webui-abc")], events=[], expected_start=start,
                        expected_end=end, expected_pod="open-webui-abc")
    with api:
        response = api.post("/diagnose", json={"question": question})
    assert response.status_code == 200
    assert "kubeai/open-webui-abc" in response.json()["answer"]
    assert calls["kubernetes"] and calls["mimir"] and calls["loki"]


def test_multiple_pods_in_a_family_are_presented_for_selection():
    api, calls = client(pods=[pod("open-webui-abc"), pod("open-webui-def")], events=[])
    with api:
        response = api.post("/diagnose", json={"question":
            "Analyse les pods open-webui dans le namespace kubeai le 22 octobre 2026 entre 09:40 et 09:55"})
    assert response.status_code == 200
    answer = response.json()["answer"]
    assert "Plusieurs pods" in answer and "open-webui-abc" in answer and "open-webui-def" in answer
    assert not calls["mimir"] and not calls["loki"]


def test_latest_demo_uses_verified_loki_markers_with_exact_nanosecond_bounds():
    base = time.time_ns() - 300 * 10**9
    phases = ["normal", "anomaly_start", "recovered", "complete"]
    stamps = [base + index * 60 * 10**9 for index in range(4)]

    def loki(request):
        assert request.url.params["query"] == (
            '{namespace="kubeai",pod="kubeai-demo-incident"} |= "KUBEAI_DEMO_INCIDENT"')
        assert request.url.params["direction"] == "backward"
        return httpx.Response(200, json={"status": "success", "data": {"resultType": "streams", "result": [
            {"stream": {"namespace": "kubeai", "pod": "kubeai-demo-incident"},
             "values": [[str(stamp), f"KUBEAI_DEMO_INCIDENT phase={phase}"]
                        for stamp, phase in zip(stamps, phases)]}]}})

    async def check():
        async with httpx.AsyncClient(base_url="http://loki", transport=httpx.MockTransport(loki)) as http:
            source = LokiClient(http, Settings())
            episode = await source.latest_demo_episode()
            assert episode["start_ns"] == stamps[0] and episode["end_ns"] == stamps[-1]
            service = SimpleNamespace(loki=source)
            chat = SimpleNamespace(messages=[SimpleNamespace(role="user", content=
                "Analyse l'incident du dernier script de démonstration")])
            window, error, _ = await resolve_request(service, chat)
            assert error is None and window["start_ns"] == stamps[0] and window["end_ns"] == stamps[-1]
            assert window["timezone"] == "UTC" and window["pod"] == "kubeai-demo-incident"

    asyncio.run(check())


def test_demo_b_then_c_in_one_chat_uses_the_four_real_marker_bounds():
    base = time.time_ns() - 300 * 10**9
    phases = ["normal", "anomaly_start", "recovered", "complete"]
    stamps = [base + index * 60 * 10**9 for index in range(4)]
    calls = []

    def kubernetes(request):
        calls.append("kubernetes")
        rows = ([pod("kubeai-demo-incident")] if request.url.path.endswith("/pods") else [])
        return httpx.Response(200, json={"metadata": {}, "items": rows})

    def loki(request):
        query = request.url.params["query"]
        calls.append("markers" if "KUBEAI_DEMO_INCIDENT" in query else "loki")
        if "KUBEAI_DEMO_INCIDENT" not in query:
            assert int(request.url.params["start"]) == stamps[0]
            assert int(request.url.params["end"]) == stamps[-1] + 1
        values = [[str(stamp), f"KUBEAI_DEMO_INCIDENT phase={phase}"]
                  for stamp, phase in zip(stamps, phases)
                  if ("KUBEAI_DEMO_INCIDENT" in query or stamp < int(request.url.params["end"]))]
        return httpx.Response(200, json={"status": "success", "data": {"resultType": "streams", "result": [
            {"stream": {"namespace": "kubeai", "pod": "kubeai-demo-incident", "container": "demo"},
             "values": values}]}})

    def mimir(request):
        calls.append("mimir")
        assert abs(float(request.url.params["start"]) - stamps[0] / 10**9) < 0.001
        assert abs(float(request.url.params["end"]) - stamps[-1] / 10**9) < 0.001
        query = request.url.params["query"]
        rows = ([{"metric": {"namespace": "kubeai", "pod": "kubeai-demo-incident"},
                  "values": [[stamps[1] / 10**9, "0.08"]]}]
                if "process_cpu_seconds_total" in query else [])
        return httpx.Response(200, json={"status": "success", "data": {"resultType": "matrix", "result": rows}})

    settings = Settings(ca_file="", token_file="", llm_api_key_file="", llm_api_key="",
                        api_key="unit-api-key-0123456789abcdef0123456789")
    app = create_app(settings, kubernetes_transport=httpx.MockTransport(kubernetes),
                     prometheus_transport=httpx.MockTransport(mimir),
                     loki_transport=httpx.MockTransport(loki))
    question = "Analyse l'incident du dernier script de démonstration"
    with TestClient(app, headers={"Authorization": "Bearer " + settings.api_key}) as api:
        first = api.post("/diagnose", json={"question": question})
        assert first.status_code == 200
        report = first.json()["observation"]
        assert {phase for phase in phases if any("phase=" + phase in item["observation"]
                                                for item in report["timeline"])} == set(phases)
        assert calls[0:2] == ["markers", "loki"]
        history = [{"role": "user", "content": question},
                   {"role": "assistant", "content": first.json()["answer"]},
                   {"role": "user", "content": "Pour ce même incident, corrèle les logs et le CPU"}]
        second = api.post("/v1/chat/completions", json={"model": "kubeai", "messages": history})
        assert second.status_code == 200
        assert "Corrélation de l'incident" in second.json()["choices"][0]["message"]["content"]
