"""Simulated, network-isolated investigation of an incident window."""

from datetime import datetime, timezone
import json

import httpx
import pytest
from fastapi.testclient import TestClient

from app.clients.kubernetes_client import KubernetesClient
from app.clients.loki_client import LokiClient
from app.clients.prometheus_client import PrometheusClient
from app.config import Settings
from app.main import create_app
from app.services.incident_timeline import parse_window

START = "2026-10-19T14:00:00+02:00"
END = "2026-10-19T14:30:00+02:00"
QUESTION = f"Que s'est-il passé lors de l'incident début={START} fin={END} namespace=kubeai pod=kubeai-demo-failure ?"
FIRST = datetime(2026, 10, 19, 12, 0, tzinfo=timezone.utc).timestamp()


def event(name, second, *, namespace="kubeai", pod="kubeai-demo-failure", reason="Failed",
          message="simulation", timestamp=True):
    metadata = {"name": name, "namespace": namespace}
    if timestamp:
        metadata["creationTimestamp"] = datetime.fromtimestamp(FIRST + second, tz=timezone.utc).isoformat()
    return {"metadata": metadata, "type": "Warning", "reason": reason, "message": message,
            "involvedObject": {"kind": "Pod", "name": pod, "namespace": namespace}}


def pod(name="kubeai-demo-failure", namespace="kubeai"):
    return {"metadata": {"name": name, "namespace": namespace, "uid": "sample-uid"},
            "status": {"phase": "Failed", "conditions": [{"type": "Ready", "status": "False"}],
                       "containerStatuses": [{"name": "demo", "restartCount": 1,
                                              "ready": False, "state": {"terminated": {"reason": "Error"}}}]}}


def client(*, events=None, pods=None, key="", kube_code=200, mimir_code=200, loki_code=200,
           mimir_empty=False, loki_empty=False, llm_code=200, llm_text='{"focus":"loki"}',
           timeout_source=None, mimir_series=1, loki_lines=1, outside_log=False,
           mimir_memory_code=200):
    calls = {"kubernetes": [], "mimir": [], "loki": [], "llm": []}
    events = [event("late", 180), event("early", 10)] if events is None else events
    pods = [pod()] if pods is None else pods

    def kubernetes(request):
        calls["kubernetes"].append((request.url.path, dict(request.url.params)))
        if timeout_source == "kubernetes":
            raise httpx.ReadTimeout("simulated")
        if kube_code != 200:
            return httpx.Response(kube_code)
        data = {"/api/v1/nodes": [], "/api/v1/pods": pods, "/api/v1/events": events}
        assert request.url.path in data
        return httpx.Response(200, json={"metadata": {}, "items": data[request.url.path]})

    def mimir(request):
        calls["mimir"].append(request)
        if timeout_source == "mimir":
            raise httpx.ReadTimeout("simulated")
        assert request.url.path == "/prometheus/api/v1/query_range"
        assert float(request.url.params["start"]) == FIRST
        assert float(request.url.params["end"]) == FIRST + 1800
        assert 30 <= int(request.url.params["step"]) <= 300
        assert 'namespace="kubeai"' in request.url.params["query"]
        assert 'pod="kubeai-demo-failure"' in request.url.params["query"]
        if mimir_code != 200:
            return httpx.Response(mimir_code)
        if mimir_memory_code != 200 and "rate(" not in request.url.params["query"]:
            return httpx.Response(mimir_memory_code)
        value = "0.25" if "rate(" in request.url.params["query"] else "10485760"
        rows = [] if mimir_empty else [{"metric": {"namespace": "kubeai", "pod": "kubeai-demo-failure"},
                                        "values": [[FIRST + 60, value]]} for _ in range(mimir_series)]
        return httpx.Response(200, json={"status": "success", "data": {"resultType": "matrix", "result": rows}})

    def loki(request):
        calls["loki"].append(request)
        if timeout_source == "loki":
            raise httpx.ReadTimeout("simulated")
        assert request.url.path == "/loki/api/v1/query_range"
        assert request.url.params["query"] == '{namespace="kubeai",pod="kubeai-demo-failure"}'
        assert request.url.params["direction"] == "forward"
        assert int(request.url.params["limit"]) == LokiClient.MAX_LINES + 1
        if loki_code != 200:
            return httpx.Response(loki_code)
        values = [] if loki_empty else [[str(int((FIRST + 30 + i) * 10**9)),
                                         "simulation only: exit code 42"] for i in range(loki_lines)]
        if outside_log:
            values.append([str(int((FIRST - 30) * 10**9)), "clock divergence"])
        return httpx.Response(200, json={"status": "success", "data": {"resultType": "streams", "result": [
            {"stream": {"namespace": "kubeai", "pod": "kubeai-demo-failure", "container": "demo"},
             "values": values}]}})

    def llm(request):
        calls["llm"].append(request)
        assert request.url.path == "/v1/chat/completions"
        assert json.loads(request.content)["max_tokens"] == 32
        assert "simulation only: exit code 42" not in request.content.decode()
        return httpx.Response(llm_code, json={"choices": [{"message": {"content": llm_text}}]})

    app = create_app(Settings(ca_file="", token_file="", llm_api_key_file="", llm_api_key=key),
                     kubernetes_transport=httpx.MockTransport(kubernetes),
                     prometheus_transport=httpx.MockTransport(mimir),
                     loki_transport=httpx.MockTransport(loki),
                     llm_transport=httpx.MockTransport(llm))
    return TestClient(app), calls


