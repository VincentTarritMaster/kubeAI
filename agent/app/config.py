"""Configuration explicite ; aucun fichier .env n'est chargé automatiquement."""

from dataclasses import dataclass
import os
from pathlib import Path
import re


@dataclass(frozen=True)
class Settings:
    namespace: str = "kubeai"
    kubernetes_url: str = "https://kubernetes.default.svc"
    prometheus_url: str = "http://prometheus.monitoring.svc.cluster.local:9090"
    token_file: str = "/var/run/secrets/kubernetes.io/serviceaccount/token"
    ca_file: str = "/var/run/secrets/kubernetes.io/serviceaccount/ca.crt"
    request_timeout: float = 5.0
    metrics_max_age: int = 60
    loki_url: str = "http://loki.monitoring.svc.cluster.local:3100"
    llm_base_url: str = "http://host.minikube.internal:11435/v1"
    llm_model: str = "apertus-8b"
    llm_api_key_file: str = "/var/run/secrets/kubeai-llm/api-key"
    llm_timeout: float = 180.0

    def __post_init__(self):
        if len(self.namespace) > 63 or not re.fullmatch(r"[a-z0-9](?:[-a-z0-9]*[a-z0-9])?", self.namespace):
            raise ValueError("KUBERNETES_NAMESPACE doit être un nom de namespace valide")
        if not 0 < self.request_timeout <= 30:
            raise ValueError("UPSTREAM_TIMEOUT_SECONDS doit être compris entre 0 et 30")
        if not 15 <= self.metrics_max_age <= 300:
            raise ValueError("METRICS_MAX_AGE_SECONDS doit être compris entre 15 et 300")
        if not 1 <= self.llm_timeout <= 600:
            raise ValueError("LLM_TIMEOUT_SECONDS doit être compris entre 1 et 600")

    @classmethod
    def from_env(cls):
        return cls(
            namespace=os.getenv("KUBERNETES_NAMESPACE", cls.namespace),
            kubernetes_url=os.getenv("KUBERNETES_API_URL", cls.kubernetes_url),
            prometheus_url=os.getenv("PROMETHEUS_URL", cls.prometheus_url),
            token_file=os.getenv("KUBERNETES_TOKEN_FILE", cls.token_file),
            ca_file=os.getenv("KUBERNETES_CA_FILE", cls.ca_file),
            request_timeout=float(os.getenv("UPSTREAM_TIMEOUT_SECONDS", "5")),
            metrics_max_age=int(os.getenv("METRICS_MAX_AGE_SECONDS", "60")),
            loki_url=os.getenv("LOKI_URL", cls.loki_url),
            llm_base_url=os.getenv("LLM_BASE_URL", cls.llm_base_url),
            llm_model=os.getenv("LLM_MODEL", cls.llm_model),
            llm_api_key_file=os.getenv("LLM_API_KEY_FILE", cls.llm_api_key_file),
            llm_timeout=float(os.getenv("LLM_TIMEOUT_SECONDS", "180")),
        )

    def token(self):
        # Relire le fichier à chaque requête : les tokens projetés sont renouvelés.
        return Path(self.token_file).read_text().strip() if self.token_file else None

    def llm_key(self):
        return Path(self.llm_api_key_file).read_text().strip() if self.llm_api_key_file else ""
