import json

import httpx

from app.clients.base import UpstreamError
from app.config import Settings


class LLMService:
    def __init__(self, http: httpx.AsyncClient, settings: Settings):
        self.http = http
        self.settings = settings

    @property
    def provider_name(self):
        return "Mistral API" if self.settings.llm_provider == "mistral-api" else "Apertus local"

    def headers(self):
        try:
            key = self.settings.llm_key()
        except (OSError, UnicodeError) as error:
            raise UpstreamError("llm", f"Clé {self.provider_name} introuvable ou illisible ; configurez le fournisseur") from error
        if self.settings.llm_provider == "mistral-api" and not key:
            raise UpstreamError("llm", "Clé Mistral manquante ; configurez MISTRAL_API_KEY ou MISTRAL_API_KEY_FILE")
        if key and (not key.isascii() or "\r" in key or "\n" in key):
            raise UpstreamError("llm", f"Format de clé {self.provider_name} invalide")
        return {"Authorization": "Bearer " + key} if key else {}

    def check_url(self, request):
        if self.settings.llm_provider == "mistral-api" and (
                request.url.scheme != "https" or request.url.username or request.url.password):
            raise UpstreamError("llm", "La connexion Mistral exige HTTPS sans identifiants dans l’URL")

    def status_error(self, code):
        name = self.provider_name
        if code in {401, 403}:
            message = f"Authentification {name} refusée (HTTP {code}) ; vérifiez la clé et les droits"
        elif code == 429:
            message = f"Quota ou limite de requêtes {name} atteint (HTTP 429) ; réessayez plus tard"
        elif code >= 500:
            message = f"Service {name} indisponible (HTTP {code}) ; réessayez plus tard"
        else:
            message = f"Réponse HTTP {code} inattendue de {name}"
        return UpstreamError("llm", message)

    async def open_completion(self, payload: dict):
        payload = {**payload, "model": self.settings.llm_model}
        try:
            request = self.http.build_request("POST", "chat/completions", json=payload,
                                              headers=self.headers(), timeout=self.settings.llm_timeout)
            self.check_url(request)
            response = await self.http.send(request, stream=True, follow_redirects=False)
            if not 200 <= response.status_code < 300:
                code = response.status_code
                await response.aclose()
                raise self.status_error(code)
            return response
        except httpx.TimeoutException as error:
            raise UpstreamError("llm", f"Délai de réponse {self.provider_name} dépassé") from error
        except httpx.RequestError as error:
            raise UpstreamError("llm", f"Connexion au service {self.provider_name} impossible") from error

    async def complete(self, payload: dict):
        response = await self.open_completion({**payload, "stream": False})
        try:
            body = json.loads(await response.aread())
            if (not isinstance(body["choices"][0]["message"]["content"], str)
                    or not body["choices"][0]["message"]["content"].strip()):
                raise ValueError("Text content expected")
            return body
        except (KeyError, IndexError, TypeError, ValueError) as error:
            raise UpstreamError("llm", f"Réponse JSON {self.provider_name} invalide ou sans contenu texte") from error
        except httpx.TimeoutException as error:
            raise UpstreamError("llm", f"Délai de lecture de la réponse {self.provider_name} dépassé") from error
        except httpx.RequestError as error:
            raise UpstreamError("llm", f"Lecture de la réponse {self.provider_name} interrompue") from error
        finally:
            await response.aclose()

    async def status(self):
        try:
            request = self.http.build_request("GET", "models", headers=self.headers(), timeout=5)
            self.check_url(request)
            response = await self.http.send(request, follow_redirects=False)
            if not 200 <= response.status_code < 300:
                raise self.status_error(response.status_code)
            ids = [item["id"] for item in response.json()["data"]]
            if self.settings.llm_model not in ids:
                raise UpstreamError("llm", f"Le modèle {self.provider_name} configuré n’est pas disponible")
            return {"status": "ready", "provider": self.settings.llm_provider,
                    "model": self.settings.llm_model,
                    "hosting": "api" if self.settings.llm_provider == "mistral-api" else "configured_endpoint"}
        except httpx.TimeoutException as error:
            raise UpstreamError("llm", f"Délai du contrôle de statut {self.provider_name} dépassé") from error
        except httpx.RequestError as error:
            raise UpstreamError("llm", f"Contrôle du statut {self.provider_name} impossible") from error
        except (ValueError, KeyError, TypeError) as error:
            raise UpstreamError("llm", f"Réponse de statut {self.provider_name} invalide") from error
