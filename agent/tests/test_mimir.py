import asyncio
import os
from unittest.mock import patch

import httpx
import pytest

from app.clients.base import UpstreamError
from app.clients.prometheus_client import PrometheusClient
from app.config import Settings


@pytest.mark.parametrize("base_url,expected_path", [
    ("http://mimir.monitoring.svc:9009/prometheus", "/prometheus/api/v1/query"),
    ("http://mimir.monitoring.svc:9009/prometheus/", "/prometheus/api/v1/query"),
    ("https://metrics.example.test/team/prometheus", "/team/prometheus/api/v1/query"),
    ("https://metrics.example.test/custom/", "/custom/api/v1/query"),
])
def test_snapshot_preserves_configured_query_prefix(base_url, expected_path):
    seen = []

    def respond(request):
        seen.append(request)
        return httpx.Response(200, json={
            "status": "success", "data": {"resultType": "vector", "result": []},
        })

    async def collect():
        settings = Settings(prometheus_url=base_url)
        async with httpx.AsyncClient(base_url=settings.prometheus_url,
                                     transport=httpx.MockTransport(respond), trust_env=False) as http:
            return await PrometheusClient(http, settings).snapshot(1234.5)

    snapshot = asyncio.run(collect())
    assert snapshot.cpu == snapshot.memory == []
    assert len(seen) == 2
    assert all(request.method == "GET" for request in seen)
    assert all(request.url.path == expected_path for request in seen)
    assert all(request.url.params["time"] == "1234.5" for request in seen)
    assert all(request.url.params["timeout"] == "5000ms" for request in seen)
    queries = [request.url.params["query"] for request in seen]
    assert any("rate(container_cpu_usage_seconds_total" in query for query in queries)
    assert any("container_memory_working_set_bytes" in query for query in queries)


@pytest.mark.parametrize("environment,expected", [
    ({}, "http://mimir.monitoring.svc:9009/prometheus"),
    ({"PROMETHEUS_URL": "http://legacy.test/query"}, "http://legacy.test/query"),
    ({"MIMIR_URL": "http://mimir.test/prometheus", "PROMETHEUS_URL": "http://legacy.test/query"},
     "http://mimir.test/prometheus"),
])
def test_metrics_configuration_default_and_environment_precedence(environment, expected):
    with patch.dict(os.environ, environment, clear=True):
        assert Settings.from_env().prometheus_url == expected


@pytest.mark.parametrize("response,expected_message", [
    (httpx.Response(503), "mimir returned HTTP 503"),
    (httpx.Response(200, json={"status": "success", "data": {"resultType": "matrix", "result": []}}),
     "Invalid Mimir result"),
])
def test_query_errors_identify_mimir(response, expected_message):
    async def collect():
        async with httpx.AsyncClient(base_url=Settings.prometheus_url,
                                     transport=httpx.MockTransport(lambda request: response)) as http:
            await PrometheusClient(http, Settings())._query("up", 1234.5)

    with pytest.raises(UpstreamError) as raised:
        asyncio.run(collect())
    assert raised.value.source == "mimir"
    assert raised.value.message == expected_message
