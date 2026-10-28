"""Bounded audit trail: JSON lines and optional Loki ingestion."""
import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import logging
import time

logger = logging.getLogger("kubeai.audit")
logger.setLevel(logging.INFO)
if not logger.handlers:
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(handler)


def key_id(authorization: str) -> str:
    """Return a stable fingerprint, never the bearer token itself."""
    scheme, separator, token = authorization.partition(" ")
    if not separator or scheme.lower() != "bearer" or not token.strip():
        return "anonymous"
    return "sha256:" + hashlib.sha256(token.strip().encode("utf-8")).hexdigest()[:16]


class AuditLog:
    def __init__(self, settings, loki_http):
        self.settings = settings
        self.loki_http = loki_http

    def emit(self, event: dict):
        if not self.settings.audit_enabled:
            return
        record = {"timestamp": datetime.now(timezone.utc).isoformat(), **event}
        logger.info(json.dumps(record, ensure_ascii=False, separators=(",", ":")))
        if self.loki_http is not None:
            asyncio.create_task(self._push(record))

    async def _push(self, record: dict):
        body = {"streams": [{
            "stream": {
                "job": "kubeai-audit",
                "cluster": self.settings.observed_cluster or "unknown",
                "event": str(record.get("event", "unknown")),
            },
            "values": [[str(time.time_ns()), json.dumps(record, ensure_ascii=False)]],
        }]}
        try:
            await self.loki_http.post(
                self.settings.loki_url.rstrip("/") + "/loki/api/v1/push",
                json=body, timeout=2,
            )
        except Exception:
            # Audit must never break the read-only request path.
            logger.warning("audit Loki push unavailable")


@dataclass
class AuditContext:
    audit: AuditLog
    request_id: str
    key_id: str

    def tool(self, name: str, arguments: dict, result: dict, duration_ms: int):
        result = result if isinstance(result, dict) else {}
        namespace = result.get("namespace") or arguments.get("namespace")
        pod = result.get("pod") or arguments.get("pod")
        status = "error" if "error" in result else str(result.get("status") or "ok")
        self.audit.emit({
            "event": "tool",
            "request_id": self.request_id,
            "key_id": self.key_id,
            "tool": name,
            "namespace": namespace,
            "pod": pod,
            "window_start": arguments.get("start"),
            "window_end": arguments.get("end"),
            "status": status,
            "duration_ms": duration_ms,
            "source": result.get("source"),
        })