def ask(api, question=QUESTION, stream=False):
    return api.post("/v1/chat/completions", json={"model": "kubeai", "stream": stream,
                                                   "messages": [{"role": "user", "content": question}]})


def body(response):
    assert response.status_code == 200
    return response.json()["choices"][0]["message"]["content"]


def sse_body(response):
    assert response.status_code == 200
    frames = [line[6:] for line in response.text.splitlines() if line.startswith("data: ")]
    assert frames[-1] == "[DONE]"
    chunks = [json.loads(frame) for frame in frames[:-1]]
    assert chunks[-1]["choices"][0]["finish_reason"] == "stop"
    return "".join(chunk["choices"][0]["delta"].get("content", "") for chunk in chunks)


def test_combined_timeline_sorted_and_current_state_distinguished():
    api, calls = client()
    with api:
        response = api.post("/diagnose", json={"question": QUESTION})
    assert response.status_code == 200
    report = response.json()["observation"]
    timeline = report["timeline"]
    assert [entry["source"] for entry in timeline] == ["Kubernetes", "Loki", "Mimir", "Mimir", "Kubernetes"]
    assert [entry["timestamp"] for entry in timeline] == sorted(entry["timestamp"] for entry in timeline)
    assert {entry["type"] for entry in timeline} == {"événement", "log", "métrique"}
    assert all(entry["reference"] and entry["resource"] for entry in timeline)
    assert report["current_pods"][0]["phase"] == "Failed"
    assert "actuels et ne prouvent pas leur état passé" in response.json()["answer"]
    assert "cause racine certaine" in response.json()["answer"]
    assert len(calls["mimir"]) == 2 and len(calls["loki"]) == 1 and calls["llm"] == []


@pytest.mark.parametrize("question", ["Que s'est-il passé lors de l'incident de 14 h ?",
                                     "Pourquoi le pod backend a-t-il redémarré ce matin ?",
                                     "Incident début=2026-10-19T14:00 fin=2026-10-19T14:30 namespace=kubeai"])
def test_ambiguous_window_requests_clarification_without_source_calls(question):
    api, calls = client()
    with api:
        response = api.post("/diagnose", json={"question": question})
    assert response.json()["observation"]["status"] == "clarification_required"
    assert "fuseau" in response.json()["answer"]
    assert not any(calls.values())


def test_timezone_normalization_and_invalid_interval():
    window, error = parse_window("incident début=2026-10-19T14:00:00+02:00 fin=2026-10-19T13:00:00Z")
    assert error is None and window["start"].isoformat() == "2026-10-19T12:00:00+00:00"
    assert window["end"].isoformat() == "2026-10-19T13:00:00+00:00"
    _, error = parse_window("incident début=2026-10-19T14:00:00+02:00 fin=2026-10-19T13:00:00+02:00")
    assert "positif" in error
    _, error = parse_window("incident début=2026-10-19T14:00:00+02:00 fin=2026-10-20T14:00:00+02:00")
    assert "six heures" in error


def test_explicit_window_followup_routes_to_incident_without_repeating_keyword():
    api, _ = client()
    question = f"début={START} fin={END} namespace=kubeai pod=kubeai-demo-failure"
    with api:
        text = body(ask(api, question))
    assert "**Chronologie**" in text


def test_empty_window_and_expired_events_are_not_falsely_healthy():
    api, _ = client(events=[], mimir_empty=True, loki_empty=True)
    with api:
        response = api.post("/diagnose", json={"question": QUESTION})
    report = response.json()["observation"]
    assert report["timeline"] == [] and report["data_status"] == "partial"
    assert "expiré" in response.json()["answer"]
    assert "Aucune observation datée" in response.json()["answer"]


def test_other_namespace_and_duplicate_events_are_filtered():
    events = [event("dup", 10), event("dup", 10), event("other", 20, namespace="other", pod="other-pod")]
    api, _ = client(events=events, pods=[pod(), pod("other-pod", "other")])
    with api:
        report = api.post("/diagnose", json={"question": QUESTION}).json()["observation"]
    assert len([e for e in report["timeline"] if e["source"] == "Kubernetes"]) == 1
    assert all("other" not in e["resource"] for e in report["timeline"])
    assert len(report["current_pods"]) == 1


@pytest.mark.parametrize("code", [403, 503])
def test_loki_failure_preserves_other_evidence(code):
    api, _ = client(loki_code=code)
    with api:
        report = api.post("/diagnose", json={"question": QUESTION}).json()["observation"]
    assert report["sources"]["loki"]["status"] == "unavailable"
    assert any(e["source"] == "Mimir" for e in report["timeline"])
    assert report["data_status"] == "partial"


