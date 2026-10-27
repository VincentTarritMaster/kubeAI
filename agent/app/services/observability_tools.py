"""Validated, bounded read-only functions exposed to the model through LangChain."""
import asyncio
from datetime import datetime, timedelta, timezone
import json
from types import SimpleNamespace

from fastapi.encoders import jsonable_encoder
from langchain_core.tools import StructuredTool
from langchain_core.utils.function_calling import convert_to_openai_tool
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator
from typing import Literal

from app.clients.base import UpstreamError
from app.services.incident_timeline import _historical_log_signal, collect
from app.services.latest_incident import discover
from app.services.privacy import redact_data


class Arguments(BaseModel):
    model_config = ConfigDict(extra="forbid")


class InventoryArgs(Arguments):
    scope: Literal["cluster", "namespace"] = "cluster"


class TargetArgs(Arguments):
    namespace: str | None = Field(default=None, max_length=63, pattern=r"^[a-z0-9](?:[-a-z0-9]*[a-z0-9])?$",
                                  description="Omit to search all namespaces.")
    pod: str | None = Field(default=None, max_length=253, pattern=r"^[a-z0-9](?:[-a-z0-9]*[a-z0-9])?$",
                            description="Omit unless a real pod name was given by the user or returned by a tool. Never guess.")


class WindowArgs(TargetArgs):
    start: AwareDatetime | None = Field(default=None, description="Inclusive ISO-8601 timestamp with UTC offset.")
    end: AwareDatetime | None = Field(default=None, description="Inclusive ISO-8601 timestamp with UTC offset.")

    @model_validator(mode="after")
    def bounded_window(self):
        if bool(self.start) != bool(self.end):
            raise ValueError("Provide both start and end, or neither")
        if self.start and not 0 < (self.end - self.start).total_seconds() <= 21600:
            raise ValueError("The time window must be positive and at most six hours")
        return self


class EvidenceArgs(WindowArgs):
    namespace: str = Field(max_length=63, pattern=r"^[a-z0-9](?:[-a-z0-9]*[a-z0-9])?$")
    pod: str = Field(max_length=253, pattern=r"^[a-z0-9](?:[-a-z0-9]*[a-z0-9])?$")
    start: AwareDatetime
    end: AwareDatetime


class ResponseArgs(Arguments):
    kind: Literal["answer", "clarification", "out_of_scope"]
    answer: str = Field(min_length=1, max_length=12000, description="Final answer in the language of the user.")


