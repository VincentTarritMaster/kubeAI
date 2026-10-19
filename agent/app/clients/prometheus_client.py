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


class PrometheusClient:
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
