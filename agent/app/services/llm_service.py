import json

import httpx

from app.clients.base import UpstreamError
from app.config import Settings


class LLMService:
    def __init__(self, http: httpx.AsyncClient, settings: Settings):
        self.http = http
        self.settings = settings

    def headers(self):
        try:
            key = self.settings.llm_key()
        except OSError as error:
            raise UpstreamError("llm", "Local Apertus key is missing; configure the model first") from error
        return {"Authorization": "Bearer " + key} if key else {}

    async def open_completion(self, payload: dict):
        payload = {**payload, "model": self.settings.llm_model}
        try:
            request = self.http.build_request("POST", "chat/completions", json=payload, headers=self.headers())
            response = await self.http.send(request, stream=True)
            if response.is_error:
                code = response.status_code
                await response.aclose()
                raise UpstreamError("llm", f"Apertus returned HTTP {code}")
            return response
        except httpx.TimeoutException as error:
            raise UpstreamError("llm", "Apertus request timed out") from error
        except httpx.RequestError as error:
            raise UpstreamError("llm", "Local Apertus is unreachable; check its server") from error

    async def complete(self, payload: dict):
        response = await self.open_completion({**payload, "stream": False})
        try:
            body = json.loads(await response.aread())
            if not isinstance(body["choices"][0]["message"]["content"], str):
                raise ValueError("Text content expected")
            return body
        except (KeyError, IndexError, TypeError, ValueError) as error:
            raise UpstreamError("llm", "Invalid Apertus response") from error
        except httpx.RequestError as error:
            raise UpstreamError("llm", "Apertus response interrupted while reading") from error
        finally:
            await response.aclose()

    async def status(self):
        try:
            response = await self.http.get("models", headers=self.headers(), timeout=5)
            response.raise_for_status()
            ids = [item["id"] for item in response.json()["data"]]
            if self.settings.llm_model not in ids:
                raise UpstreamError("llm", "The server does not expose the configured Apertus model")
            return {"status": "ready", "model": self.settings.llm_model, "hosting": "self-hosted"}
        except (httpx.HTTPError, ValueError, KeyError, TypeError) as error:
            raise UpstreamError("llm", "Local Apertus is not ready") from error