def test_mimir_absence_and_403_are_explicit():
    for options, status in (({"mimir_empty": True}, "ok"), ({"mimir_code": 403}, "unavailable")):
        api, _ = client(**options)
        with api:
            report = api.post("/diagnose", json={"question": QUESTION}).json()["observation"]
        assert report["sources"]["mimir"]["status"] == status
        assert "aucun échantillon métrique pertinent" in report["missing"]


def test_kubernetes_forbidden_keeps_loki_and_mimir():
    api, _ = client(kube_code=403)
    with api:
        report = api.post("/diagnose", json={"question": QUESTION}).json()["observation"]
    assert report["sources"]["kubernetes_events"]["status"] == "unavailable"
    assert report["current_pods"] == []
    assert {e["source"] for e in report["timeline"]} == {"Mimir", "Loki"}


def test_mistral_failure_or_untrusted_cause_keeps_deterministic_answer():
    for options in ({"key": "test-key", "llm_code": 503},
                    {"key": "test-key", "llm_text": "La base de données est tombée à 14 h."}):
        api, calls = client(**options)
        with api:
            text = body(ask(api))
        assert "base de données est tombée" not in text
        assert "Chronologie" in text and "cause racine certaine" in text
        assert len(calls["llm"]) == 1


def test_json_sse_and_diagnose_use_same_evidence_path():
    api, _ = client()
    with api:
        json_text = body(ask(api))
        sse_text = sse_body(ask(api, stream=True))
        diagnosis = api.post("/diagnose", json={"question": QUESTION})
    assert "simulation only: exit code 42" in json_text
    assert "simulation only: exit code 42" in sse_text
    assert diagnosis.json()["observation"]["scope"] == "incident"


def test_kubernetes_pagination_and_loki_volume_are_bounded(monkeypatch):
    monkeypatch.setattr(KubernetesClient, "MAX_EVENTS", 1)
    monkeypatch.setattr(LokiClient, "MAX_LINES", 1)
    api, _ = client(events=[event("a", 10), event("b", 20)], loki_lines=4)
    with api:
        report = api.post("/diagnose", json={"question": QUESTION}).json()["observation"]
    assert report["sources"]["kubernetes_events"]["status"] == "partial"
    assert report["sources"]["loki"]["status"] == "partial"
    assert len([e for e in report["timeline"] if e["type"] == "événement"]) <= 1
    assert len([e for e in report["timeline"] if e["type"] == "log"]) == 1


def test_mimir_series_cap_and_noncausal_sample_boundary(monkeypatch):
    monkeypatch.setattr(PrometheusClient, "MAX_SERIES", 1)
    api, _ = client(mimir_series=3)
    with api:
        response = api.post("/diagnose", json={"question": QUESTION})
    text = response.json()["answer"]
    assert response.json()["observation"]["sources"]["mimir"]["status"] == "partial"
    assert "Échantillon mesuré" in text
    assert "instant exact du début" not in text
    assert "cause racine certaine" in text


@pytest.mark.parametrize("source", ["kubernetes", "mimir", "loki"])
def test_source_timeout_is_reported_without_losing_other_evidence(source):
    api, _ = client(timeout_source=source)
    with api:
        report = api.post("/diagnose", json={"question": QUESTION}).json()["observation"]
    name = "kubernetes_events" if source == "kubernetes" else source
    assert report["sources"][name]["status"] == "unavailable"
    assert report["data_status"] == "partial"


def test_conflicting_event_versions_are_flagged_without_causal_claim():
    events = [event("same", 10, reason="Started"), event("same", 20, reason="Failed")]
    api, _ = client(events=events)
    with api:
        text = body(ask(api))
    assert "Versions contradictoires" in text
    assert "Aucune cause racine certaine" in text


def test_loki_outside_window_is_excluded_and_clock_warning_is_explicit():
    api, _ = client(outside_log=True)
    with api:
        report = api.post("/diagnose", json={"question": QUESTION}).json()["observation"]
    assert report["sources"]["loki"]["status"] == "partial"
    assert any("horloges" in warning for warning in report["warnings"])
    assert not any("clock divergence" in item["observation"] for item in report["timeline"])


def test_partial_mimir_retains_cpu_samples_when_memory_query_fails():
    api, _ = client(mimir_memory_code=403)
    with api:
        report = api.post("/diagnose", json={"question": QUESTION}).json()["observation"]
    assert report["sources"]["mimir"]["status"] == "partial"
    metrics = [item for item in report["timeline"] if item["source"] == "Mimir"]
    assert len(metrics) == 1 and "CPU" in metrics[0]["observation"]
    assert not any("mémoire active" in item["observation"] for item in metrics)


def test_incomplete_pod_inventory_is_marked_partial(monkeypatch):
    monkeypatch.setattr(KubernetesClient, "MAX_PODS", 1)
    api, _ = client(pods=[pod(), pod("second")])
    with api:
        report = api.post("/diagnose", json={"question": QUESTION}).json()["observation"]
    assert report["sources"]["kubernetes_pods"]["status"] == "partial"
    assert report["data_status"] == "partial"
    assert len(report["current_pods"]) == 1
