"""The model, not a keyword classifier, selects read-only functions and the reply language."""
import json
import asyncio
from datetime import datetime, timezone
from unittest.mock import patch

from fastapi.testclient import TestClient
import httpx
import pytest

from app.config import Settings
from app.main import create_app
from app.services.observability_tools import bounded_result
from app.services.observability_tools import ObservabilityTools
from app.services.tool_agent import ToolAgent


KEY = 'unit-tool-agent-key-0123456789abcdef0123456789'


def function(name, arguments, index=0, reason='tool_calls'):
    return httpx.Response(200, json={'choices': [{'finish_reason': reason, 'message': {
        'role': 'assistant', 'content': None, 'tool_calls': [{'id': 'call-' + str(index), 'type': 'function',
        'function': {'name': name, 'arguments': json.dumps(arguments)}}]}}]})


def client(decisions, protocol='native', kube_pods=None):
    seen, reads = [], []

    def model(request):
        payload = json.loads(request.content)
        if protocol == 'native':
            assert payload['tool_choice'] == 'required'
            assert payload['parallel_tool_calls'] is False
            assert 'tools' in payload
        else:
            assert payload['response_format']['type'] == 'json_schema'
            assert 'tools' not in payload
        seen.append(payload)
        return decisions[len(seen) - 1] if len(seen) <= len(decisions) else decisions[-1]

    def kube(request):
        reads.append(('kubernetes', request.url.path))
        items = kube_pods if request.url.path.endswith('/pods') and kube_pods is not None else []
        return httpx.Response(200, json={'metadata': {}, 'items': items})

    def mimir(request):
        reads.append(('mimir', request.url.path))
        return httpx.Response(200, json={'status': 'success', 'data': {
            'resultType': 'matrix' if 'query_range' in request.url.path else 'vector', 'result': []}})

    def loki(request):
        reads.append(('loki', request.url.path))
        return httpx.Response(200, json={'status': 'success', 'data': {'resultType': 'streams', 'result': []}})

    settings = Settings(chat_mode='agent', agent_tool_protocol=protocol, namespace='applications', observed_cluster='observed',
                        ca_file='', token_file='', llm_provider='local', llm_api_key_file='', api_key=KEY)
    app = create_app(settings, kubernetes_transport=httpx.MockTransport(kube),
                     prometheus_transport=httpx.MockTransport(mimir), loki_transport=httpx.MockTransport(loki),
                     llm_transport=httpx.MockTransport(model))
    return TestClient(app, headers={'Authorization': 'Bearer ' + KEY}), seen, reads


def text(response, stream):
    assert response.status_code == 200, response.text
    if not stream:
        return response.json()['choices'][0]['message']['content']
    events = [json.loads(line[6:]) for line in response.text.splitlines()
              if line.startswith('data: ') and line != 'data: [DONE]']
    assert all('error' not in event for event in events), events
    return ''.join(event['choices'][0]['delta'].get('content', '') for event in events)


@pytest.mark.parametrize('stream', [False, True])
@pytest.mark.parametrize('question,answer', [
    ('Could you take a look? Something seems off with our apps.', 'No pods are currently present in applications; health cannot be inferred from that alone.'),
    ('Tu peux regarder ce qui se passe côté applications ?', 'Aucun pod présent ; cela ne suffit pas à conclure sur la santé applicative.'),
])
def test_model_selects_tools_then_answers_without_keyword_routing(stream, question, answer):
    api, seen, reads = client([
        function('get_cluster_inventory', {'scope': 'namespace'}),
        function('get_logs', {'namespace': 'applications'}),
        function('respond', {'kind': 'answer', 'answer': answer}),
    ])
    with patch('app.api.chat.diagnostic_route', side_effect=AssertionError('Legacy router called')), \
            patch('app.models.replay', side_effect=AssertionError('Keyword conversation analysis called')), api:
        response = api.post('/v1/chat/completions', json={'messages': [{'role': 'user', 'content': question}], 'stream': stream})
        assert text(response, stream) == answer
    assert any(source == 'kubernetes' for source, _ in reads)
    assert any(source == 'loki' for source, _ in reads)
    assert seen[0]['messages'][-1]['content'] == question
    assert 'language of the latest user question' in seen[0]['messages'][0]['content']
    assert any(message['role'] == 'tool' for message in seen[-1]['messages'])
    assert 'untrusted DATA' in seen[0]['messages'][0]['content']


