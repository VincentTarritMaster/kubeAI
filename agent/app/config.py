"""Configuration explicite ; aucun fichier .env n'est chargé automatiquement."""

from dataclasses import dataclass, field
import os
from pathlib import Path
import re
from urllib.parse import urlsplit


@dataclass(frozen=True)
class Settings:
    namespace: str = "kubeai"
    kubernetes_url: str = "https://kubernetes.default.svc"
    # Mimir expose l'API Prometheus sous ce préfixe ; le client reste compatible PromQL.
    prometheus_url: str = "http://mimir.monitoring.svc:9009/prometheus"
    token_file: str = "/var/run/secrets/kubernetes.io/serviceaccount/token"
    ca_file: str = "/var/run/secrets/kubernetes.io/serviceaccount/ca.crt"
    request_timeout: float = 5.0
    metrics_max_age: int = 60
    loki_url: str = "http://loki.monitoring.svc.cluster.local:3100"
    llm_provider: str = "mistral-api"
    llm_base_url: str = "https://api.mistral.ai/v1"
    llm_model: str = "mistral-small-2603"
    llm_api_key_file: str = "/var/run/secrets/kubeai-mistral/api-key"
    llm_timeout: float = 60.0
    llm_api_key: str = field(default="", repr=False)

    def __post_init__(self):
        # Keep previously deployed values working while exposing the two
        # canonical profiles consistently in the API and configuration.
        aliases = {"mistral": "mistral-api", "apertus": "local"}
        provider = aliases.get(self.llm_provider.strip().lower(), self.llm_provider.strip().lower())
        object.__setattr__(self, "llm_provider", provider)
        if provider == "local":
            if self.llm_base_url == "https://api.mistral.ai/v1":
                object.__setattr__(self, "llm_base_url", "http://127.0.0.1:11435/v1")
            if self.llm_model == "mistral-small-2603":
                object.__setattr__(self, "llm_model", "apertus-8b")
            if self.llm_api_key_file == "/var/run/secrets/kubeai-mistral/api-key":
                object.__setattr__(self, "llm_api_key_file", "")
            if self.llm_timeout == 60.0:
                object.__setattr__(self, "llm_timeout", 180.0)
        if len(self.namespace) > 63 or not re.fullmatch(r"[a-z0-9](?:[-a-z0-9]*[a-z0-9])?", self.namespace):
            raise ValueError("KUBERNETES_NAMESPACE doit être un nom de namespace valide")
        if not 0 < self.request_timeout <= 30:
            raise ValueError("UPSTREAM_TIMEOUT_SECONDS doit être compris entre 0 et 30")
        if not 15 <= self.metrics_max_age <= 300:
            raise ValueError("METRICS_MAX_AGE_SECONDS doit être compris entre 15 et 300")
        if not 1 <= self.llm_timeout <= 600:
            raise ValueError("LLM_TIMEOUT_SECONDS doit être compris entre 1 et 600")
        if self.llm_provider not in {"mistral-api", "local"}:
            raise ValueError("LLM_PROVIDER doit être mistral-api ou local")
        if self.llm_provider == "mistral-api":
            url = urlsplit(self.llm_base_url)
            if (url.scheme != "https" or not url.hostname or url.username or url.password
                    or url.query or url.fragment):
                raise ValueError("MISTRAL_BASE_URL doit être une URL HTTPS sans identifiants ni paramètres")
        else:
            url = urlsplit(self.llm_base_url)
            if (url.scheme not in {"http", "https"} or not url.hostname or url.username or url.password
                    or url.query or url.fragment):
                raise ValueError("LLM_BASE_URL doit être une URL HTTP(S) sans identifiants ni paramètres")

    @classmethod
    def from_env(cls):
        provider = os.getenv("LLM_PROVIDER", cls.llm_provider).strip().lower()
        local = provider in {"local", "apertus"}
        ca_file = os.getenv("KUBERNETES_CA_FILE", cls.ca_file)
        # Service-account CA files are projected only inside Kubernetes. On a
        # developer machine, use the OS trust store and let API requests report
        # the cluster as unavailable instead of failing application startup.
        if "KUBERNETES_CA_FILE" not in os.environ and not Path(ca_file).is_file():
            ca_file = ""
        return cls(
            namespace=os.getenv("KUBERNETES_NAMESPACE", cls.namespace),
            kubernetes_url=os.getenv("KUBERNETES_API_URL", cls.kubernetes_url),
            prometheus_url=os.getenv("MIMIR_URL", os.getenv("PROMETHEUS_URL", cls.prometheus_url)),
            token_file=os.getenv("KUBERNETES_TOKEN_FILE", cls.token_file),
            ca_file=ca_file,
            request_timeout=float(os.getenv("UPSTREAM_TIMEOUT_SECONDS", "5")),
            metrics_max_age=int(os.getenv("METRICS_MAX_AGE_SECONDS", "60")),
            loki_url=os.getenv("LOKI_URL", cls.loki_url),
            llm_provider=provider,
            llm_base_url=(os.getenv("LLM_BASE_URL", "http://127.0.0.1:11435/v1")
                          if local else os.getenv("MISTRAL_BASE_URL", cls.llm_base_url)),
            llm_model=(os.getenv("LLM_MODEL", "apertus-8b")
                       if local else os.getenv("MISTRAL_MODEL", cls.llm_model)),
            llm_api_key_file=(os.getenv("LLM_API_KEY_FILE", "")
                              if local else os.getenv("MISTRAL_API_KEY_FILE", cls.llm_api_key_file)),
            llm_timeout=float(os.getenv("LLM_TIMEOUT_SECONDS", "180" if local else "60")),
            llm_api_key="" if local else os.getenv("MISTRAL_API_KEY", ""),
        )

    def token(self):
        # Relire le fichier à chaque requête : les tokens projetés sont renouvelés.
        return Path(self.token_file).read_text().strip() if self.token_file else None

    def llm_key(self):
        if self.llm_api_key.strip():
            return self.llm_api_key.strip()
        return Path(self.llm_api_key_file).read_text(encoding="utf-8").strip() if self.llm_api_key_file else ""
