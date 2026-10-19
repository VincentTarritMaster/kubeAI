"""Simulated, evidence-only correlation without external HTTP."""

from datetime import datetime, timezone
import importlib.util
import json
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from app.clients.prometheus_client import HistoricalMetrics, HistoricalPoint
from app.config import Settings
from app.main import create_app
from app.services.correlation import assess

FIRST = datetime(2026, 10, 19, 12, 0, tzinfo=timezone.utc).timestamp()
QUESTION = ("Pourquoi mon application NestJS est-elle lente "
            "début=2026-10-19T14:00:00+02:00 fin=2026-10-19T14:30:00+02:00 "
            "namespace=kubeai pod=kubeai-demo-correlation ?")
RESOURCE = "kubeai/kubeai-demo-correlation"


def point(kind, second, value):
    return HistoricalPoint(FIRST + second, kind, value,
                           {"namespace": "kubeai", "pod": "kubeai-demo-correlation"})


def entry(source, kind, second, observation, reference):
    return {"timestamp": datetime.fromtimestamp(FIRST + second, tz=timezone.utc).isoformat().replace("+00:00", "Z"),
            "source": source, "type": kind, "resource": RESOURCE,
            "observation": observation, "reference": reference}


def report(points=(), logs=(), events=(), *, source_status=None):
    timeline = [entry("Mimir", "métrique", p.timestamp - FIRST, f"Échantillon {p.value}",
                      f"query_range {p.metric} {RESOURCE} @ " +
                      datetime.fromtimestamp(p.timestamp, tz=timezone.utc).isoformat().replace("+00:00", "Z"))
                for p in points]
    timeline += list(logs) + list(events)
    return {"scope": "incident", "start": "2026-10-19T12:00:00Z", "end": "2026-10-19T12:30:00Z",
            "timezone": "+02:00", "namespace": "kubeai", "pod": "kubeai-demo-correlation",
            "timeline": sorted(timeline, key=lambda item: item["timestamp"]),
            "current_pods": [], "sources": source_status or {}, "missing": [], "warnings": [],
            "data_status": "complete"}


def assess_points(points=(), logs=(), events=()):
    return assess(report(points, logs, events), HistoricalMetrics(list(points), []))


def test_memory_oom_requires_restart_increase_and_measured_limit():
    points = [point("restart_count", 60, 0), point("restart_count", 240, 1),
              point("memory_working_set_bytes", 170, 31_000_000),
              point("memory_limit_bytes", 170, 32_000_000)]
    events = [entry("Kubernetes", "événement", 180, "Warning / OOMKilled", "Event kubeai / oom")]
    result = assess_points(points, events=events)
    hypothesis = next(h for h in result["hypotheses"] if h["id"] == "memory_oom")
    assert hypothesis["confidence"].startswith("étayée")
    assert len(hypothesis["supporting_observations"]) == 5
    assert hypothesis["resources"] == [RESOURCE]
    assert hypothesis["interval"]["start"] == "2026-10-19T12:00:00Z"
    assert hypothesis["alternatives"] and hypothesis["recommended_checks"]
    assert "causalité non prouvée" in hypothesis["confidence"]


def test_oom_with_low_memory_is_contradictory_not_memory_cause():
    points = [point("restart_count", 60, 0), point("restart_count", 240, 1),
              point("memory_working_set_bytes", 170, 1_000_000), point("memory_limit_bytes", 170, 32_000_000)]
    events = [entry("Kubernetes", "événement", 180, "OOMKilled", "Event oom")]
    result = assess_points(points, events=events)
    assert not any(h["id"] == "memory_oom" for h in result["hypotheses"])
    assert "cause indéterminée" == result["conclusion"]
    assert any("contredisent" in text for text in result["correlation_limits"])


def test_cpu_contention_requires_limit_and_real_latency_samples():
    points = [point("cpu_cores", 150, 0.18), point("cpu_limit_cores", 150, 0.2),
              point("http_latency_p95_seconds", 30, 0.15),
              point("http_latency_p95_seconds", 160, 0.7)]
    result = assess_points(points)
    item = next(h for h in result["hypotheses"] if h["id"] == "cpu_latency")
    assert "latence HTTP réellement mesurée" in item["description"]
    assert len(item["supporting_observations"]) == 4
    assert "Attente d'une dépendance" in item["alternatives"]


