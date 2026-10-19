"""Cluster-wide Kubernetes inventory tests; all responses are simulated."""

from fastapi.testclient import TestClient
import httpx

from app.clients.kubernetes_client import KubernetesClient
from app.config import Settings
from app.main import create_app


def node(name="worker", ready="True"):
    return {"metadata": {"name": name}, "status": {"conditions": [
        {"type": "Ready", "status": ready, "reason": "KubeletReady"},
    ]}}


def pod(name="web", namespace="kubeai", *, ready="True", restarts=0):
    return {
        "metadata": {"name": name, "namespace": namespace, "uid": name + "-uid"},
        "spec": {"nodeName": "worker"},
        "status": {"phase": "Running", "conditions": [{"type": "Ready", "status": ready}],
                   "containerStatuses": [{"name": "app", "ready": ready == "True",
                                          "restartCount": restarts, "state": {"running": {}}}]},
    }


def event(name, timestamp, *, message="Pod scheduled"):
    return {"metadata": {"name": name, "namespace": "other", "creationTimestamp": timestamp},
            "reason": "Scheduled", "message": message, "type": "Normal",
            "involvedObject": {"kind": "Pod", "name": "worker-app", "namespace": "other"}}


def response(items, continuation=""):
    return httpx.Response(200, json={"metadata": {"continue": continuation}, "items": items})


def client(handler):
    app = create_app(Settings(ca_file="", token_file=""), kubernetes_transport=httpx.MockTransport(handler))
    return TestClient(app)


def test_cluster_inventory_reports_nodes_all_namespaces_restarts_and_ordered_events():
    paths = []

    def handler(request):
        paths.append(request.url.path)
        if request.url.path == "/api/v1/nodes":
            return response([node(), node("unready", "False")])
        if request.url.path == "/api/v1/pods":
            return response([pod("web", restarts=2), pod("batch", "other", ready="False", restarts=3)])
        if request.url.path == "/api/v1/events":
            return response([event("older", "2026-10-18T10:00:00Z"),
                             event("newer", "2026-10-19T10:00:00Z")])
        raise AssertionError(request.url.path)

    with client(handler) as api:
        result = api.get("/cluster/inventory")
        schema = api.get("/openapi.json").json()
    assert result.status_code == 200
    body = result.json()
    assert paths == ["/api/v1/nodes", "/api/v1/pods", "/api/v1/events"]
    assert body["scope"] == "cluster"
    assert body["health"] == "degraded"
    assert body["data_status"] == "complete"
    assert body["summary"] == {"nodes": 2, "nodes_ready": 1, "nodes_not_ready": 1,
                               "pods": 2, "pods_unhealthy": 1, "restarts": 5, "events": 2}
    assert [(item["name"], item["health"]) for item in body["nodes"]] == [
        ("unready", "NotReady"), ("worker", "Ready")]
    assert [(item["namespace"], item["name"]) for item in body["pods"]] == [
        ("kubeai", "web"), ("other", "batch")]
    assert [item["name"] for item in body["events"]] == ["newer", "older"]
    assert body["events"][0]["involved_kind"] == "Pod"
    assert schema["paths"]["/cluster/inventory"]["get"]["operationId"] == "get_cluster_inventory"


def test_forbidden_source_is_explicit_and_does_not_hide_other_data():
    def handler(request):
        if request.url.path == "/api/v1/nodes":
            return httpx.Response(403)
        if request.url.path == "/api/v1/pods":
            return response([pod("web", "other")])
        return response([])

    with client(handler) as api:
        body = api.get("/cluster/inventory").json()
    assert body["nodes"] is None
    assert body["summary"]["nodes"] is None
    assert body["pods"][0]["namespace"] == "other"
    assert body["health"] == "unknown"
    assert body["data_status"] == "partial"
    assert body["sources"]["nodes"]["status"] == "unavailable"
    assert "403" in body["sources"]["nodes"]["message"]


