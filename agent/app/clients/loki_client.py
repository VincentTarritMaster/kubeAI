import json
import time

import httpx

from app.clients.base import UpstreamError, get_json
from app.config import Settings


class LokiClient:
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