def test_high_cpu_without_latency_or_limit_cannot_explain_nestjs_slowness():
    result = assess_points([point("cpu_cores", 30, 0.9)])
    assert result["conclusion"] == "cause indéterminée"
    assert any("latence HTTP NestJS" in text for text in result["correlation_limits"])
    assert any("aucune saturation CPU" in text for text in result["correlation_limits"])


def test_connection_error_log_alone_has_insufficient_evidence():
    logs = [entry("Loki", "log", 30, "app : connection refused", "Loki line-1")]
    result = assess_points(logs=logs)
    item = next(h for h in result["hypotheses"] if h["id"] == "connectivity")
    assert item["confidence"] == "preuves insuffisantes"
    assert item["contradictions"] and item["alternatives"]


def test_network_event_needs_same_window_and_resource():
    logs = [entry("Loki", "log", 30, "app : connection timed out", "Loki line-1")]
    close = [entry("Kubernetes", "événement", 70, "NetworkUnavailable", "Event network")]
    far = [entry("Kubernetes", "événement", 700, "NetworkUnavailable", "Event old")]
    assert assess_points(logs=logs, events=close)["hypotheses"][0]["confidence"].startswith("étayée")
    assert assess_points(logs=logs, events=far)["hypotheses"][0]["confidence"] == "preuves insuffisantes"


def test_demo_marker_and_cpu_rise_are_a_limited_hypothesis():
    points = [point("cpu_cores", 10, 0.01), point("cpu_cores", 80, 0.17)]
    logs = [entry("Loki", "log", 40, "load : KUBEAI_DEMO_CPU_LOAD start", "Loki marker")]
    result = assess_points(points, logs=logs)
    item = next(h for h in result["hypotheses"] if h["id"] == "demo_cpu_activity")
    assert "limitée" in item["confidence"] and len(item["supporting_observations"]) == 3
    assert "charge de démonstration" in item["description"]


def test_app_process_cpu_correlates_with_incident_markers_without_claiming_container_saturation():
    points = [point("app_cpu_cores", 10, 0.002), point("app_cpu_cores", 80, 0.08)]
    logs = [entry("Loki", "log", 40, "KUBEAI_DEMO_INCIDENT phase=anomaly_start", "Loki start"),
            entry("Loki", "log", 150, "KUBEAI_DEMO_INCIDENT phase=recovered", "Loki recovered")]
    result = assess_points(points, logs=logs)
    hypothesis = next(h for h in result["hypotheses"] if h["id"] == "demo_app_cpu")
    assert hypothesis["confidence"].startswith("limitée")
    assert len(hypothesis["supporting_observations"]) == 4
    assert any("CPU global du conteneur" in text for text in result["correlation_limits"])
    assert "causalité non prouvée" in hypothesis["confidence"]


def test_app_cpu_absence_is_explicit_and_does_not_create_hypothesis():
    logs = [entry("Loki", "log", 40, "KUBEAI_DEMO_INCIDENT phase=anomaly_start", "Loki start")]
    result = assess_points(logs=logs)
    assert not any(h["id"] == "demo_app_cpu" for h in result["hypotheses"])
    assert result["conclusion"] == "cause indéterminée"
    assert any("série process_cpu_seconds_total absente" in text for text in result["correlation_limits"])


def test_app_cpu_without_pre_anomaly_baseline_is_not_correlated():
    points = [point("app_cpu_cores", 80, 0.08)]
    logs = [entry("Loki", "log", 40, "KUBEAI_DEMO_INCIDENT phase=anomaly_start", "Loki start")]
    result = assess_points(points, logs=logs)
    assert not any(h["id"] == "demo_app_cpu" for h in result["hypotheses"])
    assert result["conclusion"] == "cause indéterminée"


def test_temporally_distant_marker_does_not_support_cpu_hypothesis():
    points = [point("cpu_cores", 10, 0.01), point("cpu_cores", 800, 0.17)]
    logs = [entry("Loki", "log", 40, "KUBEAI_DEMO_CPU_LOAD start", "Loki marker")]
    assert assess_points(points, logs=logs)["conclusion"] == "cause indéterminée"


def test_no_data_is_indeterminate():
    result = assess_points()
    assert result["hypotheses"] == [] and result["conclusion"] == "cause indéterminée"
    assert result["data_status"] == "partial"


