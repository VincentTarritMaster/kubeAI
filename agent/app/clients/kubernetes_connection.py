"""Build a verified HTTPX connection from either pod identity or kubeconfig."""

import asyncio
import ssl
from pathlib import Path
from urllib.parse import urlsplit

import httpx


class KubeconfigAuth(httpx.Auth):
    """Read refreshed credentials from the official Kubernetes config loader."""

    def __init__(self, configuration):
        self.configuration = configuration

    def _add_credentials(self, request):
        token = self.configuration.get_api_key_with_prefix("BearerToken", alias="authorization")
        if token:
            request.headers["Authorization"] = token
        elif getattr(self.configuration, "access_token", None):
            request.headers["Authorization"] = "Bearer " + self.configuration.access_token
        elif (getattr(self.configuration, "username", None) is not None
              or getattr(self.configuration, "password", None) is not None):
            request.headers.update(httpx.BasicAuth(
                self.configuration.username or "", self.configuration.password or "",
            ).auth_flow(request).__next__().headers)

    def auth_flow(self, request):
        self._add_credentials(request)
        yield request

    async def async_auth_flow(self, request):
        # Exec and auth-provider refresh hooks may run subprocesses; keep them
        # off FastAPI's event loop while retaining the SDK's refresh semantics.
        await asyncio.to_thread(self._add_credentials, request)
        yield request


def _load_kube_configuration(settings):
    try:
        from kubernetes import client as kubernetes_client
        from kubernetes import config as kubernetes_config
    except ImportError as error:
        raise RuntimeError(
            "Le mode kubeconfig nécessite la dépendance kubernetes de agent/requirements.txt"
        ) from error

    configuration = kubernetes_client.Configuration()
    config_file = Path(settings.kubeconfig_file).expanduser() if settings.kubeconfig_file else None
    kubernetes_config.load_kube_config(
        config_file=str(config_file) if config_file else None,
        context=settings.kubernetes_context,
        client_configuration=configuration,
        persist_config=False,
    )
    return configuration


def build_kubernetes_http_options(settings):
    """Return safe AsyncClient options without exposing kubeconfig credentials."""
    if settings.kubernetes_access_mode == "in-cluster":
        verify = ssl.create_default_context(cafile=settings.ca_file) if settings.ca_file else True
        return {"base_url": settings.kubernetes_url, "verify": verify}

    try:
        configuration = _load_kube_configuration(settings)
    except Exception as error:
        raise RuntimeError(
            "Impossible de charger le kubeconfig ou le contexte Kubernetes demandé"
        ) from error

    parsed = urlsplit(configuration.host or "")
    if (parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password
            or parsed.query or parsed.fragment):
        raise RuntimeError("Le serveur du kubeconfig doit être une URL HTTPS valide")
    if not getattr(configuration, "verify_ssl", True):
        raise RuntimeError("Le kubeconfig désactive la vérification TLS ; connexion refusée")
    tls_server_name = getattr(configuration, "tls_server_name", None)
    if tls_server_name and tls_server_name.lower() != parsed.hostname.lower():
        raise RuntimeError("Le kubeconfig demande un nom TLS différent du serveur ; configuration non prise en charge")

    ca_file = getattr(configuration, "ssl_ca_cert", None)
    try:
        verify = ssl.create_default_context(cafile=ca_file) if ca_file else True
    except (OSError, ssl.SSLError) as error:
        raise RuntimeError("Le certificat CA du kubeconfig est inaccessible ou invalide") from error

    cert_file = getattr(configuration, "cert_file", None)
    key_file = getattr(configuration, "key_file", None)
    if bool(cert_file) != bool(key_file):
        raise RuntimeError("Le kubeconfig doit fournir ensemble le certificat client et sa clé")

    options = {
        "base_url": configuration.host.rstrip("/") + "/",
        "verify": verify,
        "auth": KubeconfigAuth(configuration),
    }
    if cert_file:
        options["cert"] = (cert_file, key_file)
    proxy = getattr(configuration, "proxy", None)
    if proxy:
        options["proxy"] = proxy
    return options
