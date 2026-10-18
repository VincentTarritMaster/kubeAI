from app.clients.base import UpstreamError, get_json
from app.config import Settings
import httpx


class KubernetesClient:
    def __init__(self, http: httpx.AsyncClient, settings: Settings):
        self.http = http
        self.settings = settings

    async def _page(self, params):
        try:
            token = self.settings.token()
        except OSError as error:
            raise UpstreamError("kubernetes", "Cannot read the Kubernetes token") from error
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        data = await get_json(
            self.http, "kubernetes",
            f"/api/v1/namespaces/{self.settings.namespace}/pods",
            headers=headers, params=params,
        )
        if not isinstance(data.get("items"), list):
            raise UpstreamError("kubernetes", "The Kubernetes response does not contain a pod list")
        return data

    async def list_pods(self):
        items = []
        continuation = ""
        while True:
            page = await self._page({"limit": 500, "continue": continuation})
            items.extend(page["items"])
            continuation = page.get("metadata", {}).get("continue", "")
            if not continuation:
                return items

    async def ready(self):
        await self._page({"limit": 1})