def make_client(*, kube_code=200, mimir_code=200, loki_code=200, key="", llm_code=200,
                llm_text='{"priority":"demo_cpu_activity"}', missing_cpu=False):
    calls = {"kubernetes": [], "mimir": [], "loki": [], "llm": []}

    def kube(request):
        calls["kubernetes"].append(request.url.path)
        if kube_code != 200:
            return httpx.Response(kube_code)
        items = {"/api/v1/nodes": [], "/api/v1/events": [], "/api/v1/pods": [
            {"metadata": {"name": "kubeai-demo-correlation", "namespace": "kubeai"},
             "status": {"phase": "Running", "containerStatuses": [{"name": "load", "restartCount": 0}]}}]}
        assert request.url.path in items
        return httpx.Response(200, json={"metadata": {}, "items": items[request.url.path]})

    def mimir(request):
        calls["mimir"].append(request)
        assert request.url.path == "/prometheus/api/v1/query_range"
        assert float(request.url.params["start"]) == FIRST
        assert float(request.url.params["end"]) == FIRST + 1800
        if mimir_code != 200:
            return httpx.Response(mimir_code)
        query = request.url.params["query"]
        if "process_cpu_seconds_total" in query:
            values = [] if missing_cpu else [[FIRST + 10, "0.002"], [FIRST + 80, "0.17"]]
        elif "container_cpu_usage_seconds_total" in query:
            values = [] if missing_cpu else [[FIRST + 10, "0.01"], [FIRST + 80, "0.17"]]
        elif "container_memory_working_set_bytes" in query:
            values = [[FIRST + 80, "10485760"]]
        elif 'resource="cpu"' in query:
            values = [[FIRST + 80, "0.2"]]
        elif 'resource="memory"' in query:
            values = [[FIRST + 80, "33554432"]]
        else:
            values = []
        rows = [{"metric": {"namespace": "kubeai", "pod": "kubeai-demo-correlation"},
                 "values": values}] if values else []
        return httpx.Response(200, json={"status": "success", "data": {"resultType": "matrix", "result": rows}})

    def loki(request):
        calls["loki"].append(request)
        assert request.url.params["query"] == '{namespace="kubeai",pod="kubeai-demo-correlation"}'
        if loki_code != 200:
            return httpx.Response(loki_code)
        return httpx.Response(200, json={"status": "success", "data": {"resultType": "streams", "result": [
            {"stream": {"namespace": "kubeai", "pod": "kubeai-demo-correlation", "container": "load"},
             "values": [[str(int((FIRST + 40) * 10**9)), "KUBEAI_DEMO_CPU_LOAD start: synthetic workload"]]}]}})

    def llm(request):
        calls["llm"].append(request)
        assert request.url.path == "/v1/chat/completions"
        assert "KUBEAI_DEMO_CPU_LOAD" not in request.content.decode()
        return httpx.Response(llm_code, json={"choices": [{"message": {"content": llm_text}}]})

    app = create_app(Settings(ca_file="", token_file="", llm_api_key_file="", llm_api_key=key),
                     kubernetes_transport=httpx.MockTransport(kube),
                     prometheus_transport=httpx.MockTransport(mimir),
                     loki_transport=httpx.MockTransport(loki), llm_transport=httpx.MockTransport(llm))
    return TestClient(app), calls


def ask(api, question=QUESTION, *, stream=False):
    return api.post("/v1/chat/completions", json={"model": "kubeai", "stream": stream,
                                                   "messages": [{"role": "user", "content": question}]})


def answer(response):
    assert response.status_code == 200
    return response.json()["choices"][0]["message"]["content"]


def stream_text(response):
    assert response.status_code == 200
    frames = [line[6:] for line in response.text.splitlines() if line.startswith("data: ")]
    assert frames[-1] == "[DONE]"
    chunks = [json.loads(frame) for frame in frames[:-1]]
    assert chunks[-1]["choices"][0]["finish_reason"] == "stop"
    return "".join(chunk["choices"][0]["delta"].get("content", "") for chunk in chunks)


def test_json_sse_diagnose_and_no_paid_call_without_key():
    api, calls = make_client()
    with api:
        text = answer(ask(api))
        streamed = stream_text(ask(api, stream=True))
        diagnosed = api.post("/diagnose", json={"question": QUESTION})
    assert "demo_cpu_activity" in text and "demo_cpu_activity" in streamed
    assert diagnosed.json()["observation"]["scope"] == "correlation"
    assert diagnosed.json()["observation"]["hypotheses"][0]["id"] == "demo_cpu_activity"
    assert len(calls["mimir"]) == 18 and len(calls["loki"]) == 3 and calls["llm"] == []
    queries = [call.url.params["query"] for call in calls["mimir"]]
    assert any("kube_pod_container_status_restarts_total" in query for query in queries)
    assert any("rate(process_cpu_seconds_total" in call.url.params["query"]
               and "[1m]" in call.url.params["query"] for call in calls["mimir"])
    assert sum("kube_pod_container_resource_limits" in query for query in queries) == 6


