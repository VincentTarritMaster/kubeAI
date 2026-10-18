import asyncio
from datetime import datetime, timezone

from app.clients.base import UpstreamError
from app.clients.kubernetes_client import KubernetesClient
from app.clients.prometheus_client import MetricsSnapshot, PrometheusClient
from app.models import ClusterStatus, ContainerStatus, PodMetrics, PodStatus, SourceStatus, Summary


def pod_status(raw: dict, snapshot: MetricsSnapshot) -> PodStatus:
    metadata, status = raw["metadata"], raw.get("status", {})
    containers = []
    for field, kind in (("containerStatuses", "app"), ("initContainerStatuses", "init")):
        for item in status.get(field, []):
            state = item.get("state", {})
            state_name = next((key for key in ("running", "waiting", "terminated") if key in state), "unknown")
            previous = item.get("lastState", {}).get("terminated", {})
            containers.append(ContainerStatus(
                name=item["name"], container_id=item.get("containerID"),
                kind=kind, ready=item.get("ready", False),
                restarts=item.get("restartCount", 0), state=state_name,
                reason=state.get(state_name, {}).get("reason"),
                last_termination_reason=previous.get("reason"), last_exit_code=previous.get("exitCode"),
            ))
    phase = status.get("phase", "Unknown")
    ready = any(c.get("type") == "Ready" and c.get("status") == "True" for c in status.get("conditions", []))
    if metadata.get("deletionTimestamp"):
        health = "terminating"
    elif phase == "Succeeded":
        health = "completed"
    elif phase == "Running" and ready:
        health = "healthy"
    elif phase in {"Pending", "Running", "Failed"}:
        health = "unhealthy"
    else:
        health = "unknown"
    issues = [f"{c.name}: {c.reason}" for c in containers if c.reason and c.reason != "Completed"]
    if health == "unhealthy" and not issues:
        issues.append(status.get("reason") or f"Pod {phase}, Ready={str(ready).lower()}")

    running = {c.name for c in containers if c.state == "running"}
    running_ids = {c.name: c.container_id.split("://", 1)[-1]
                   for c in containers if c.state == "running" and c.container_id}

    def total(samples):
        values = {}
        for sample in samples:
            labels = sample.labels
            # Un pod recréé avec le même nom ne doit pas hériter des mesures
            # de son prédécesseur. Les chemins systemd remplacent '-' par '_'.
            cgroup = labels.get("id", "").replace("_", "-")
            if (
                labels.get("namespace") == metadata["namespace"]
                and labels.get("pod") == metadata["name"]
                and f'pod{metadata["uid"]}' in cgroup
                and labels.get("container") in running
                and (labels["container"] not in running_ids
                     or running_ids[labels["container"]] in cgroup)
            ):
                name = labels["container"]
                values[name] = values.get(name, 0.0) + sample.value
        return sum(values.values()) if running and running <= values.keys() else None

    cpu, memory = total(snapshot.cpu), total(snapshot.memory)
    if not running:
        metric_state = "not_applicable"
    elif cpu is not None and memory is not None:
        metric_state = "available"
    elif cpu is not None or memory is not None:
        metric_state = "partial"
    else:
        metric_state = "unavailable"
    return PodStatus(
        name=metadata["name"], namespace=metadata["namespace"], uid=metadata["uid"],
        node=raw.get("spec", {}).get("nodeName"), phase=phase, ready=ready, health=health,
        restarts=sum(c.restarts for c in containers), containers=containers, issues=issues,
        metrics=PodMetrics(status=metric_state, cpu_cores=cpu,
                           memory_working_set_bytes=round(memory) if memory is not None else None),
    )


class ClusterStatusService:
    def __init__(self, kubernetes: KubernetesClient, prometheus: PrometheusClient):
        self.kubernetes = kubernetes
        self.prometheus = prometheus

    async def status(self) -> ClusterStatus:
        observed_at = datetime.now(timezone.utc)
        raw_pods, snapshot = await asyncio.gather(
            self.kubernetes.list_pods(), self.prometheus.snapshot(observed_at.timestamp()),
            return_exceptions=True,
        )
        if isinstance(raw_pods, Exception):
            raise raw_pods
        warnings = []
        if isinstance(snapshot, UpstreamError):
            prometheus_status = SourceStatus(status="unavailable", message=snapshot.message)
            warnings.append(snapshot.message)
            snapshot = MetricsSnapshot([], [], [])
        elif isinstance(snapshot, Exception):
            raise snapshot
        else:
            prometheus_status = SourceStatus(status="ok")
        warnings.extend(snapshot.warnings)
        pods = sorted((pod_status(p, snapshot) for p in raw_pods), key=lambda pod: pod.name)
        missing = [p.name for p in pods if p.metrics.status in {"partial", "unavailable"}]
        if missing:
            warnings.append("Missing or incomplete metrics: " + ", ".join(missing))
        if (missing or warnings) and prometheus_status.status == "ok":
            prometheus_status.status = "partial"
            prometheus_status.message = "Some metrics are missing or have warnings"
        summary = Summary(
            total=len(pods), ready=sum(p.ready for p in pods),
            unhealthy=sum(p.health == "unhealthy" for p in pods),
            completed=sum(p.health == "completed" for p in pods),
            terminating=sum(p.health == "terminating" for p in pods),
            restarts=sum(p.restarts for p in pods),
        )
        health = "empty" if not pods else "healthy"
        if summary.unhealthy:
            health = "degraded"
        elif any(p.health == "unknown" for p in pods):
            health = "unknown"
        return ClusterStatus(
            observed_at=observed_at, namespace=self.kubernetes.settings.namespace,
            health=health, data_status="partial" if warnings else "complete", summary=summary,
            sources={"kubernetes": SourceStatus(status="ok"), "prometheus": prometheus_status},
            warnings=warnings, pods=pods,
        )
