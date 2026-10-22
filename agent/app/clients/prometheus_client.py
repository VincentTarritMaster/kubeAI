import asyncio
from dataclasses import dataclass
import json
import math
import re

import httpx

from app.clients.base import UpstreamError, get_json
from app.config import Settings


@dataclass
class MetricSample:
    labels: dict[str, str]
    value: float


@dataclass
class MetricsSnapshot:
    cpu: list[MetricSample]
    memory: list[MetricSample]
    warnings: list[str]


@dataclass
class ClusterUsage:
    cpu_cores: float | None
    memory_working_set_bytes: int | None
    warnings: list[str]
    cpu_query: str | None = None
    memory_query: str | None = None


@dataclass(frozen=True)
class HistoricalPoint:
    timestamp: float
    metric: str
    value: float
    labels: dict[str, str]


@dataclass
class HistoricalMetrics:
    points: list[HistoricalPoint]
    warnings: list[str]
    truncated: bool = False
    partial: bool = False


class PrometheusClient:
    MAX_RANGE_SECONDS = 6 * 3600
    MIN_STEP_SECONDS = 30
    MAX_SERIES = 20
    MAX_POINTS = 400
    MAX_INCIDENT_SIGNAL_POINTS = 1500
    def __init__(self, http: httpx.AsyncClient, settings: Settings):
        self.http = http
        self.settings = settings

    async def _query(self, query: str, timestamp: float):
        # Chemin relatif : conserver le préfixe Mimir déjà présent dans la base URL.
        body = await get_json(self.http, "mimir", "api/v1/query", params={
            "query": query, "time": timestamp,
            "timeout": f"{max(1, int(self.settings.request_timeout * 1000))}ms",
        })
        data = body.get("data", {})
        if (body.get("status") != "success" or not isinstance(data, dict)
                or data.get("resultType") != "vector" or not isinstance(data.get("result"), list)):
            raise UpstreamError("mimir", "Invalid Mimir result")
        samples = []
        warnings = [str(message) for message in body.get("warnings", [])]
        try:
            for result in data["result"]:
                value = float(result["value"][1])
                if not math.isfinite(value) or value < 0:
                    warnings.append("An unusable Mimir sample was ignored")
                    continue
                samples.append(MetricSample(labels=result["metric"], value=value))
        except (KeyError, TypeError, ValueError) as error:
            raise UpstreamError("mimir", "Invalid Mimir sample") from error
        return samples, warnings

    async def reachable(self, timestamp: float) -> str:
        """Cheap availability probe used by the capabilities report."""
        try:
            await self._query("vector(1)", timestamp)
            return "available"
        except UpstreamError:
            return "unavailable"

    async def job_status(self, job: str, timestamp: float) -> str:
        """Report a known scrape job without exposing an arbitrary PromQL API."""
        if job not in {"kube-state-metrics", "kubernetes-cadvisor", "kubernetes-pods-annotated"}:
            raise ValueError("Unsupported scrape job")
        try:
            samples, _ = await self._query(f"min(up{{job={json.dumps(job)}}})", timestamp)
        except UpstreamError:
            return "unknown"
        if not samples:
            return "unavailable"
        return "available" if samples[0].value == 1 else "degraded"

    async def metric_present(self, metric: str, timestamp: float) -> bool:
        """Check one allowlisted optional metric family over the last hour."""
        if metric != "http_requests_total":
            raise ValueError("Unsupported metric")
        try:
            samples, _ = await self._query(f"count(last_over_time({metric}[1h]))", timestamp)
        except UpstreamError:
            return False
        return bool(samples and samples[0].value > 0)

    async def snapshot(self, timestamp: float):
        selector = f'job="kubernetes-cadvisor",namespace={json.dumps(self.settings.namespace)},container!="",container!="POD"'
        cpu = f"container_cpu_usage_seconds_total{{{selector}}}"
        memory = f"container_memory_working_set_bytes{{{selector}}}"
        age = self.settings.metrics_max_age
        # Le timestamp de la réponse instantanée est l'heure d'évaluation, pas
        # celle du scrape : filtrer les échantillons à l'intérieur de PromQL.
        cpu_query = (
            f"sum by (namespace, pod, container, id) "
            f"(rate({cpu}[2m]) and (time() - timestamp({cpu}) < {age}))"
        )
        memory_query = (
            f"sum by (namespace, pod, container, id) "
            f"({memory} and (time() - timestamp({memory}) < {age}))"
        )
        results = await asyncio.gather(
            self._query(cpu_query, timestamp), self._query(memory_query, timestamp),
        )
        return MetricsSnapshot(results[0][0], results[1][0], results[0][1] + results[1][1])

    async def cluster_usage(self, timestamp: float) -> ClusterUsage:
        """Observed pod-container usage across namespaces, not node capacity."""
        selector = 'job="kubernetes-cadvisor",namespace!="",pod!="",container!="",container!="POD"'
        cpu = f"container_cpu_usage_seconds_total{{{selector}}}"
        memory = f"container_memory_working_set_bytes{{{selector}}}"
        age = self.settings.metrics_max_age
        queries = (
            f"sum(rate({cpu}[2m]) and (time() - timestamp({cpu}) < {age}))",
            f"sum({memory} and (time() - timestamp({memory}) < {age}))",
        )
        results = await asyncio.gather(*(self._query(query, timestamp) for query in queries))
        cpu_samples, memory_samples = results[0][0], results[1][0]
        warnings = results[0][1] + results[1][1]
        if len(cpu_samples) > 1 or len(memory_samples) > 1:
            raise UpstreamError("mimir", "Invalid aggregate Mimir result")
        if not cpu_samples or not memory_samples:
            warnings.append("Cluster CPU or memory samples are missing")
        return ClusterUsage(
            cpu_cores=cpu_samples[0].value if cpu_samples else None,
            memory_working_set_bytes=round(memory_samples[0].value) if memory_samples else None,
            warnings=warnings,
            cpu_query=queries[0], memory_query=queries[1],
        )

    async def history(self, start: float, end: float, namespace: str | None = None,
                      pod: str | None = None, *, include_correlation: bool = False) -> HistoricalMetrics:
        if not start < end or end - start > self.MAX_RANGE_SECONDS:
            raise ValueError("Historical Mimir window must be positive and at most six hours")
        selector = ['job="kubernetes-cadvisor"', 'container!=""', 'container!="POD"']
        selector.append('namespace=' + json.dumps(namespace) if namespace else 'namespace!=""')
        selector.append('pod=' + json.dumps(pod) if pod else 'pod!=""')
        labels = ",".join(selector)
        cpu = f'container_cpu_usage_seconds_total{{{labels}}}'
        memory = f'container_memory_working_set_bytes{{{labels}}}'
        queries = {
            "cpu_cores": f'sum by (namespace,pod) (rate({cpu}[2m]))',
            "memory_working_set_bytes": f'sum by (namespace,pod) ({memory})',
        }
        if include_correlation:
            target = [f'namespace={json.dumps(namespace)}', f'pod={json.dumps(pod)}']
            target_selector = ",".join(target)
            queries.update({
                # Application process CPU is distinct from container CPU usage.
                "app_cpu_cores": f'sum by (namespace,pod) (rate(process_cpu_seconds_total{{{target_selector}}}[1m]))',
                "restart_count": f'sum by (namespace,pod) (kube_pod_container_status_restarts_total{{{target_selector}}})',
                "cpu_limit_cores": f'sum by (namespace,pod) (kube_pod_container_resource_limits{{{target_selector},resource="cpu",unit="core"}})',
                "memory_limit_bytes": f'sum by (namespace,pod) (kube_pod_container_resource_limits{{{target_selector},resource="memory",unit="byte"}})',
                # Optional application HTTP instrumentation. The series are
                # absent for applications that do not expose them; the caller
                # reports that absence instead of inventing a latency.
                "http_error_ratio": (
                    f'sum by (namespace,pod) (rate(http_requests_total{{{target_selector},status=~"5.."}}[1m]))'
                    f' / clamp_min(sum by (namespace,pod) (rate(http_requests_total{{{target_selector}}}[1m])), 0.001)'
                ),
                "http_latency_p95_seconds": (
                    'histogram_quantile(0.95, sum by (le,namespace,pod) '
                    f'(rate(http_request_duration_seconds_bucket{{{target_selector}}}[1m])))'
                ),
            })
        step = max(self.MIN_STEP_SECONDS,
                   math.ceil((end - start) / max(1, self.MAX_POINTS // len(queries) - 1)))
        points: list[HistoricalPoint] = []
        warnings: list[str] = []
        truncated = False
        partial = False
        successes = 0
        for kind, query in queries.items():
            try:
                body = await get_json(self.http, "mimir", "api/v1/query_range", params={
                    "query": query, "start": start, "end": end, "step": step,
                    "timeout": f"{max(1, int(self.settings.request_timeout * 1000))}ms",
                })
            except UpstreamError as error:
                partial = True
                warnings.append(f"{kind}: {error.message}")
                continue
            successes += 1
            data = body.get("data")
            if (body.get("status") != "success" or not isinstance(data, dict)
                    or data.get("resultType") != "matrix" or not isinstance(data.get("result"), list)):
                raise UpstreamError("mimir", "Invalid Mimir range result")
            raw_warnings = body.get("warnings")
            if isinstance(raw_warnings, list):
                warnings.extend(str(item)[:240] for item in raw_warnings[:8])
            series = data["result"]
            query_points = 0
            if len(series) > self.MAX_SERIES:
                truncated = True
            for item in series[:self.MAX_SERIES]:
                try:
                    labels = item["metric"]
                    values = item["values"]
                    if not isinstance(labels, dict) or not isinstance(values, list):
                        raise TypeError
                    if (namespace and labels.get("namespace") != namespace) or (pod and labels.get("pod") != pod):
                        warnings.append("Mimir series outside requested resource ignored")
                        continue
                    for raw_stamp, raw_value in values:
                        stamp, value = float(raw_stamp), float(raw_value)
                        if not math.isfinite(stamp) or not math.isfinite(value) or value < 0:
                            warnings.append("Unusable Mimir sample ignored")
                            continue
                        if not start <= stamp <= end:
                            warnings.append("Mimir sample outside requested window ignored")
                            continue
                        if query_points >= self.MAX_POINTS // len(queries):
                            truncated = True
                            break
                        points.append(HistoricalPoint(stamp, kind, value, labels))
                        query_points += 1
                except (KeyError, TypeError, ValueError) as error:
                    raise UpstreamError("mimir", "Invalid Mimir range sample") from error
        if successes == 0:
            raise UpstreamError("mimir", "; ".join(warnings))
        if truncated:
            warnings.append("Mimir series or sample limit reached")
        return HistoricalMetrics(points, warnings, truncated, partial)

    async def incident_signals(self, start: float, end: float, namespace: str,
                               pod: str) -> HistoricalMetrics:
        """Read the annotated demo process CPU at scrape cadence for discovery.

        The broad history method intentionally has a small response budget and
        can step over a short incident in a six-hour scan. This focused query is
        one series for one validated resource, sampled every 30 seconds, so B/C
        can still locate a real CPU signal when Loki has expired its logs.
        """
        if (not math.isfinite(start) or not math.isfinite(end)
                or not start < end or end - start > self.MAX_RANGE_SECONDS):
            raise ValueError("Incident signal window must be positive and at most six hours")
        valid = r"[a-z0-9](?:[-a-z0-9]*[a-z0-9])?"
        if (not namespace or len(namespace) > 63 or not re.fullmatch(valid, namespace)
                or not pod or len(pod) > 253 or not re.fullmatch(valid, pod)):
            raise ValueError("Invalid resource for incident signal query")
        selector = f'namespace={json.dumps(namespace)},pod={json.dumps(pod)}'
        query = f"sum by (namespace,pod) (rate(process_cpu_seconds_total{{{selector}}}[1m]))"
        try:
            body = await get_json(self.http, "mimir", "api/v1/query_range", params={
                "query": query, "start": start, "end": end, "step": self.MIN_STEP_SECONDS,
                "timeout": f"{max(1, int(self.settings.request_timeout * 1000))}ms",
            })
        except UpstreamError:
            raise
        data = body.get("data")
        if (body.get("status") != "success" or not isinstance(data, dict)
                or data.get("resultType") != "matrix" or not isinstance(data.get("result"), list)):
            raise UpstreamError("mimir", "Invalid incident signal result")
        warnings = [str(item)[:240] for item in body.get("warnings", [])[:8]] if isinstance(body.get("warnings"), list) else []
        points = []
        truncated = False
        try:
            series = data["result"]
            if len(series) > self.MAX_SERIES:
                truncated = True
                warnings.append("Mimir incident signal series limit reached")
            for item in series[:self.MAX_SERIES]:
                labels, values = item["metric"], item["values"]
                if (not isinstance(labels, dict) or labels.get("namespace") != namespace
                        or labels.get("pod") != pod or not isinstance(values, list)):
                    warnings.append("Mimir incident signal outside requested resource ignored")
                    continue
                for raw_stamp, raw_value in values:
                    moment, value = float(raw_stamp), float(raw_value)
                    if not math.isfinite(moment) or not math.isfinite(value) or value < 0:
                        warnings.append("Unusable incident signal sample ignored")
                        continue
                    if not start <= moment <= end:
                        warnings.append("Incident signal outside requested window ignored")
                        continue
                    if len(points) >= self.MAX_INCIDENT_SIGNAL_POINTS:
                        truncated = True
                        break
                    points.append(HistoricalPoint(moment, "app_cpu_cores", value, labels))
                if truncated and len(points) >= self.MAX_INCIDENT_SIGNAL_POINTS:
                    break
        except (KeyError, TypeError, ValueError) as error:
            raise UpstreamError("mimir", "Invalid incident signal sample") from error
        if truncated:
            warnings.append("Mimir incident signal point limit reached")
        return HistoricalMetrics(points, warnings, truncated, False)
