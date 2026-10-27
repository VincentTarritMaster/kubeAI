"""Copied French prompts must select the same historical route as typed ones."""
import json
import time

from fastapi.testclient import TestClient
import httpx
import pytest

from app.api.chat import diagnostic_route
from app.config import Settings
from app.main import create_app
from app.models import ChatRequest


QUESTION = ('Analyse l’incident du dernier script de démonstration. Retrouve ses logs dans Loki '
            'et reconstitue les phases normal, anomaly_start, recovered et complete.')
FOLLOWUP = 'Pour ce même incident, corrèle les logs, le CPU et les erreurs HTTP.'


@pytest.mark.parametrize('apostrophe', ["'", '’', '‘', 'ʼ', '＇'])
def test_typographic_apostrophes_preserve_incident_route_and_followup(apostrophe):
    question = QUESTION.replace('’', apostrophe)
    history = [{'role': 'user', 'content': question}]
    assert diagnostic_route(ChatRequest(messages=history)) == 'incident'
    history += [{'role': 'assistant', 'content': 'Le namespace est healthy ; le pod a terminé.'},
                {'role': 'user', 'content': FOLLOWUP}]
    chat = ChatRequest(messages=history)
    assert diagnostic_route(chat) == 'correlation'
    assert question in chat._dialogue.incident_turns


@pytest.mark.parametrize('stream', [False, True])
def test_exact_copied_prompt_and_correlation_read_the_same_loki_episode(stream):
    start = time.time_ns() - 300 * 10**9
    phases = ['normal', 'anomaly_start', 'recovered', 'complete']
    stamps = [start + index * 60 * 10**9 for index in range(4)]
    queries = []

    def kubernetes(request):
        assert request.url.path in ('/api/v1/nodes', '/api/v1/pods', '/api/v1/events')
        return httpx.Response(200, json={'metadata': {}, 'items': []})

    def loki(request):
        query = request.url.params['query']
        assert 'namespace="applications"' in query and 'cluster="observed"' in query
        queries.append(query)
        if 'KUBEAI_DEMO_INCIDENT' not in query:
            assert int(request.url.params['start']) == start
            assert int(request.url.params['end']) == stamps[-1] + 1
        return httpx.Response(200, json={'status': 'success', 'data': {'resultType': 'streams', 'result': [{
            'stream': {'namespace': 'applications', 'pod': 'kubeai-demo-incident', 'container': 'demo'},
            'values': [[str(stamp), 'KUBEAI_DEMO_INCIDENT phase=' + phase]
                       for stamp, phase in zip(stamps, phases)]}]}})

    def mimir(request):
        assert request.url.path.endswith('/query_range')
        assert abs(float(request.url.params['start']) - start / 10**9) < 0.001
        assert abs(float(request.url.params['end']) - stamps[-1] / 10**9) < 0.001
        assert 'cluster="observed"' in request.url.params['query']
        return httpx.Response(200, json={'status': 'success', 'data': {'resultType': 'matrix', 'result': []}})

    def no_freeform_model(request):
        pytest.fail('The incident must use historical observations, not the generic current-state chat.')

    settings = Settings(namespace='applications', observed_cluster='observed', ca_file='', token_file='',
                        llm_api_key_file='', api_key='unit-api-key-0123456789abcdef0123456789')
    app = create_app(settings, kubernetes_transport=httpx.MockTransport(kubernetes),
                     prometheus_transport=httpx.MockTransport(mimir), loki_transport=httpx.MockTransport(loki),
                     llm_transport=httpx.MockTransport(no_freeform_model))

    def answer(response):
        assert response.status_code == 200, response.text
        if not stream:
            return response.json()['choices'][0]['message']['content']
        chunks = [json.loads(line[6:]) for line in response.text.splitlines()
                  if line.startswith('data: ') and line != 'data: [DONE]']
        return ''.join(chunk['choices'][0]['delta'].get('content', '') for chunk in chunks)

    history = [{'role': 'user', 'content': QUESTION}]
    with TestClient(app, headers={'Authorization': 'Bearer ' + settings.api_key}) as api:
        first = answer(api.post('/v1/chat/completions', json={'messages': history, 'stream': stream}))
        assert 'Chronologie' in first and 'applications/kubeai-demo-incident' in first
        for phase in phases:
            assert 'phase=' + phase in first
        history += [{'role': 'assistant', 'content': first}, {'role': 'user', 'content': FOLLOWUP}]
        second = answer(api.post('/v1/chat/completions', json={'messages': history, 'stream': stream}))
        assert 'Corrélation de l’incident' in second or "Corrélation de l'incident" in second
        assert 'applications/kubeai-demo-incident' in second
        assert sum('KUBEAI_DEMO_INCIDENT' in query for query in queries) == 2
