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
    llm_provider: str = "mistral"
    llm_base_url: str = "https://api.mistral.ai/v1"
    llm_model: str = "mistral-small-2603"
    llm_api_key_file: str = "/var/run/secrets/kubeai-mistral/api-key"
    llm_timeout: float = 60.0
    llm_api_key: str = field(default="", repr=False)

    def __post_init__(self):
        if len(self.namespace) > 63 or not re.fullmatch(r"[a-z0-9](?:[-a-z0-9]*[a-z0-9])?", self.namespace):
            raise ValueError("KUBERNETES_NAMESPACE doit être un nom de namespace valide")
        if not 0 < self.request_timeout <= 30:
            raise ValueError("UPSTREAM_TIMEOUT_SECONDS doit être compris entre 0 et 30")
        if not 15 <= self.metrics_max_age <= 300:
            raise ValueError("METRICS_MAX_AGE_SECONDS doit être compris entre 15 et 300")
        if not 1 <= self.llm_timeout <= 600:
            raise ValueError("LLM_TIMEOUT_SECONDS doit être compris entre 1 et 600")
        if self.llm_provider not in {"mistral", "apertus"}:
            raise ValueError("LLM_PROVIDER doit être mistral ou apertus")
        if self.llm_provider == "mistral":
            url = urlsplit(self.llm_base_url)
            if (url.scheme != "https" or not url.hostname or url.username or url.password
                    or url.query or url.fragment):
                raise ValueError("MISTRAL_BASE_URL doit être une URL HTTPS sans identifiants ni paramètres")

    @classmethod
    def from_env(cls):
        provider = os.getenv("LLM_PROVIDER", cls.llm_provider).strip().lower()
        aperture = provider == "apertus"
        return cls(
            namespace=os.getenv("KUBERNETES_NAMESPACE", cls.namespace),
            kubernetes_url=os.getenv("KUBERNETES_API_URL", cls.kubernetes_url),
            prometheus_url=os.getenv("MIMIR_URL", os.getenv("PROMETHEUS_URL", cls.prometheus_url)),
            token_file=os.getenv("KUBERNETES_TOKEN_FILE", cls.token_file),
            ca_file=os.getenv("KUBERNETES_CA_FILE", cls.ca_file),
            request_timeout=float(os.getenv("UPSTREAM_TIMEOUT_SECONDS", "5")),
            metrics_max_age=int(os.getenv("METRICS_MAX_AGE_SECONDS", "60")),
            loki_url=os.getenv("LOKI_URL", cls.loki_url),
            llm_provider=provider,
            llm_base_url=(os.getenv("LLM_BASE_URL", "http://host.minikube.internal:11435/v1")
                          if aperture else os.getenv("MISTRAL_BASE_URL", cls.llm_base_url)),
            llm_model=(os.getenv("LLM_MODEL", "apertus-8b")
                       if aperture else os.getenv("MISTRAL_MODEL", cls.llm_model)),
            llm_api_key_file=(os.getenv("LLM_API_KEY_FILE", "/var/run/secrets/kubeai-llm/api-key")
                              if aperture else os.getenv("MISTRAL_API_KEY_FILE", cls.llm_api_key_file)),
            llm_timeout=float(os.getenv("LLM_TIMEOUT_SECONDS", "180" if aperture else "60")),
            llm_api_key="" if aperture else os.getenv("MISTRAL_API_KEY", ""),
        )

    def token(self):
        # Relire le fichier à chaque requête : les tokens projetés sont renouvelés.
        return Path(self.token_file).read_text().strip() if self.token_file else None

    def llm_key(self):
        if self.llm_api_key.strip():
            return self.llm_api_key.strip()
        return Path(self.llm_api_key_file).read_text(encoding="utf-8").strip() if self.llm_api_key_file else ""
