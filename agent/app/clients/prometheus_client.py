import asyncio
from dataclasses import dataclass
import json
import math

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
        )

    async def history(self, start: float, end: float, namespace: str | None = None,
                      pod: str | None = None) -> HistoricalMetrics:
        if not start < end or end - start > self.MAX_RANGE_SECONDS:
            raise ValueError("Historical Mimir window must be positive and at most six hours")
        step = max(self.MIN_STEP_SECONDS, math.ceil((end - start) / 240))
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