def test_final_monitoring_claim_requires_fresh_evidence():
    api, seen, reads = client([
        function('respond', {'kind': 'answer', 'answer': 'Everything is healthy.'}),
        function('get_cluster_inventory', {}),
        function('respond', {'kind': 'answer', 'answer': 'The inventory is empty; no health conclusion is supported.'}),
    ])
    with api:
        result = api.post('/v1/chat/completions', json={'messages': [{'role': 'user', 'content': 'How are things looking?'}]})
    assert 'Everything is healthy' not in text(result, False)
    assert reads and len(seen) == 3
    assert 'Read fresh evidence' in json.dumps(seen[1]['messages'])


@pytest.mark.parametrize('stream', [False, True])
def test_populated_inventory_count_is_not_a_resource_list(stream):
    answer = 'Un pod est présent ; les logs récents ne contiennent aucune entrée.'
    api, _, reads = client([
        function('get_cluster_inventory', {'scope': 'cluster'}),
        function('get_logs', {'namespace': 'applications', 'pod': 'real-application'}),
        function('respond', {'kind': 'answer', 'answer': answer}),
    ], kube_pods=[{'metadata': {'name': 'real-application', 'namespace': 'applications'},
                  'status': {'phase': 'Running', 'containerStatuses': [],
                             'conditions': [{'type': 'Ready', 'status': 'True'}]}}])
    with api:
        response = api.post('/v1/chat/completions', json={'messages': [{'role': 'user', 'content':
            'Quels pods ont actuellement un problème ? Vérifie leur état, les redémarrages et les logs récents.'}],
            'stream': stream})
    actual = text(response, stream)
    assert actual.split('\n\n---\n')[0] == answer
    assert '`applications/real-application`' in actual
    assert ('loki', '/loki/api/v1/query_range') in reads


def test_resource_provenance_ignores_summary_counts_and_source_status_objects():
    agent = object.__new__(ToolAgent)
    agent.settings = Settings(namespace='applications')
    known = set()
    agent.remember_targets({'summary': {'pods': 12}, 'sources': {'pods': {'status': 'ok'}},
                            'pods': [{'namespace': 'applications', 'name': 'actual-pod'}]}, known)
    assert known == {('applications', 'actual-pod')}


def test_unexpected_streaming_failure_is_logged_without_exposing_credentials(caplog):
    api, _, _ = client([])
    with patch('app.api.chat.ToolAgent.run', side_effect=RuntimeError('Failure with Bearer ' + KEY)), api:
        response = api.post('/v1/chat/completions', json={'messages': [{'role': 'user', 'content': 'Check the cluster.'}],
                                                       'stream': True})
    assert 'internal error' in response.text and 'data: [DONE]' in response.text
    assert 'RuntimeError' in caplog.text and 'Agent streaming failed' in caplog.text
    assert KEY not in caplog.text and KEY not in response.text


@pytest.mark.parametrize('name,args', [
    ('delete_pod', {'name': 'api-worker'}),
    ('get_logs', {'namespace': '../../secrets'}),
    ('get_metrics', {'query': 'arbitrary query'}),
    ('get_logs', {'start': '2026-10-19T10:00:00Z', 'end': '2026-10-21T10:00:00Z'}),
])
def test_invalid_or_write_calls_are_never_executed(name, args):
    api, seen, reads = client([
        function(name, args), function('respond', {'kind': 'clarification', 'answer': 'Please narrow the target or time window.'}),
    ])
    with api:
        result = api.post('/v1/chat/completions', json={'messages': [{'role': 'user', 'content': 'Please investigate.'}]})
    assert result.status_code == 200 and not reads
    assert 'invalid_tool_call' in json.dumps(seen[1]['messages'])


