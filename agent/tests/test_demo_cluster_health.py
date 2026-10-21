"""Demonstration A through the existing chat API, without live HTTP calls."""

from datetime import datetime, timedelta, timezone
import json

from fastapi.testclient import TestClient
import httpx
import pytest

from app.clients.kubernetes_client import KubernetesClient
from app.config import Settings
from app.models import ChatMessage, ChatRequest
from app.main import create_app
from app.api.chat import diagnostic_route

QUESTION = "Quel est l'état de santé de mon cluster ?"


def node(name="worker", ready="True"):
    return {"metadata": {"name": name}, "status": {"conditions": [
        {"type": "Ready", "status": ready},
    ]}}


def pod(name="web", namespace="kubeai", *, phase="Running", ready="True",
        restarts=0, reason=None, node_name="worker"):
    state = {"waiting": {"reason": reason}} if reason else {"running": {}}
    return {"metadata": {"name": name, "namespace": namespace, "uid": name + "-uid"},
            "spec": {"nodeName": node_name},
            "status": {"phase": phase, "conditions": [{"type": "Ready", "status": ready}],
                       "containerStatuses": [{"name": "app", "ready": ready == "True",
                                              "restartCount": restarts, "state": state}]}}


def warning(*, minutes_ago=2, reason="FailedScheduling", name="web", namespace="other"):
    stamp = (datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)).isoformat()
    return {"metadata": {"name": "event-1", "namespace": namespace, "creationTimestamp": stamp},
            "type": "Warning", "reason": reason, "message": "Bearer sensitive-value",
            "involvedObject": {"kind": "Pod", "name": name, "namespace": namespace}}


def make_client(*, nodes=None, pods=None, events=None, key="", kube_status=None,
                mimir_status=200, llm_status=200, llm_text='{"focus":"nodes"}',
                mimir_empty=False, observed=None, event_status=None):
    calls = {"kubernetes": [], "mimir": [], "llm": []}
    nodes = [node()] if nodes is None else nodes
    pods = [pod()] if pods is None else pods
    events = [] if events is None else events

    def kubernetes(request):
        calls["kubernetes"].append(request.url.path)
        if kube_status:
            return httpx.Response(kube_status)
        if request.url.path == "/api/v1/events" and event_status:
            return httpx.Response(event_status)
        items = {"/api/v1/nodes": nodes, "/api/v1/pods": pods, "/api/v1/events": events}
        assert request.url.path in items
        return httpx.Response(200, json={"metadata": {}, "items": items[request.url.path]})

    def mimir(request):
        calls["mimir"].append(request.url.path)
        assert request.url.path == "/prometheus/api/v1/query"
        query = request.url.params["query"]
        assert 'namespace!=""' in query and 'pod!=""' in query
        if mimir_status != 200:
            return httpx.Response(mimir_status)
        value = "0.75" if "rate(" in query else "104857600"
        return httpx.Response(200, json={"status": "success", "data": {
            "resultType": "vector", "result": [] if mimir_empty else [
                {"metric": {}, "value": [1, value]}],
        }})

    def llm(request):
        calls["llm"].append(request.url.path)
        assert request.url.path == "/v1/chat/completions"
        sent = json.loads(request.content)
        assert sent["max_tokens"] == 256
        assert "sensitive-value" not in request.content.decode()
        return httpx.Response(llm_status, json={"choices": [{"message": {"content": llm_text}}]})

    def no_loki(request):
        pytest.fail("Loki must not be queried for global health demonstration A")

    app = create_app(Settings(ca_file="", token_file="", llm_api_key_file="", llm_api_key=key,
                              api_key="unit-api-key-0123456789abcdef0123456789"),
                     kubernetes_transport=httpx.MockTransport(kubernetes),
                     prometheus_transport=httpx.MockTransport(mimir),
                     loki_transport=httpx.MockTransport(no_loki),
                     llm_transport=httpx.MockTransport(llm))
    return TestClient(app, headers={"Authorization": "Bearer unit-api-key-0123456789abcdef0123456789"}), calls


def chat(api, *, stream=False):
    return api.post("/v1/chat/completions", json={"model": "kubeai", "stream": stream,
                                               "messages": [{"role": "user", "content": QUESTION}]})


