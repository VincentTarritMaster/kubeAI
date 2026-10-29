import asyncio
from datetime import datetime, timezone

from app.clients.base import UpstreamError
from app.clients.kubernetes_client import KubernetesClient
from app.clients.prometheus_client import MetricsSnapshot, PrometheusClient
from app.models import (ClusterContainerStatus, ClusterInventory, ClusterInventorySummary, ClusterPodStatus,
                        ClusterStatus, ContainerStatus, KubernetesEvent, NodeStatus,
                        PodMetrics, PodStatus, SourceStatus, Summary)
from app.services.privacy import redact_text


def _dict(value):
    return value if isinstance(value, dict) else {}


def _text(value):
    return value if isinstance(value, str) else None


def _safe_text(value, limit=240):
    text = _text(value)
    return redact_text(text[:4096])[:limit] if text else None


def _count(value):
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def _timestamp(value):
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.astimezone(timezone.utc) if parsed.tzinfo else None
    except ValueError:
        return None


def node_status(raw: dict) -> NodeStatus:
    metadata, status = _dict(raw.get("metadata")), _dict(raw.get("status"))
    conditions = status.get("conditions")
    conditions = conditions if isinstance(conditions, list) else []
    ready_condition = next((item for item in conditions if isinstance(item, dict)
                            and item.get("type") == "Ready"), {})
    state = ready_condition.get("status")
    ready = True if state == "True" else False if state == "False" else None
    return NodeStatus(
        name=_text(metadata.get("name")) or "",
        ready=ready, health="Ready" if ready is True else "NotReady" if ready is False else "Unknown",
        reason=_safe_text(ready_condition.get("reason")),
        message=_safe_text(ready_condition.get("message"), 512),
    )


def cluster_pod_status(raw: dict) -> ClusterPodStatus:
    metadata, status, spec = (_dict(raw.get(field)) for field in ("metadata", "status", "spec"))
    containers = []
    restart_counts = []
    for field, kind in (("containerStatuses", "app"), ("initContainerStatuses", "init")):
        entries = status.get(field)
        for item in entries if isinstance(entries, list) else []:
            if not isinstance(item, dict):
                continue
            state = _dict(item.get("state"))
            state_name = next((key for key in ("running", "waiting", "terminated") if key in state), "unknown")
            current = _dict(state.get(state_name))
            previous = _dict(_dict(item.get("lastState")).get("terminated"))
            restarts = _count(item.get("restartCount"))
            restart_counts.append(restarts)
            containers.append(ClusterContainerStatus(
                name=_text(item.get("name")) or "", container_id=_text(item.get("containerID")),
                kind=kind, ready=item.get("ready") if isinstance(item.get("ready"), bool) else None,
                restarts=restarts,
                state=state_name, reason=_safe_text(current.get("reason")),
                last_termination_reason=_safe_text(previous.get("reason")),
                last_exit_code=previous.get("exitCode") if isinstance(previous.get("exitCode"), int)
                and not isinstance(previous.get("exitCode"), bool) else None,
            ))
    phase = _text(status.get("phase")) or "Unknown"
    conditions = status.get("conditions")
    conditions = conditions if isinstance(conditions, list) else []
    ready_condition = next((item for item in conditions if isinstance(item, dict)
                            and item.get("type") == "Ready"), {})
    state = ready_condition.get("status")
    ready = True if state == "True" else False if state == "False" else None
    if metadata.get("deletionTimestamp"):
        health = "terminating"
    elif phase == "Succeeded":
        health = "completed"
    elif phase == "Running" and ready is True:
        health = "healthy"
    elif phase in {"Pending", "Failed"} or (phase == "Running" and ready is False):
        health = "unhealthy"
    else:
        health = "unknown"
    issues = [f"{item.name}: {item.reason}" for item in containers
              if item.reason and item.reason != "Completed"]
    if health == "unhealthy" and not issues:
        issues.append(_text(status.get("reason")) or f"Pod {phase}, Ready={str(ready).lower()}")
    created = _timestamp(metadata.get("creationTimestamp"))
    age_seconds = max(0, int((datetime.now(timezone.utc) - created).total_seconds())) if created else None
    return ClusterPodStatus(
        name=_text(metadata.get("name")) or "", namespace=_text(metadata.get("namespace")) or "",
        uid=_text(metadata.get("uid")), node=_text(spec.get("nodeName")), phase=phase,
        ready=ready, health=health,
        restarts=sum(restart_counts) if isinstance(status.get("containerStatuses"), list)
                 and all(count is not None for count in restart_counts) else None,
        age_seconds=age_seconds,
        containers=containers, issues=[redact_text(issue)[:240] for issue in issues],
    )


