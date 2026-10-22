import json
from dataclasses import dataclass
import re
import time

import httpx

from app.clients.base import UpstreamError, get_json
from app.config import Settings


@dataclass
class HistoricalLogs:
    entries: list[dict]
    truncated: bool = False
    outside_window: int = 0


class LokiClient:
    MAX_LINES = 50
    MAX_RANGE_NS = 6 * 3600 * 10**9
    DEMO_LOOKBACK_NS = 30 * 86400 * 10**9
    def __init__(self, http: httpx.AsyncClient, settings: Settings):
        self.http = http
        self.settings = settings

    async def recent_logs(self, pod_names: list[str]):
        # Limiter le contexte et n'interroger que des noms provenant de Kubernetes.
        if not pod_names:
            return []
        import re
        selector = (
            "{namespace=" + json.dumps(self.settings.namespace)
            + ",pod=~" + json.dumps("|".join(re.escape(name) for name in pod_names[:6])) + "}"
        )
        now = time.time_ns()
        response = await get_json(self.http, "loki", "/loki/api/v1/query_range", params={
            "query": selector, "start": str(now - 600 * 10**9), "end": str(now),
            "direction": "backward", "limit": "12",
        })
        data = response.get("data", {})
        if response.get("status") != "success" or not isinstance(data, dict) or data.get("resultType") != "streams":
            raise UpstreamError("loki", "Invalid Loki result")
        try:
            entries = [{
                "pod": stream["stream"].get("pod"), "container": stream["stream"].get("container"),
                "timestamp_ns": value[0], "message": value[1][:400],
            } for stream in data["result"] for value in stream["values"]]
            return sorted(entries, key=lambda item: int(item["timestamp_ns"]), reverse=True)[:12]
        except (KeyError, TypeError, ValueError) as error:
            raise UpstreamError("loki", "Invalid Loki sample") from error

    async def logs_between(self, start_ns: int, end_ns: int, namespace: str,
                           pod: str | None = None) -> HistoricalLogs:
        if not 0 < start_ns < end_ns or end_ns - start_ns > self.MAX_RANGE_NS:
            raise ValueError("Historical Loki window must be positive and at most six hours")
        valid = r"[a-z0-9](?:[-a-z0-9]*[a-z0-9])?"
        if not re.fullmatch(valid, namespace) or len(namespace) > 63:
            raise ValueError("Invalid namespace for Loki selector")
        if pod and (not re.fullmatch(valid, pod) or len(pod) > 253):
            raise ValueError("Invalid pod for Loki selector")
        selector = "{namespace=" + json.dumps(namespace)
        if pod:
            selector += ",pod=" + json.dumps(pod)
        selector += "}"
        response = await get_json(self.http, "loki", "/loki/api/v1/query_range", params={
            # Loki's end is exclusive. Query one nanosecond beyond the user's
            # inclusive bound, then still filter returned samples to end_ns.
            "query": selector, "start": str(start_ns), "end": str(end_ns + 1),
            "direction": "forward", "limit": str(self.MAX_LINES + 1),
        })
        data = response.get("data")
        if (response.get("status") != "success" or not isinstance(data, dict)
                or data.get("resultType") != "streams" or not isinstance(data.get("result"), list)):
            raise UpstreamError("loki", "Invalid Loki range result")
        entries = []
        truncated = False
        outside_window = 0
        try:
            for stream in data["result"]:
                labels = stream["stream"]
                if not isinstance(labels, dict) or labels.get("namespace") != namespace:
                    continue
                if pod and labels.get("pod") != pod:
                    continue
                for raw_stamp, message in stream["values"]:
                    stamp = int(raw_stamp)
                    if start_ns <= stamp <= end_ns and isinstance(message, str):
                        entries.append({"namespace": namespace, "pod": labels.get("pod"),
                                        "container": labels.get("container"),
                                        "timestamp_ns": str(raw_stamp), "message": message[:400]})
                        if len(entries) > self.MAX_LINES:
                            truncated = True
                            break
                    elif not start_ns <= stamp <= end_ns:
                        outside_window += 1
                if truncated:
                    break
        except (KeyError, TypeError, ValueError) as error:
            raise UpstreamError("loki", "Invalid Loki range sample") from error
        entries.sort(key=lambda item: int(item["timestamp_ns"]))
        return HistoricalLogs(entries[:self.MAX_LINES], truncated, outside_window)

    async def latest_demo_episode(self, namespace: str = "kubeai") -> dict | None:
        """Locate a complete real demo cycle from its Loki markers, newest first."""
        if namespace != self.settings.namespace:
            raise ValueError("Demo namespace must match the configured read-only namespace")
        end = time.time_ns()
        selector = "{namespace=" + json.dumps(namespace) + ",pod=\"kubeai-demo-incident\"}"
        body = await get_json(self.http, "loki", "/loki/api/v1/query_range", params={
            "query": selector + ' |= "KUBEAI_DEMO_INCIDENT"',
            "start": str(end - self.DEMO_LOOKBACK_NS), "end": str(end),
            "direction": "backward", "limit": "200",
        })
        data = body.get("data")
        if (body.get("status") != "success" or not isinstance(data, dict)
                or data.get("resultType") != "streams" or not isinstance(data.get("result"), list)):
            raise UpstreamError("loki", "Invalid demo marker search result")
        markers = []
        try:
            for stream in data["result"]:
                labels = stream["stream"]
                if labels.get("namespace") != namespace or labels.get("pod") != "kubeai-demo-incident":
                    continue
                for raw_stamp, message in stream["values"]:
                    found = re.search(r"\bKUBEAI_DEMO_INCIDENT\s+phase=(normal|anomaly_start|recovered|complete)\b", message)
                    if found:
                        moment = int(raw_stamp)
                        if end - self.DEMO_LOOKBACK_NS <= moment <= end:
                            markers.append((moment, found.group(1)))
        except (KeyError, TypeError, ValueError) as error:
            raise UpstreamError("loki", "Invalid demo marker sample") from error
        markers.sort()
        for index in range(len(markers) - 1, -1, -1):
            completed, phase = markers[index]
            if phase != "complete":
                continue
            preceding = [item for item in markers[:index] if completed - 6 * 3600 * 10**9 <= item[0] < completed]
            normals = [item for item in preceding if item[1] == "normal"]
            if not normals:
                continue
            normal = normals[-1]
            cycle = [item for item in preceding if item[0] >= normal[0]] + [(completed, phase)]
            phases = [item[1] for item in cycle]
            if "anomaly_start" in phases and "recovered" in phases and (
                    phases.index("normal") < phases.index("anomaly_start") <
                    phases.index("recovered") < phases.index("complete")):
                return {"start_ns": normal[0], "end_ns": completed,
                        "markers": [{"timestamp_ns": str(moment), "phase": kind} for moment, kind in cycle]}
        return None