def test_model_can_decline_unrelated_request_without_reading_sources():
    api, _, reads = client([function('respond', {'kind': 'out_of_scope', 'answer': 'I can help with Kubernetes observability, not sports.'})])
    with api:
        result = api.post('/diagnose', json={'question': 'Who won the match?'})
    assert result.status_code == 200 and not reads
    assert result.json()['observation']['status'] == 'out_of_scope'


def test_tool_loop_is_bounded_and_repeated_read_uses_request_local_cache():
    api, seen, reads = client([function('get_logs', {})])
    with api:
        result = api.post('/v1/chat/completions', json={'messages': [{'role': 'user', 'content': 'Investigate.'}]})
    assert result.status_code == 503 and len(seen) == 8
    assert reads == [('loki', '/loki/api/v1/query_range')]


def test_truncated_model_call_does_not_execute_a_function():
    api, _, reads = client([function('get_logs', {}, reason='length')])
    with api:
        result = api.post('/v1/chat/completions', json={'messages': [{'role': 'user', 'content': 'Investigate.'}]})
    assert result.status_code == 503 and not reads


def test_bounded_results_are_valid_json_redacted_and_explicit_about_truncation():
    result = bounded_result({'logs': [{'message': 'secret-value ' + 'x' * 400, 'timestamp': index} for index in range(100)]},
                            ('secret-value',), limit=1500)
    encoded = json.dumps(result)
    assert len(encoded) < 1700 and 'secret-value' not in encoded and result['truncated']


def test_context_trimming_preserves_actual_user_question():
    agent = object.__new__(ToolAgent)
    agent.tools = type('Tools', (), {'schemas': []})()
    question = {'role': 'user', 'content': 'The actual user question'}
    messages = [{'role': 'system', 'content': 'trusted'}, {'role': 'assistant', 'content': 'old ' * 10000},
                question, {'role': 'assistant', 'tool_calls': [{'id': 'call-1'}]},
                {'role': 'tool', 'content': json.dumps({'logs': ['x' * 400] * 100})}]
    agent.bound_context(messages, question)
    assert any(item is question for item in messages)
    assert messages[0]['role'] == 'system'


def test_running_environment_defaults_to_agent_mode(monkeypatch):
    monkeypatch.delenv('KUBEAI_CHAT_MODE', raising=False)
    assert Settings.from_env().chat_mode == 'agent'


def test_invented_pod_argument_is_rejected_before_any_source_read():
    api, seen, reads = client([
        function('find_latest_incident', {'namespace': 'applications', 'pod': 'myapp-invented'}),
        function('respond', {'kind': 'clarification', 'answer': 'Which application do you want to inspect?'}),
    ])
    with api:
        result = api.post('/diagnose', json={'question': 'Peux-tu comprendre ce qui a déraillé ?'})
    assert result.status_code == 200 and not reads
    assert 'Unverified pod name' in json.dumps(seen[1]['messages'])


def test_pod_explicitly_named_by_user_can_be_read_without_inventory():
    api, _, reads = client([
        function('get_logs', {'namespace': 'applications', 'pod': 'api-worker'}),
        function('respond', {'kind': 'answer', 'answer': 'No retained logs in this window; this does not prove health.'}),
    ])
    with api:
        result = api.post('/diagnose', json={'question': 'What did api-worker write recently?'})
    assert result.status_code == 200 and reads == [('loki', '/loki/api/v1/query_range')]


