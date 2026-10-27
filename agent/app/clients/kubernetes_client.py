from dataclasses import dataclass

import httpx

from app.clients.base import UpstreamError, get_json
from app.config import Settings


@dataclass(frozen=True)
class CollectionResult:
    items: list[dict]
    truncated: bool = False


class KubernetesClient:
    PAGE_SIZE = 200
    MAX_PAGES = 25
    MAX_NODES = 500
    MAX_PODS = 2000
    MAX_EVENTS = 500

    def __init__(self, http: httpx.AsyncClient, settings: Settings):
        self.http = http
        self.settings = settings

    async def _page(self, path: str, params: dict):
        try:
            token = self.settings.token()
        except OSError as error:
            raise UpstreamError("kubernetes", "Cannot read the Kubernetes token") from error
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        data = await get_json(
            self.http, "kubernetes",
            path,
            headers=headers, params=params,
        )
        items = data.get("items")
        metadata = data.get("metadata") or {}
        if (not isinstance(items, list) or not all(isinstance(item, dict) for item in items)
                or not isinstance(metadata, dict) or not isinstance(metadata.get("continue", ""), str)):
            raise UpstreamError("kubernetes", "Invalid Kubernetes list response")
        return items, metadata.get("continue", "")

    async def _collect(self, path: str, max_items: int) -> CollectionResult:
        items = []
        continuation = ""
        seen = set()
        for _ in range(self.MAX_PAGES):
            remaining = max_items - len(items)
            page, next_token = await self._page(path, {
                "limit": min(self.PAGE_SIZE, remaining), "continue": continuation,
            })
            items.extend(page[:remaining])
            if len(page) > remaining or (next_token and len(items) >= max_items):
                return CollectionResult(items, truncated=True)
            if not next_token:
                return CollectionResult(items)
            if next_token == continuation or next_token in seen:
                raise UpstreamError("kubernetes", "Kubernetes pagination token repeated")
            seen.add(next_token)
            continuation = next_token
        return CollectionResult(items, truncated=True)

    async def list_namespace_pods(self) -> CollectionResult:
        return await self._collect(f"/api/v1/namespaces/{self.settings.namespace}/pods", self.MAX_PODS)

    async def list_pods(self):
        return (await self.list_namespace_pods()).items

    async def list_nodes(self) -> CollectionResult:
        return await self._collect("/api/v1/nodes", self.MAX_NODES)

    async def list_all_pods(self) -> CollectionResult:
        return await self._collect("/api/v1/pods", self.MAX_PODS)

    async def list_events(self) -> CollectionResult:
        return await self._collect("/api/v1/events", self.MAX_EVENTS)

    async def ready(self):
        await self._page("/api/v1/pods", {"limit": 1})
