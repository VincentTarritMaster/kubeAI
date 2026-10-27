"""Automatic incident discovery must use timestamped evidence and preserve explicit bounds."""
import asyncio
from datetime import datetime, timezone
import json
from types import SimpleNamespace
import time

from fastapi.testclient import TestClient
import httpx
import pytest

from app.clients.base import UpstreamError
from app.clients.loki_client import HistoricalLogs, LokiClient
from app.config import Settings
from app.conversation import Dialogue
from app.main import create_app
from app.models import ChatRequest
from app.services.incident_timeline import _historical_log_signal, resolve_request
from app.services.latest_incident import discover


def entry(at, pod='api-worker', message='connection refused'):
    return {'namespace': 'applications', 'pod': pod, 'timestamp_ns': str(at), 'message': message}


def warning(at, pod='api-worker', namespace='applications', kind='Pod', category='Warning'):
    return SimpleNamespace(type=category, involved_kind=kind, namespace=namespace, involved_name=pod,
                           observed_at=datetime.fromtimestamp(at / 10**9, timezone.utc))


def service(*, entries=(), events=(), episode=None, fail_logs=False, fail_events=False):
    class Logs:
        settings = Settings(namespace='applications', observed_cluster='observed')

        async def incident_candidates(self, namespace, pod):
            if fail_logs:
                raise UpstreamError('loki', 'unavailable')
            return HistoricalLogs([item for item in entries if item['namespace'] == namespace
                                   and (pod is None or item['pod'] == pod)])

        async def latest_demo_episode(self):
            return episode

    async def inventory():
        return SimpleNamespace(events=None if fail_events else list(events),
                               sources={'events': SimpleNamespace(status='unavailable' if fail_events else 'ok')})

    return SimpleNamespace(loki=Logs(), cluster_status=SimpleNamespace(inventory=inventory))


def find(source, *, parts=None, state=None):
    return asyncio.run(discover(source, parts or {}, _historical_log_signal, state or Dialogue()))


def test_latest_log_discovers_a_non_demo_pod_without_current_inventory():
    now = time.time_ns()
    at = now - 60 * 10**9
    result, error, _ = find(service(entries=[entry(at), entry(at - 3600 * 10**9, 'older-worker')]))
    assert error is None and result['pod'] == 'api-worker'
    assert result['start_ns'] == at - 120 * 10**9
    assert at <= result['end_ns'] <= time.time_ns()
    assert 'restent à vérifier' in result['search_note']


def test_latest_warning_competes_with_logs_and_ignores_unrelated_or_future_events():
    now = time.time_ns()
    result, error, _ = find(service(entries=[entry(now - 600 * 10**9)], events=[
        warning(now - 60 * 10**9, 'failed-worker'), warning(now, 'other', namespace='collecte'),
        warning(now + 3600 * 10**9, 'future'), warning(now, 'healthy', category='Normal'),
        warning(now, 'node', kind='Node'), warning(now, '../invalid'),
    ]))
    assert error is None and result['pod'] == 'failed-worker'
    assert 'Kubernetes' in result['search_note']


def test_old_completed_demo_cannot_hide_a_newer_failure_in_the_same_pod():
    now = time.time_ns()
    start = now - 3600 * 10**9
    episode = {'start_ns': start, 'end_ns': start + 240 * 10**9, 'markers': [
        {'phase': 'anomaly_start', 'timestamp_ns': str(start + 90 * 10**9)}]}
    result, error, _ = find(service(entries=[entry(now - 60 * 10**9, 'kubeai-demo-incident')], episode=episode))
    assert error is None and result['start_ns'] > episode['end_ns']
    assert 'restent à vérifier' in result['search_note']


def test_simultaneous_pods_require_verified_selection():
    at = time.time_ns() - 120 * 10**9
    source = service(events=[warning(at, 'api-a'), warning(at, 'api-b')])
    result, error, _ = find(source)
    assert result is None and error.startswith('Plusieurs pods correspondent')
    result, error, _ = find(source, state=Dialogue(choices=(('applications', 'api-a'), ('applications', 'api-b')), selection=1))
    assert error is None and result['pod'] == 'api-b'


@pytest.mark.parametrize('fail_logs,fail_events', [(False, False), (True, True)])
def test_absent_or_unavailable_sources_never_invent_an_incident(fail_logs, fail_events):
    result, error, _ = find(service(fail_logs=fail_logs, fail_events=fail_events))
    assert result is None and 'Cela ne prouve pas' in error
    if fail_logs:
        assert 'Recherche incomplète' in error


def test_explicit_window_is_not_replaced_by_automatic_discovery():
    request = ChatRequest(messages=[{'role': 'user', 'content':
        'Analyse le dernier incident namespace=applications pod=api-worker '
        'début=2026-10-19T18:25:00Z fin=2026-10-19T18:33:00Z'}])
    result, error, _ = asyncio.run(resolve_request(SimpleNamespace(), request))
    assert error is None
    assert result['start'] == datetime(2026, 10, 19, 18, 25, tzinfo=timezone.utc)


