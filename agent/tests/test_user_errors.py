from app.clients.base import UpstreamError
from app.services.llm_service import parse_action_content
from app.services.tool_agent import ToolAgent
from app.services.user_errors import classify, language_of, user_error


def test_language_detection():
    assert language_of("Quel est l'état de santé de mon cluster ?") == "fr"
    assert language_of("What is the health of my cluster?") == "en"


def test_mistral_error_is_localized_and_correlated():
    error = UpstreamError("llm", "Invalid or incomplete structured function call")
    french = user_error(error, "Quel est l'état du cluster ?")
    english = user_error(error, "What is the cluster health?")
    assert french["code"] == "LLM_INVALID_TOOL_CALL"
    assert english["code"] == "LLM_INVALID_TOOL_CALL"
    assert "modèle IA" in french["message"]
    assert "AI model" in english["message"]
    assert french["diagnostic_id"].startswith("kubeai-")
    assert french["diagnostic_id"] in french["message"]
    assert "Invalid or incomplete" not in french["message"]


def test_source_and_timeout_classification():
    assert classify(UpstreamError("mimir", "timed out"))[0] == "MIMIR_UNAVAILABLE"
    assert classify(UpstreamError("mimir", "HTTP 503"))[0] == "MIMIR_UNAVAILABLE"
    assert classify(UpstreamError("loki", "HTTP 503"))[0] == "LOKI_UNAVAILABLE"
    assert classify(UpstreamError("kubernetes", "forbidden"))[0] == "K8S_FORBIDDEN"
    assert classify(UpstreamError("agent", "timed out"))[0] == "UPSTREAM_TIMEOUT"


def test_action_parser_accepts_fenced_json_only():
    assert parse_action_content('```json\n{"name":"respond","arguments":{"kind":"answer"}}\n```') == {
        "name": "respond", "arguments": {"kind": "answer"},
    }
    assert parse_action_content('{"name":"respond","arguments":{}}') == {
        "name": "respond", "arguments": {},
    }


def test_partial_source_warning_is_explicit_and_localized():
    trace = [{"name": "get_metrics", "result": {
        "error": "source_unavailable", "source": "mimir",
    }}]
    french = ToolAgent.source_warning(None, trace, "Quel est l'état du cluster ?")
    english = ToolAgent.source_warning(None, trace, "What is the cluster health?")
    assert "Diagnostic partiel" in french and "Mimir" in french
    assert "Partial diagnosis" in english and "Mimir" in english
    assert "Mimir" in ToolAgent.source_warning(
        None, [{"name": "get_cluster_inventory", "result": {
            "metrics": {"error": "metrics_unavailable"},
        }}], "Quel est l'état du cluster ?")
