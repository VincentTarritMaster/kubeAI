from datetime import datetime, timedelta, timezone

from app.clients.prometheus_client import ClusterUsage
from app.models import (ClusterContainerStatus, ClusterInventory, ClusterInventorySummary,
                        ClusterPodStatus, KubernetesEvent, NodeStatus, SourceStatus)
from app.services.global_health import ClusterHealthReport, assess
from app.services.incident_timeline import parse_window


def pod(name, phase, health, namespace="applications", ready=None, restarts=0, age=None):
    return ClusterPodStatus(
        name=name, namespace=namespace, uid=name, node="node-a", phase=phase,
        ready=ready, health=health, restarts=restarts,
        containers=[ClusterContainerStatus(
            name="app", kind="app", ready=ready is True, restarts=restarts,
            state="running" if phase == "Running" else "waiting",
        )],
        issues=[], age_seconds=age,
    )


def report(pods, *, nodes=None, events=None, usage=None):
    nodes = nodes or [NodeStatus(name="node-a", ready=True, health="Ready")]
    events = events or []
    inventory = ClusterInventory(
        observed_at=datetime.now(timezone.utc), health="healthy", data_status="complete",
        summary=ClusterInventorySummary(), sources={
            "nodes": SourceStatus(status="ok"), "pods": SourceStatus(status="ok"),
            "events": SourceStatus(status="ok"),
        }, warnings=[], nodes=nodes, pods=pods, events=events,
    )
    return assess(inventory, usage or ClusterUsage(cpu_cores=0.1, memory_working_set_bytes=100,
                                                   warnings=[]))


def test_succeeded_pod_is_not_a_failure():
    result = report([pod("job", "Succeeded", "completed")])
    assert result["health"] == "HEALTHY"
    assert result["pods"]["unhealthy"] == 0
    assert result["pods"]["succeeded"] == 1
    ClusterHealthReport.model_validate(result)


def test_failed_and_pending_pods_degrade_once():
    result = report([pod("failed", "Failed", "unhealthy"), pod("pending", "Pending", "unhealthy")])
    assert result["health"] == "DEGRADED"
    assert result["pods"]["failed"] == 1
    assert result["pods"]["pending"] == 1
    assert len(result["causes"]) == 2


def test_historical_event_does_not_degrade_current_health():
    old = KubernetesEvent(
        name="old", namespace="applications", involved_kind="Pod", involved_name="job",
        type="Warning", reason="FailedScheduling", message="old",
        observed_at=datetime.now(timezone.utc) - timedelta(hours=2),
    )
    result = report([pod("job", "Succeeded", "completed")], events=[old])
    assert result["health"] == "HEALTHY"
    assert result["events"]["active_warnings"] == 0


def test_missing_metrics_are_reported_not_invented():
    result = report([pod("running", "Running", "healthy", ready=True)],
                    usage=ClusterUsage(cpu_cores=None, memory_working_set_bytes=None, warnings=[]))
    assert "Mimir : CPU ou mémoire absent" in result["unavailable"]
    assert result["metrics"]["cpu_cores"] is None


def test_pending_age_is_reported_not_confused_with_running():
    result = report([pod("pending", "Pending", "unhealthy", age=600)])
    assert result["health"] == "DEGRADED"
    assert result["pods"]["oldest_pending_seconds"] == 600
    assert result["pods"]["affected"][0]["age_seconds"] == 600


def test_running_but_not_ready_is_unhealthy():
    result = report([pod("running-not-ready", "Running", "unhealthy", ready=False)])
    assert result["health"] == "DEGRADED"
    assert result["pods"]["unhealthy"] == 1


def test_failed_and_unknown_are_not_hidden():
    result = report([pod("failed", "Failed", "unhealthy"),
                     pod("unknown", "Unknown", "unknown")])
    assert result["pods"]["failed"] == 1
    assert result["pods"]["unknown"] == 1
    assert result["health"] == "DEGRADED"


def test_no_ready_node_is_critical():
    result = report([pod("running", "Running", "healthy", ready=True)],
                    nodes=[NodeStatus(name="node-a", ready=False, health="NotReady")])
    assert result["health"] == "CRITICAL"


def test_one_not_ready_node_among_two_degrades():
    result = report([pod("running", "Running", "healthy", ready=True)],
                    nodes=[NodeStatus(name="node-a", ready=True, health="Ready"),
                           NodeStatus(name="node-b", ready=False, health="NotReady")])
    assert result["health"] == "DEGRADED"


def test_cumulative_restarts_are_context_not_current_failure():
    result = report([pod("running", "Running", "healthy", ready=True, restarts=7)])
    assert result["health"] == "HEALTHY"
    assert result["restarting_total"] == 1
    assert result["restarts"]["counters_are_cumulative"] is True


def test_time_before_date_reformulation_is_parsed():
    parsed, error = parse_window(
        "Raconte-moi ce qui s'est passé sur le pod kubeai-demo-incident dans le namespace "
        "applications entre 09:08 et 09:13 UTC le 29 octobre 2026."
    )
    assert error is None
    assert parsed["start"].isoformat() == "2026-10-29T09:08:00+00:00"
    assert parsed["end"].isoformat() == "2026-10-29T09:13:00+00:00"
    assert parsed["pod"] == "kubeai-demo-incident"