def event_status(raw: dict) -> KubernetesEvent:
    metadata = _dict(raw.get("metadata"))
    involved = _dict(raw.get("involvedObject") or raw.get("regarding"))
    series = _dict(raw.get("series"))
    observed_at = next((parsed for parsed in (
        _timestamp(raw.get("eventTime")), _timestamp(series.get("lastObservedTime")),
        _timestamp(raw.get("lastTimestamp")), _timestamp(raw.get("firstTimestamp")),
        _timestamp(metadata.get("creationTimestamp")),
    ) if parsed is not None), None)
    message = _text(raw.get("message")) or _text(raw.get("note"))
    return KubernetesEvent(
        name=_text(metadata.get("name")) or "",
        namespace=_text(metadata.get("namespace")) or _text(involved.get("namespace")),
        involved_kind=_text(involved.get("kind")), involved_name=_text(involved.get("name")),
        type=_text(raw.get("type")), reason=_safe_text(raw.get("reason")),
        message=_safe_text(message, 1024),
        count=_count(raw.get("count")) or _count(series.get("count")),
        first_observed_at=_timestamp(raw.get("firstTimestamp"))
        or _timestamp(metadata.get("creationTimestamp")), observed_at=observed_at,
    )


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

    async def status(self, namespace: str | None = None) -> ClusterStatus:
        observed_at = datetime.now(timezone.utc)
        collection = (self.kubernetes.list_namespace_pods() if namespace
                      else self.kubernetes.list_all_pods())
        pod_collection, snapshot = await asyncio.gather(
            collection, self.prometheus.snapshot(observed_at.timestamp()),
            return_exceptions=True,
        )
        if isinstance(pod_collection, Exception):
            raise pod_collection
        warnings = []
        if pod_collection.truncated:
            warnings.append("Kubernetes pod inventory reached its collection limit")
        if isinstance(snapshot, UpstreamError):
            mimir_status = SourceStatus(status="unavailable", message=snapshot.message)
            warnings.append(snapshot.message)
            snapshot = MetricsSnapshot([], [], [])
        elif isinstance(snapshot, Exception):
            raise snapshot
        else:
            mimir_status = SourceStatus(status="ok")
        warnings.extend(snapshot.warnings)
        pods = sorted((pod_status(p, snapshot) for p in pod_collection.items), key=lambda pod: pod.name)
        missing = [p.name for p in pods if p.metrics.status in {"partial", "unavailable"}]
        if missing:
            warnings.append("Missing or incomplete metrics: " + ", ".join(missing))
        if (missing or warnings) and mimir_status.status == "ok":
            mimir_status.status = "partial"
            mimir_status.message = "Some metrics are missing or have warnings"
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
        if pod_collection.truncated and health != "degraded":
            health = "unknown"
        return ClusterStatus(
            observed_at=observed_at, scope="namespace" if namespace else "cluster",
            namespace=namespace or "*",
            health=health, data_status="partial" if warnings else "complete", summary=summary,
            sources={"kubernetes": SourceStatus(
                status="partial" if pod_collection.truncated else "ok",
                message="Pod collection limit reached" if pod_collection.truncated else None,
            ), "mimir": mimir_status},
            warnings=warnings, pods=pods,
        )

    async def inventory(self) -> ClusterInventory:
        observed_at = datetime.now(timezone.utc)
        results = await asyncio.gather(
            self.kubernetes.list_nodes(), self.kubernetes.list_all_pods(), self.kubernetes.list_events(),
            return_exceptions=True,
        )
        sources = {}
        warnings = []
        values = {}
        for name, result in zip(("nodes", "pods", "events"), results):
            if isinstance(result, Exception):
                message = result.message if isinstance(result, UpstreamError) else "Unexpected Kubernetes read failure"
                sources[name] = SourceStatus(status="unavailable", message=message)
                warnings.append(f"{name}: {message}")
                values[name] = None
                continue
            values[name] = result.items
            if result.truncated:
                message = f"{name}: Kubernetes collection limit reached"
                sources[name] = SourceStatus(status="partial", message=message)
                warnings.append(message)
            else:
                sources[name] = SourceStatus(status="ok")

        nodes = sorted((node_status(item) for item in values["nodes"]), key=lambda item: item.name) \
            if values["nodes"] is not None else None
        pods = sorted((cluster_pod_status(item) for item in values["pods"]),
                      key=lambda item: (item.namespace, item.name)) if values["pods"] is not None else None
        events = sorted((event_status(item) for item in values["events"]),
                        key=lambda item: (item.observed_at is not None,
                                          item.observed_at or datetime.min.replace(tzinfo=timezone.utc)),
                        reverse=True) if values["events"] is not None else None

        incomplete = {
            "nodes": nodes is not None and any(not node.name or node.ready is None for node in nodes),
            "pods": pods is not None and any(not pod.name or not pod.namespace or pod.restarts is None
                                             or pod.health == "unknown" for pod in pods),
            "events": events is not None and any(event.observed_at is None for event in events),
        }
        for name, missing in incomplete.items():
            if missing:
                message = f"{name}: some Kubernetes fields are unavailable"
                if sources[name].status == "ok":
                    sources[name] = SourceStatus(status="partial", message=message)
                warnings.append(message)

        summary = ClusterInventorySummary(
            nodes=len(nodes) if nodes is not None else None,
            nodes_ready=sum(node.ready is True for node in nodes) if nodes is not None else None,
            nodes_not_ready=sum(node.ready is False for node in nodes) if nodes is not None else None,
            pods=len(pods) if pods is not None else None,
            pods_unhealthy=sum(pod.health == "unhealthy" for pod in pods) if pods is not None else None,
            restarts=(sum(pod.restarts for pod in pods) if pods is not None
                      and all(pod.restarts is not None for pod in pods) else None),
            events=len(events) if events is not None else None,
        )
        if (nodes is not None and any(node.ready is False for node in nodes)) or (
                pods is not None and any(pod.health == "unhealthy" for pod in pods)):
            health = "degraded"
        elif nodes is None or pods is None or not nodes or incomplete["nodes"] or incomplete["pods"] \
                or sources["nodes"].status == "partial" or sources["pods"].status == "partial":
            health = "unknown"
        else:
            health = "healthy"
        data_status = ("unavailable" if all(source.status == "unavailable" for source in sources.values())
                       else "partial" if any(source.status != "ok" for source in sources.values())
                       else "complete")
        return ClusterInventory(
            observed_at=observed_at, health=health, data_status=data_status, summary=summary,
            sources=sources, warnings=warnings, nodes=nodes, pods=pods, events=events,
        )

    async def capabilities(self) -> dict:
        """Report configured sources and optional metric families without calling the LLM."""
        observed_at = datetime.now(timezone.utc)
        timestamp = observed_at.timestamp()
        mimir, ksm, cadvisor, app_http = await asyncio.gather(
            self.prometheus.reachable(timestamp),
            self.prometheus.job_status("kube-state-metrics", timestamp),
            self.prometheus.job_status("kubernetes-cadvisor", timestamp),
            self.prometheus.metric_present("http_requests_total", timestamp),
        )
        settings = self.kubernetes.settings
        if settings.llm_provider == "mistral-api":
            try:
                llm_configured = bool(settings.llm_key())
            except (OSError, UnicodeError):
                llm_configured = False
        else:
            # Local servers may not require a key; this only says the profile is
            # selected, not that the endpoint is reachable.
            llm_configured = True
        return {
            "observed_at": observed_at,
            "service": "kubeai-agent",
            "version": "0.3.0",
            "scope": "cluster",
            "namespace": settings.namespace,
            "read_only": True,
            "sources": {
                "kubernetes": "configured",
                "mimir": mimir,
                "loki": "configured",
            },
            "metrics": {
                "kube_state_metrics": ksm,
                "cadvisor": cadvisor,
                "application_http": "available" if app_http else "unavailable",
            },
            "llm": {
                "provider": settings.llm_provider,
                "model": settings.llm_model,
                "framework": "langchain",
                "configured": llm_configured,
            },
            "limits": {
                "observation_pods": 20,
                "log_lines": 12,
                "log_pods": 6,
                "history_messages": 32,
                "history_characters": 16000,
                "max_completion_tokens": 1024,
            },
        }
