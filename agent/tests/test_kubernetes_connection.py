from types import SimpleNamespace
import asyncio
import sys
from pathlib import Path

import httpx
import pytest
import certifi

import app.clients.kubernetes_connection as connection_module
from app.clients.kubernetes_connection import KubeconfigAuth, build_kubernetes_http_options
from app.config import Settings


def config(**overrides):
    values = {
        "host": "https://cluster.example.test:6443",
        "verify_ssl": True,
        "ssl_ca_cert": None,
        "cert_file": None,
        "key_file": None,
        "tls_server_name": None,
        "proxy": None,
        "access_token": None,
        "username": None,
        "password": None,
        "get_api_key_with_prefix": lambda *_args, **_kwargs: "Bearer fake-token",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def kubeconfig_settings(**overrides):
    values = {
        "kubernetes_access_mode": "kubeconfig",
        "kubernetes_context": "unit-context",
        "token_file": "missing-projected-token",
    }
    values.update(overrides)
    return Settings(**values)


def test_in_cluster_connection_keeps_service_url_and_verified_tls():
    settings = Settings(ca_file="")

    options = build_kubernetes_http_options(settings)

    assert options["base_url"] == "https://kubernetes.default.svc"
    assert options["verify"] is True
    assert "auth" not in options


def test_kubeconfig_uses_selected_host_context_auth_and_tls(monkeypatch):
    loaded = []
    kubeconfig = config(ssl_ca_cert=certifi.where(), cert_file="client.pem", key_file="client-key.pem")
    monkeypatch.setattr(connection_module, "_load_kube_configuration", lambda settings: (loaded.append(settings), kubeconfig)[1])

    options = build_kubernetes_http_options(kubeconfig_settings())

    assert loaded and options["base_url"] == "https://cluster.example.test:6443/"
    assert options["verify"].verify_mode == 2  # ssl.CERT_REQUIRED
    assert options["cert"] == ("client.pem", "client-key.pem")
    assert isinstance(options["auth"], KubeconfigAuth)


def test_kubeconfig_mode_requires_an_explicit_context():
    with pytest.raises(ValueError, match="KUBERNETES_CONTEXT"):
        Settings(kubernetes_access_mode="kubeconfig")


def test_loader_uses_the_requested_file_and_context(monkeypatch):
    calls = {}

    class Configuration:
        pass

    def load_kube_config(**kwargs):
        calls.update(kwargs)

    fake_package = SimpleNamespace(
        client=SimpleNamespace(Configuration=Configuration),
        config=SimpleNamespace(load_kube_config=load_kube_config),
    )
    monkeypatch.setitem(sys.modules, "kubernetes", fake_package)
    settings = kubeconfig_settings(kubeconfig_file="C:/private/config", kubernetes_context="dev-context")

    loaded = connection_module._load_kube_configuration(settings)

    assert isinstance(loaded, Configuration)
    assert calls["config_file"] == str(Path("C:/private/config").expanduser())
    assert calls["context"] == "dev-context"
    assert calls["persist_config"] is False
    assert calls["client_configuration"] is loaded


def test_official_loader_reads_selected_context_without_network(tmp_path):
    kubeconfig_path = tmp_path / "config"
    kubeconfig_path.write_text(
        """apiVersion: v1
kind: Config
clusters:
- name: test-cluster
  cluster:
    server: https://127.0.0.1:6443
users:
- name: test-user
  user:
    token: fake-kubeconfig-token
contexts:
- name: unit-context
  context:
    cluster: test-cluster
    user: test-user
current-context: some-other-context
""",
        encoding="utf-8",
    )
    options = build_kubernetes_http_options(
        kubeconfig_settings(kubeconfig_file=str(kubeconfig_path)),
    )
    request = next(options["auth"].auth_flow(httpx.Request(
        "GET", "https://127.0.0.1:6443/api/v1/nodes",
    )))

    assert options["base_url"] == "https://127.0.0.1:6443/"
    assert request.headers["authorization"] == "Bearer fake-kubeconfig-token"


def test_unknown_context_fails_with_sanitized_startup_error(monkeypatch):
    monkeypatch.setattr(connection_module, "_load_kube_configuration",
                        lambda _settings: (_ for _ in ()).throw(ValueError("private config detail")))

    with pytest.raises(RuntimeError, match="contexte Kubernetes demandé") as raised:
        build_kubernetes_http_options(kubeconfig_settings())
    assert "private config detail" not in str(raised.value)


def test_invalid_or_untrusted_kubeconfig_fails_closed(monkeypatch):
    settings = kubeconfig_settings()
    monkeypatch.setattr(connection_module, "_load_kube_configuration", lambda _settings: config(verify_ssl=False))

    with pytest.raises(RuntimeError, match="désactive la vérification TLS"):
        build_kubernetes_http_options(settings)


def test_kubeconfig_rejects_invalid_ca_and_non_https_server(monkeypatch):
    settings = kubeconfig_settings()
    monkeypatch.setattr(connection_module, "_load_kube_configuration", lambda _settings: config(host="http://cluster.test"))
    with pytest.raises(RuntimeError, match="HTTPS"):
        build_kubernetes_http_options(settings)

    monkeypatch.setattr(connection_module, "_load_kube_configuration", lambda _settings: config(ssl_ca_cert="missing-ca.pem"))
    with pytest.raises(RuntimeError, match="CA du kubeconfig"):
        build_kubernetes_http_options(settings)


def test_kubeconfig_auth_refreshes_token_without_projected_pod_token():
    tokens = iter(("Bearer fake-one", "Bearer fake-two"))
    auth = KubeconfigAuth(config(get_api_key_with_prefix=lambda *_args, **_kwargs: next(tokens)))
    settings = kubeconfig_settings()

    assert settings.token() is None
    first = next(auth.auth_flow(httpx.Request("GET", "https://cluster.example.test/api/v1/nodes")))
    second = next(auth.auth_flow(httpx.Request("GET", "https://cluster.example.test/api/v1/pods")))
    assert first.headers["authorization"] == "Bearer fake-one"
    assert second.headers["authorization"] == "Bearer fake-two"


def test_async_auth_refresh_runs_through_async_flow():
    auth = KubeconfigAuth(config())

    async def apply_auth():
        request = httpx.Request("GET", "https://cluster.example.test/api/v1/nodes")
        async for authenticated in auth.async_auth_flow(request):
            return authenticated

    request = asyncio.run(apply_auth())
    assert request.headers["authorization"] == "Bearer fake-token"


def test_kubeconfig_auth_supports_basic_auth_when_configured():
    auth = KubeconfigAuth(config(get_api_key_with_prefix=lambda *_args, **_kwargs: None,
                                 username="fake-user", password="fake-password"))

    request = next(auth.auth_flow(httpx.Request("GET", "https://cluster.example.test/api/v1/events")))

    assert request.headers["authorization"].startswith("Basic ")


@pytest.mark.parametrize("environment,expected_mimir,expected_loki", [
    ({}, "http://mimir.monitoring.svc:9009/prometheus",
     "http://loki.monitoring.svc.cluster.local:3100"),
    ({"MIMIR_URL": "http://127.0.0.1:9009/prometheus", "LOKI_URL": "http://127.0.0.1:3100"},
     "http://127.0.0.1:9009/prometheus", "http://127.0.0.1:3100"),
])
def test_monitoring_urls_support_cluster_services_and_windows_tunnels(
        monkeypatch, environment, expected_mimir, expected_loki):
    for name in ("KUBEAI_ENV", "MIMIR_URL", "PROMETHEUS_URL", "LOKI_URL"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("KUBEAI_ENV", "production")
    for name, value in environment.items():
        monkeypatch.setenv(name, value)

    settings = Settings.from_env()

    assert settings.prometheus_url == expected_mimir
    assert settings.loki_url == expected_loki