def answer(response):
    assert response.status_code == 200
    return response.json()["choices"][0]["message"]["content"]


def stream_text(response):
    assert response.status_code == 200
    assert "text/event-stream" in response.headers["content-type"]
    chunks = [line[6:] for line in response.text.splitlines() if line.startswith("data: ")]
    assert chunks[-1] == "[DONE]"
    parsed = [json.loads(chunk) for chunk in chunks[:-1]]
    assert parsed[-1]["choices"][0]["finish_reason"] == "stop"
    return "".join(item["choices"][0]["delta"].get("content", "") for item in parsed)


def test_healthy_cluster_uses_all_namespaces_mimir_and_no_paid_call_without_key():
    api, calls = make_client(nodes=[node(), node("second")], pods=[pod(), pod("api", "other")])
    with api:
        text = answer(chat(api))
    assert "**État global : sain selon les contrôles effectués**" in text
    assert "2 Ready, 0 NotReady" in text
    assert "2 Running, 0 en erreur" in text
    assert "0.750 cœur(s)" in text and "100.0 Mio" in text
    assert "Analyse IA avancée :** indisponible" in text
    assert calls["kubernetes"] == ["/api/v1/nodes", "/api/v1/pods", "/api/v1/events"]
    assert len(calls["mimir"]) == 2 and calls["llm"] == []
    assert "namespace `kubeai`" not in text


def test_not_ready_node_degrades_and_mistral_can_select_only_supported_focus():
    api, calls = make_client(nodes=[node(), node("broken", "False")], key="test-key")
    with api:
        text = answer(chat(api))
    assert "**État global : dégradé**" in text
    assert "1 Ready, 1 NotReady" in text
    assert "Nœud `broken` : NotReady" in text
    assert "Priorité de lecture sélectionnée avec Mistral" in text
    assert calls["llm"] == ["/v1/chat/completions"]


def test_all_nodes_not_ready_is_critical_by_project_rule():
    api, _ = make_client(nodes=[node("first", "False"), node("second", "False")])
    with api:
        text = answer(chat(api))
    assert "**État global : critique**" in text
    assert "aucun nœud observé n'est Ready" in text


def test_truncated_node_inventory_cannot_claim_every_node_is_not_ready(monkeypatch):
    monkeypatch.setattr(KubernetesClient, "MAX_NODES", 1)
    api, _ = make_client(nodes=[node("first", "False"), node("second", "False")])
    with api:
        text = answer(chat(api))
    assert "**État global : dégradé**" in text
    assert "nodes : partial" in text
    assert "**État global : critique**" not in text


def test_pending_failed_and_crashloop_pods_are_reported_across_namespaces():
    pods = [pod("pending", "alpha", phase="Pending", ready="False"),
            pod("failed", "beta", phase="Failed", ready="False"),
            pod("loop", "gamma", ready="False", restarts=5, reason="CrashLoopBackOff")]
    api, _ = make_client(pods=pods)
    with api:
        text = answer(chat(api))
    assert "**État global : dégradé**" in text
    for name in ("alpha/pending", "beta/failed", "gamma/loop"):
        assert name in text
    assert "CrashLoopBackOff" in text


def test_historical_cumulative_restarts_alone_do_not_degrade_current_health():
    api, _ = make_client(pods=[pod("restarting", "other", restarts=9)])
    with api:
        text = answer(chat(api))
    assert "**État global : sain selon les contrôles effectués**" in text
    assert "9 redémarrage(s) cumulés" in text
    assert "compteurs cumulés sans horodatage" in text
    assert "ne suffisent pas à établir un incident récent" in text


def test_historical_restarts_plus_recent_warning_degrade_current_health():
    api, _ = make_client(pods=[pod("restarting", "other", restarts=9)], events=[warning()])
    with api:
        text = answer(chat(api))
    assert "**État global : dégradé**" in text
    assert "9 redémarrage(s) cumulés" in text
    assert "Warning FailedScheduling" in text