def bounded_result(value, sensitive=(), limit=6500):
    """Keep valid JSON and explicit truncation instead of cutting a JSON string."""
    data = redact_data(jsonable_encoder(value), sensitive)
    if not isinstance(data, dict):
        data = {"data": data}

    def lists(item):
        if isinstance(item, dict):
            for child in item.values():
                yield from lists(child)
        elif isinstance(item, list):
            if len(item) > 1:
                yield item
            for child in item:
                yield from lists(child)

    while len(json.dumps(data, ensure_ascii=False)) > limit:
        candidates = list(lists(data))
        if not candidates:
            return {"error": "result_too_large", "message": "Narrow the target or time window.", "truncated": True}
        largest = max(candidates, key=lambda item: len(json.dumps(item, ensure_ascii=False)))
        # Retain both ends of a history; do not silently discard its recovery.
        retained = max(1, len(largest) // 2)
        indices = sorted({round(index * (len(largest) - 1) / max(1, retained - 1)) for index in range(retained)})
        largest[:] = [largest[index] for index in indices]
        data["truncated"] = True
    return data


class ObservabilityTools:
    def __init__(self, diagnosis):
        self.diagnosis = diagnosis
        self.settings = diagnosis.llm.settings
        definitions = [
            ("get_cluster_inventory", self.inventory, InventoryArgs,
             "Read current nodes, pods, events and measured CPU/memory. scope=namespace reads only the configured namespace."),
            ("find_latest_incident", self.latest, TargetArgs,
             "Discover the latest timestamped application anomaly from retained logs and Kubernetes warnings. Returns pod and exact UTC search bounds; then read get_incident_evidence."),
            ("get_logs", self.logs, WindowArgs,
             "Read up to 50 raw log excerpts for a namespace/pod and time window, including deleted pods. Without dates read the last ten minutes."),
            ("get_metrics", self.metrics, WindowArgs,
             "Read current CPU/memory or raw historical samples. For comparing logs with CPU/HTTP in an incident, get_incident_evidence provides compact extrema and logs together. Missing metrics are unknown."),
            ("get_events", self.events, WindowArgs,
             "Read Kubernetes events still retained for the target. Without dates read the last hour. Events may expire before logs."),
            ("get_incident_evidence", self.evidence, EvidenceArgs,
             "Read logs, metric history and Kubernetes events together for one pod in an exact UTC window. Use the bounds from find_latest_incident or the user."),
            ("respond", self.respond, ResponseArgs,
             "Finish with a concise answer, clarification or scope refusal in the user's language. Monitoring answers require fresh tool evidence first."),
        ]
        self.registry = {name: StructuredTool.from_function(name=name, coroutine=function,
                         args_schema=schema, description=description) for name, function, schema, description in definitions}
        self.schemas = [convert_to_openai_tool(tool) for tool in self.registry.values()]

    def target(self, namespace):
        return namespace or self.settings.namespace

    @staticmethod
    def window(start, end, seconds=600):
        end = end or datetime.now(timezone.utc)
        start = start or end - timedelta(seconds=seconds)
        return start.astimezone(timezone.utc), end.astimezone(timezone.utc)

    async def inventory(self, scope="cluster"):
        if scope == "namespace":
            return (await self.diagnosis.cluster_status.status(self.settings.namespace)).model_dump(mode="json")
        inventory, usage = await asyncio.gather(
            self.diagnosis.cluster_status.inventory(),
            self.diagnosis.cluster_status.prometheus.cluster_usage(datetime.now(timezone.utc).timestamp()),
            return_exceptions=True)
        if isinstance(inventory, Exception):
            raise inventory
        data = inventory.model_dump(mode="json")
        data["metrics"] = ({"error": "metrics_unavailable"} if isinstance(usage, Exception) else jsonable_encoder(usage))
        return data

    async def latest(self, namespace=None, pod=None):
        window, error, _ = await discover(self.diagnosis, {"namespace": namespace, "pod": pod},
                                        _historical_log_signal, SimpleNamespace(selection=None, choices=()))
        if error:
            return {"status": "unresolved", "message": error}
        # Outward rounding keeps all marker lines when a model copies dates
        # with second precision; never lose the final subsecond recovery line.
        start = window["start"].replace(microsecond=0)
        end = window["end"].replace(microsecond=0)
        if window["end"].microsecond:
            end += timedelta(seconds=1)
        return {"status": "found", "namespace": window["namespace"], "pod": window["pod"],
                "start": start, "end": end, "discovery_note": window["search_note"],
                "next_step": "Read get_incident_evidence with these exact resource and time bounds."}

    async def logs(self, namespace=None, pod=None, start=None, end=None):
        start, end = self.window(start, end)
        logs = await self.diagnosis.loki.logs_between(int(start.timestamp() * 10**9), int(end.timestamp() * 10**9),
                                                     namespace, pod)
        return {"namespace": namespace or "*", "pod": pod, "start": start, "end": end,
                "entries": logs.entries, "truncated": logs.truncated, "outside_window": logs.outside_window}

    async def metrics(self, namespace=None, pod=None, start=None, end=None):
        namespace = self.target(namespace)
        if start is None and end is None and pod is None and namespace == self.settings.namespace:
            return jsonable_encoder(await self.diagnosis.cluster_status.prometheus.snapshot(datetime.now(timezone.utc).timestamp()))
        start, end = self.window(start, end, 300)
        return jsonable_encoder(await self.diagnosis.cluster_status.prometheus.history(
            start.timestamp(), end.timestamp(), namespace, pod, include_correlation=True))

    async def events(self, namespace=None, pod=None, start=None, end=None):
        start, end = self.window(start, end, 3600)
        inventory = await self.diagnosis.cluster_status.inventory()
        rows = [event.model_dump(mode="json") for event in inventory.events or []
                if (not namespace or event.namespace == namespace)
                and (not pod or event.involved_name == pod)
                and event.observed_at and start <= event.observed_at <= end]
        return {"namespace": namespace or "*", "pod": pod, "start": start, "end": end,
                "events": rows[:30], "source": inventory.sources["events"].model_dump(),
                "truncated": len(rows) > 30}

    async def evidence(self, namespace, pod, start, end):
        window = {"namespace": namespace, "pod": pod, "start": start, "end": end, "timezone": "UTC"}
        report, _, metrics, _ = await collect(self.diagnosis, window, include_correlation=True)
        groups = {}
        for point in metrics.points if metrics else []:
            groups.setdefault(point.metric, []).append(point)
        ranges = {}
        for kind, points in groups.items():
            low, high = min(points, key=lambda point: point.value), max(points, key=lambda point: point.value)
            ranges[kind] = {"min": {"timestamp": low.timestamp, "value": low.value},
                            "max": {"timestamp": high.timestamp, "value": high.value}, "samples": len(points)}
        return {"namespace": namespace, "pod": pod, "start": start, "end": end,
                "logs": [{"timestamp": entry["timestamp"], "message": entry["observation"], "reference": entry["reference"]}
                         for entry in report["timeline"] if entry["source"] == "Loki"],
                "events": [{"timestamp": entry["timestamp"], "message": entry["observation"], "reference": entry["reference"]}
                           for entry in report["timeline"] if entry["source"] == "Kubernetes"],
                "metrics_ranges": ranges, "sources": report["sources"], "missing": report["missing"],
                "warnings": report["warnings"], "current_pods_not_historical": report["current_pods"]}

    async def respond(self, kind, answer):
        return {"kind": kind, "answer": answer}