def test_latest_discovery_verifies_target_for_following_evidence_tool():
    async def discovered(self, namespace=None, pod=None):
        return {'status': 'found', 'namespace': 'applications', 'pod': 'verified-worker',
                'start': '2026-10-27T10:00:00Z', 'end': '2026-10-27T10:04:00Z'}

    api, _, reads = client([
        function('find_latest_incident', {}),
        function('get_incident_evidence', {'namespace': 'applications', 'pod': 'verified-worker',
                 'start': '2026-10-27T10:00:00Z', 'end': '2026-10-27T10:04:00Z'}),
        function('respond', {'kind': 'answer', 'answer': 'The window has no retained evidence; no cause is established.'}),
    ])
    with patch('app.services.observability_tools.ObservabilityTools.latest', discovered), api:
        result = api.post('/diagnose', json={'question': 'What went wrong most recently?'})
    assert result.status_code == 200, result.text
    assert [item['name'] for item in result.json()['observation']['tools']] == ['find_latest_incident', 'get_incident_evidence']
    assert any(source == 'mimir' for source, _ in reads)
    assert '`applications/verified-worker`' in result.json()['answer']
    assert '2026-10-27T10:04:00' in result.json()['answer']


def test_discovered_window_rounds_outward_to_keep_all_marker_lines():
    async def discovered(*args):
        return {'namespace': 'applications', 'pod': 'real-worker', 'search_note': 'actual markers',
                'start': datetime(2026, 10, 27, 10, 0, 0, 950000, tzinfo=timezone.utc),
                'end': datetime(2026, 10, 27, 10, 4, 0, 65000, tzinfo=timezone.utc)}, None, None
    api, _, _ = client([])
    with api, patch('app.services.observability_tools.discover', discovered):
        result = asyncio.run(ObservabilityTools(api.app.state.diagnosis).latest())
    assert result['start'] == datetime(2026, 10, 27, 10, 0, tzinfo=timezone.utc)
    assert result['end'] == datetime(2026, 10, 27, 10, 4, 1, tzinfo=timezone.utc)


def structured(name, arguments, reason='stop'):
    return httpx.Response(200, json={'choices': [{'finish_reason': reason, 'message': {
        'role': 'assistant', 'content': json.dumps({'name': name, 'arguments': arguments})}}]})


def test_local_json_protocol_keeps_model_tool_selection_and_real_results():
    api, seen, reads = client([
        structured('get_logs', {'namespace': 'applications'}),
        structured('respond', {'kind': 'answer', 'answer': 'No retained log entries in this window.'}),
    ], protocol='auto')
    with patch('app.api.chat.diagnostic_route', side_effect=AssertionError('Keyword router called')), api:
        response = api.post('/diagnose', json={'question': 'Could you check what the application wrote?'})
    assert response.status_code == 200
    assert reads == [('loki', '/loki/api/v1/query_range')]
    assert response.json()['observation']['tools'][0]['name'] == 'get_logs'
    assert any('FUNCTION RESULT' in message['content'] for message in seen[1]['messages'])
    assert 'REAL USER REQUEST (quoted data)' in seen[1]['messages'][0]['content']
    assert 'Could you check what the application wrote?' in seen[1]['messages'][0]['content']
    assert all('tool_calls' not in message and message['role'] != 'tool' for message in seen[1]['messages'])
    names = {branch['properties']['name']['const'] for branch in seen[0]['response_format']['json_schema']['schema']['anyOf']}
    assert names == {'get_cluster_inventory', 'find_latest_incident', 'get_logs', 'get_metrics',
                     'get_events', 'get_incident_evidence', 'respond'}


@pytest.mark.parametrize('decision', [
    structured('get_logs', {}, reason='length'),
    structured('delete_pod', {}),
    httpx.Response(200, json={'choices': [{'message': {'content': '{"name":"get_logs","name":"get_metrics","arguments":{}}'}}]}),
])
def test_invalid_structured_output_cannot_execute_tools(decision):
    api, _, reads = client([decision], protocol='auto')
    with api:
        response = api.post('/diagnose', json={'question': 'Investigate.'})
    assert response.status_code == 503 and not reads