def test_all_sources_forbidden_are_unavailable_not_healthy_empty_lists():
    with client(lambda request: httpx.Response(403)) as api:
        body = api.get("/cluster/inventory").json()
    assert body["health"] == "unknown"
    assert body["data_status"] == "unavailable"
    assert body["nodes"] is body["pods"] is body["events"] is None
    assert all(source["status"] == "unavailable" for source in body["sources"].values())


def test_timeout_is_isolated_to_events():
    def handler(request):
        if request.url.path == "/api/v1/events":
            raise httpx.ReadTimeout("simulated timeout")
        return response([node()] if request.url.path == "/api/v1/nodes" else [pod()])

    with client(handler) as api:
        body = api.get("/cluster/inventory").json()
    assert body["health"] == "healthy"
    assert body["data_status"] == "partial"
    assert body["events"] is None
    assert body["sources"]["events"]["status"] == "unavailable"
    assert "timed out" in body["sources"]["events"]["message"]


def test_pagination_follows_continuation_without_dropping_items():
    seen = []

    def handler(request):
        if request.url.path == "/api/v1/pods":
            token = request.url.params.get("continue", "")
            seen.append(token)
            return response([pod("first" if not token else "second")], "next" if not token else "")
        return response([node()] if request.url.path == "/api/v1/nodes" else [])

    with client(handler) as api:
        body = api.get("/cluster/inventory").json()
    assert seen == ["", "next"]
    assert [item["name"] for item in body["pods"]] == ["first", "second"]
    assert body["sources"]["pods"]["status"] == "ok"


def test_collection_limit_marks_data_partial_and_health_unknown(monkeypatch):
    monkeypatch.setattr(KubernetesClient, "MAX_PODS", 2)

    def handler(request):
        if request.url.path == "/api/v1/pods":
            return response([pod("first"), pod("second")], "more")
        return response([node()] if request.url.path == "/api/v1/nodes" else [])

    with client(handler) as api:
        body = api.get("/cluster/inventory").json()
    assert body["summary"]["pods"] == 2
    assert body["health"] == "unknown"
    assert body["data_status"] == "partial"
    assert body["sources"]["pods"]["status"] == "partial"


def test_missing_fields_are_unknown_and_events_without_dates_sort_last():
    def handler(request):
        if request.url.path == "/api/v1/nodes":
            return response([{"metadata": {"name": "worker"}}])
        if request.url.path == "/api/v1/pods":
            return response([{"metadata": {"name": "pending", "namespace": "other"}}])
        return response([{"metadata": {"name": "undated"}, "reason": "Unknown"},
                         event("dated", "2026-10-19T10:00:00Z")])

    with client(handler) as api:
        body = api.get("/cluster/inventory").json()
    assert body["nodes"][0]["ready"] is None
    assert body["pods"][0]["health"] == "unknown"
    assert body["pods"][0]["restarts"] is None
    assert [item["name"] for item in body["events"]] == ["dated", "undated"]
    assert body["health"] == "unknown"
    assert body["data_status"] == "partial"
    assert all(source["status"] == "partial" for source in body["sources"].values())


def test_repeated_pagination_token_rejects_only_affected_source():
    def handler(request):
        if request.url.path == "/api/v1/events":
            return response([event("one", "2026-10-19T10:00:00Z")], "repeat")
        return response([node()] if request.url.path == "/api/v1/nodes" else [])

    with client(handler) as api:
        body = api.get("/cluster/inventory").json()
    assert body["events"] is None
    assert body["sources"]["events"]["status"] == "unavailable"
    assert body["nodes"][0]["name"] == "worker"


def test_event_message_is_redacted_before_response():
    def handler(request):
        if request.url.path == "/api/v1/events":
            return response([event("private", "2026-10-19T10:00:00Z",
                                   message="Bearer example-token-value")])
        return response([node()] if request.url.path == "/api/v1/nodes" else [])

    with client(handler) as api:
        body = api.get("/cluster/inventory").json()
    assert "example-token-value" not in body["events"][0]["message"]
    assert "[MASQUÉ]" in body["events"][0]["message"]
