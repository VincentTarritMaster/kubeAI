import asyncio
import time

import httpx

from app.clients.loki_client import LokiClient
from app.clients.prometheus_client import PrometheusClient
from app.config import Settings
from app.services.incident_timeline import resolve_request
from types import SimpleNamespace


def test_all_metric_queries_are_scoped_to_observed_cluster():
    queries = []

    def respond(request):
        queries.append(request.url.params['query'])
        result_type = 'matrix' if request.url.path.endswith('query_range') else 'vector'
        return httpx.Response(200, json={'status': 'success', 'data': {'resultType': result_type, 'result': []}})

    async def collect():
        settings = Settings(observed_cluster='workloads')
        async with httpx.AsyncClient(base_url=settings.prometheus_url, transport=httpx.MockTransport(respond)) as http:
            client = PrometheusClient(http, settings)
            await client.snapshot(1234)
            await client.cluster_usage(1234)
            await client.job_status('kube-state-metrics', 1234)
            await client.metric_present('http_requests_total', 1234)
            await client.history(1000, 1300, 'kubeai', 'demo', include_correlation=True)
            await client.incident_signals(1000, 1300, 'kubeai', 'demo')

    asyncio.run(collect())
    assert len(queries) >= 15
    assert all('cluster="workloads"' in query for query in queries)


def test_all_log_queries_are_scoped_to_observed_cluster():
    queries = []

    def respond(request):
        queries.append(request.url.params['query'])
        return httpx.Response(200, json={'status': 'success', 'data': {'resultType': 'streams', 'result': []}})

    async def collect():
        settings = Settings(observed_cluster='workloads')
        async with httpx.AsyncClient(base_url=settings.loki_url, transport=httpx.MockTransport(respond)) as http:
            client = LokiClient(http, settings)
            await client.recent_logs(['demo'])
            end = time.time_ns()
            await client.logs_between(end - 10**9, end, 'kubeai', 'demo')
            await client.latest_demo_episode()

    asyncio.run(collect())
    assert len(queries) == 3
    assert all('cluster="workloads"' in query for query in queries)


def test_latest_demo_resolves_in_configured_application_namespace():
    start = time.time_ns() - 300 * 10**9
    phases = ['normal', 'anomaly_start', 'recovered', 'complete']

    def respond(request):
        assert 'namespace="applications"' in request.url.params['query']
        assert 'cluster="workloads"' in request.url.params['query']
        return httpx.Response(200, json={'status': 'success', 'data': {'resultType': 'streams', 'result': [{
            'stream': {'namespace': 'applications', 'pod': 'kubeai-demo-incident', 'cluster': 'workloads'},
            'values': [[str(start + index * 60 * 10**9), 'KUBEAI_DEMO_INCIDENT phase=' + phase]
                       for index, phase in enumerate(phases)]}]}})

    async def collect():
        settings = Settings(namespace='applications', observed_cluster='workloads')
        async with httpx.AsyncClient(base_url=settings.loki_url, transport=httpx.MockTransport(respond)) as http:
            service = SimpleNamespace(loki=LokiClient(http, settings))
            chat = SimpleNamespace(messages=[SimpleNamespace(role='user', content="Analyse l'incident du dernier script de démonstration")])
            window, error, _ = await resolve_request(service, chat)
            assert error is None
            assert window['namespace'] == 'applications'
            assert window['start_ns'] == start

    asyncio.run(collect())