@pytest.mark.parametrize("source,option", [("loki", {"loki_code": 503}),
                                           ("mimir", {"mimir_code": 503}),
                                           ("kubernetes_events", {"kube_code": 403})])
def test_unavailable_sources_are_explicit(source, option):
    api, _ = make_client(**option)
    with api:
        observation = api.post("/diagnose", json={"question": QUESTION}).json()["observation"]
    assert observation["sources"][source]["status"] == "unavailable"
    assert observation["data_status"] == "partial"


def test_ambiguous_window_or_resource_never_queries_sources():
    for question in ("Pourquoi mon application NestJS est-elle lente entre 14 h et 14 h 30 ?",
                     "Pourquoi est-elle lente début=2026-10-19T14:00:00+02:00 fin=2026-10-19T14:30:00+02:00 ?"):
        api, calls = make_client()
        with api:
            observation = api.post("/diagnose", json={"question": question}).json()["observation"]
        assert observation["status"] == "clarification_required"
        assert not any(calls.values())


def test_missing_metrics_does_not_claim_slowness_cause():
    api, _ = make_client(missing_cpu=True)
    with api:
        text = answer(ask(api))
    assert "cause indéterminée" in text
    assert "latence HTTP NestJS" in text


def test_mistral_failure_and_invented_text_never_replace_computed_facts():
    for options in ({"key": "test-key", "llm_code": 503},
                    {"key": "test-key", "llm_text": "La base NestJS est détruite."}):
        api, calls = make_client(**options)
        with api:
            text = answer(ask(api))
        assert "demo_cpu_activity" in text and "base NestJS est détruite" not in text
        assert len(calls["llm"]) == 1


def test_mistral_can_only_prioritize_a_supported_hypothesis():
    api, calls = make_client(key="test-key")
    with api:
        text = answer(ask(api))
    assert "Mistral API a sélectionné la priorité demo_cpu_activity" in text
    assert len(calls["llm"]) == 1


def test_deployable_demo_is_bounded_and_has_no_cluster_credentials():
    manifest = (Path(__file__).resolve().parents[2] / "k8s/demo/correlation-pod.yaml").read_text(encoding="utf-8")
    assert "automountServiceAccountToken: false" in manifest
    assert "runAsNonRoot: true" in manifest and "allowPrivilegeEscalation: false" in manifest
    assert "limits:" in manifest and "activeDeadlineSeconds: 300" in manifest
    assert "kind: ClusterRole" not in manifest and "kind: Secret" not in manifest


def test_incident_demo_exposes_annotated_metrics_and_has_bounded_resources():
    root = Path(__file__).resolve().parents[2] / "k8s/demo/incident"
    pod_manifest = (root / "pod.yaml").read_text(encoding="utf-8")
    app = (root / "app.py").read_text(encoding="utf-8")
    assert 'prometheus.io/scrape: "true"' in pod_manifest
    assert 'prometheus.io/port: "8080"' in pod_manifest
    assert 'prometheus.io/path: "/metrics"' in pod_manifest
    assert "process_cpu_seconds_total" in app and '"/metrics"' in app
    assert 'log_phase("anomaly_start")' in app
    assert 'log_phase("recovered")' in app
    assert "server.shutdown()" in app
    assert "automountServiceAccountToken: false" in pod_manifest
    assert "activeDeadlineSeconds: 300" in pod_manifest
    assert "memory: 64Mi" in pod_manifest and "cpu: 100m" in pod_manifest
    assert "nodes/proxy" not in pod_manifest


def test_incident_application_emits_prometheus_process_cpu_counter():
    path = Path(__file__).resolve().parents[2] / "k8s/demo/incident/app.py"
    spec = importlib.util.spec_from_file_location("kubeai_demo_incident_app", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    output = module.render_metrics()
    assert "# TYPE process_cpu_seconds_total counter" in output
    assert output.rstrip().startswith("# HELP process_cpu_seconds_total")
    assert float(output.splitlines()[-1].split()[-1]) >= 0