def test_restart_focus_requires_recent_matching_restart_evidence():
    historical, _ = make_client(pods=[pod("web", "other", restarts=9)],
                                llm_text='{"focus":"restarts"}', key="test-key")
    with historical:
        old_only = answer(chat(historical))
    assert "proposition non étayée écartée" in old_only
    assert "9 redémarrage(s) cumulés" in old_only

    recent, _ = make_client(pods=[pod("web", "other", restarts=9)],
                            events=[warning(reason="BackOff", name="web", namespace="other")],
                            llm_text='{"focus":"restarts"}', key="test-key")
    with recent:
        backed_by_event = answer(chat(recent))
    assert "Priorité de lecture sélectionnée avec Mistral" in backed_by_event
    assert "comparer d'abord les cumuls de redémarrages" in backed_by_event


@pytest.mark.parametrize(("question", "expected"), [
    ("Quel est l'état de santé global du cluster ?", "cluster"),
    ("Que s'est-il passé lors de cet incident ?", "incident"),
    ("Corrèle les logs et métriques de cet incident.", "correlation"),
    ("Corrèle la chronologie de cet incident.", "correlation"),
    ("Résume les observations disponibles.", "chat"),
])
def test_a_b_c_routing_is_explicit_and_global_health_needs_no_dates(question, expected):
    chat_request = ChatRequest(messages=[ChatMessage(role="user", content=question)])
    assert diagnostic_route(chat_request) == expected


def test_mimir_failure_keeps_kubernetes_diagnosis_with_limits():
    api, _ = make_client(mimir_status=503, nodes=[node(), node("broken", "False")])
    with api:
        text = answer(chat(api))
    assert "**État global : dégradé**" in text
    assert "Mimir : indisponible" in text
    assert "CPU et mémoire indisponibles" in text
    assert "0.000 cœur" not in text


@pytest.mark.parametrize("code", [403, 503])
def test_kubernetes_unavailable_or_forbidden_is_indeterminate(code):
    api, _ = make_client(kube_status=code)
    with api:
        text = answer(chat(api))
    assert "**État global : indéterminé**" in text
    assert "Nœuds : données indisponibles" in text
    assert "Pods : données indisponibles" in text
    assert "sain selon" not in text


def test_unavailable_recent_events_do_not_allow_a_healthy_claim():
    api, _ = make_client(event_status=403)
    with api:
        text = answer(chat(api))
    assert "**État global : indéterminé**" in text
    assert "events : unavailable" in text


def test_recent_warning_is_evidence_but_old_warning_is_not():
    api, _ = make_client(events=[warning(), warning(minutes_ago=40, reason="OldEvent")])
    with api:
        text = answer(chat(api))
    assert "**État global : dégradé**" in text
    assert "FailedScheduling" in text
    assert "OldEvent" not in text
    assert "sensitive-value" not in text


def test_partial_and_contradictory_node_pod_data_cannot_be_called_healthy():
    api, _ = make_client(nodes=[node("worker", "False"), node("unknown", "Unknown")],
                         pods=[pod("web", node_name="worker")])
    with api:
        text = answer(chat(api))
    assert "**État global : dégradé**" in text
    assert "contradictoires" in text
    assert "nodes : partial" in text


def test_mistral_failure_returns_deterministic_answer():
    api, calls = make_client(nodes=[node(), node("broken", "False")], key="test-key", llm_status=503)
    with api:
        text = answer(chat(api))
    assert "**État global : dégradé**" in text
    assert "Analyse IA avancée :** indisponible" in text
    assert calls["llm"] == ["/v1/chat/completions"]


def test_untrusted_mistral_prose_is_not_published_as_fact():
    api, _ = make_client(nodes=[node(), node("broken", "False")], key="test-key",
                         llm_text="La base de données est détruite et le CPU est à 99 %.")
    with api:
        text = answer(chat(api))
    assert "**État global : dégradé**" in text
    assert "proposition non étayée écartée" in text
    assert "base de données" not in text and "99 %" not in text


def test_json_sse_and_diagnose_share_the_global_cluster_path():
    api, _ = make_client(nodes=[node(), node("broken", "False")], pods=[pod("api", "other")])
    with api:
        json_text = answer(chat(api))
        sse_text = stream_text(chat(api, stream=True))
        diagnosis = api.post("/diagnose", json={"question": QUESTION})
    assert "**État global : dégradé**" in json_text
    assert "**État global : dégradé**" in sse_text
    assert diagnosis.status_code == 200
    assert diagnosis.json()["observation"]["scope"] == "cluster"
    assert diagnosis.json()["observation"]["pods"]["total"] == 1