@pytest.mark.parametrize('message', ['no error observed', 'errors=0', 'sans anomalie', 'phase=normal'])
def test_normal_messages_are_not_discovery_signals(message):
    at = time.time_ns() - 60 * 10**9
    result, error, _ = find(service(entries=[entry(at, message=message)]))
    assert result is None


def test_log_discovery_is_bounded_and_scoped_to_cluster_namespace_and_timestamps():
    now = time.time_ns()
    values = [[str(now - (index + 1) * 10**9), 'error reading dependency'] for index in range(201)]
    values.append([str(now + 3600 * 10**9), 'future error'])

    def respond(request):
        assert 'namespace="applications"' in request.url.params['query']
        assert 'cluster="observed"' in request.url.params['query']
        assert request.url.params['limit'] == '201' and request.url.params['direction'] == 'backward'
        return httpx.Response(200, json={'status': 'success', 'data': {'resultType': 'streams', 'result': [
            {'stream': {'namespace': 'applications', 'pod': 'api-worker', 'cluster': 'observed'}, 'values': values},
            {'stream': {'namespace': 'applications', 'pod': 'other-worker', 'cluster': 'other'}, 'values': values},
        ]}})

    async def collect():
        settings = Settings(namespace='applications', observed_cluster='observed')
        async with httpx.AsyncClient(base_url='http://loki', transport=httpx.MockTransport(respond)) as http:
            return await LokiClient(http, settings).incident_candidates('applications')

    result = asyncio.run(collect())
    assert result.truncated and len(result.entries) == 200 and result.outside_window == 1
    assert {item['pod'] for item in result.entries} == {'api-worker'}


@pytest.mark.parametrize('question', ['Analyse le dernier incident', 'Analyse l’incident le plus récent', 'Analyse la dernière panne'])
@pytest.mark.parametrize('stream', [False, True])
def test_short_prompt_discovers_demo_and_followup_keeps_its_marker_window(question, stream):
    start = time.time_ns() - 400 * 10**9
    phases = ['normal', 'anomaly_start', 'recovered', 'complete']
    stamps = [start + index * 60 * 10**9 for index in range(4)]
    calls = []

    def kubernetes(request):
        assert request.url.path in ('/api/v1/nodes', '/api/v1/pods', '/api/v1/events')
        return httpx.Response(200, json={'metadata': {}, 'items': []})

    def loki(request):
        query = request.url.params['query']
        assert 'namespace="applications"' in query and 'cluster="observed"' in query
        calls.append(query)
        if ' |~ ' not in query and 'KUBEAI_DEMO_INCIDENT' not in query:
            assert int(request.url.params['start']) == start
            assert int(request.url.params['end']) == stamps[-1] + 1
        return httpx.Response(200, json={'status': 'success', 'data': {'resultType': 'streams', 'result': [{
            'stream': {'namespace': 'applications', 'pod': 'kubeai-demo-incident', 'container': 'demo', 'cluster': 'observed'},
            'values': [[str(stamp), 'KUBEAI_DEMO_INCIDENT phase=' + phase] for stamp, phase in zip(stamps, phases)]}]}})

    def mimir(request):
        assert request.url.path.endswith('/query_range')
        assert abs(float(request.url.params['start']) - start / 10**9) < 0.001
        assert abs(float(request.url.params['end']) - stamps[-1] / 10**9) < 0.001
        return httpx.Response(200, json={'status': 'success', 'data': {'resultType': 'matrix', 'result': []}})

    settings = Settings(namespace='applications', observed_cluster='observed', ca_file='', token_file='',
                        llm_api_key_file='', api_key='unit-api-key-0123456789abcdef0123456789')
    app = create_app(settings, kubernetes_transport=httpx.MockTransport(kubernetes),
                     prometheus_transport=httpx.MockTransport(mimir), loki_transport=httpx.MockTransport(loki))

    def content(response):
        assert response.status_code == 200, response.text
        if not stream:
            return response.json()['choices'][0]['message']['content']
        events = [json.loads(line[6:]) for line in response.text.splitlines()
                  if line.startswith('data: ') and line != 'data: [DONE]']
        return ''.join(event['choices'][0]['delta'].get('content', '') for event in events)

    history = [{'role': 'user', 'content': question}]
    with TestClient(app, headers={'Authorization': 'Bearer ' + settings.api_key}) as api:
        first = content(api.post('/v1/chat/completions', json={'messages': history, 'stream': stream}))
        assert 'Chronologie' in first and 'applications/kubeai-demo-incident' in first
        assert '**Recherche automatique :**' in first and '30 jours' in first
        history += [{'role': 'assistant', 'content': first},
                    {'role': 'user', 'content': 'Pour ce même incident, corrèle les logs et les métriques.'}]
        second = content(api.post('/v1/chat/completions', json={'messages': history, 'stream': stream}))
        assert "Corrélation de l'incident" in second
        assert '**Recherche automatique :**' in second
        assert sum(' |~ ' in query for query in calls) == 2
