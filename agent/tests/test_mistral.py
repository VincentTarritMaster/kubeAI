"""Offline contract tests for Mistral and the existing Open WebUI API."""
import json
from pathlib import Path

from fastapi.testclient import TestClient
import httpx
import pytest

import app.config as config_module
from app.config import Settings
from app.main import create_app
from app.services.privacy import redact_data, redact_text
from test_api import pod, sample


def make_client(llm_handler, *, key="unit-secret-123456", kube_code=200, mimir_code=200,
                loki_code=200, log_message="application ready"):
    calls = []

    def kube(request):
        return httpx.Response(kube_code, json={"items": [pod()]})

    def mimir(request):
        value = 0.02 if "rate(" in request.url.params["query"] else 104857600
        return httpx.Response(mimir_code, json={"status": "success", "data": {
            "resultType": "vector", "result": [sample(value)],
        }})

    def loki(request):
        return httpx.Response(loki_code, json={"status": "success", "data": {
            "resultType": "streams", "result": [{"stream": {"pod": "web", "container": "app"},
                                            "values": [["1792347000000000000", log_message]]}],
        }})

    def llm(request):
        calls.append(request)
        assert request.url.scheme == "https"
        assert request.url.host == "api.mistral.ai"
        assert request.headers["authorization"] == "Bearer " + key
        if request.method == "POST":
            assert request.url.path == "/v1/chat/completions"
            assert json.loads(request.content)["model"] == "mistral-small-2603"
        return llm_handler(request)

    settings = Settings(ca_file="", token_file="", llm_api_key=key, llm_api_key_file="")
    client = TestClient(create_app(
        settings,
        kubernetes_transport=httpx.MockTransport(kube),
        prometheus_transport=httpx.MockTransport(mimir),
        loki_transport=httpx.MockTransport(loki),
        llm_transport=httpx.MockTransport(llm),
    ))
    return client, calls


def chat_body(*, stream=False, model="kubeai"):
    return {"model": model, "stream": stream,
            "messages": [{"role": "user", "content": "Explique les observations actuelles."}]}


def completion_response(content="### 1. État observé\nPod Ready [K8S]."):
    return httpx.Response(200, json={
        "id": "provider-id", "object": "chat.completion", "model": "mistral-small-2603",
        "choices": [{"index": 0, "message": {"role": "assistant", "content": content},
                     "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
    })


def sse_response(parts):
    frames = [{"choices": [{"index": 0, "delta": {"role": "assistant"}, "finish_reason": None}]}]
    frames += [{"choices": [{"index": 0, "delta": {"content": part}, "finish_reason": None}]}
               for part in parts]
    frames.append({"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]})
    return httpx.Response(200, text="".join("data: " + json.dumps(frame) + "\n\n" for frame in frames)
                          + "data: [DONE]\n\n", headers={"content-type": "text/event-stream"})


def events(response):
    lines = [line[6:] for line in response.text.splitlines() if line.startswith("data: ")]
    return [json.loads(line) if line != "[DONE]" else line for line in lines]


def test_config_defaults_and_secret_is_not_in_repr(monkeypatch):
    monkeypatch.delenv("KUBEAI_ENV", raising=False)
    for name in ("LLM_PROVIDER", "MISTRAL_API_KEY", "MISTRAL_MODEL", "MISTRAL_BASE_URL"):
        monkeypatch.delenv(name, raising=False)
    settings = Settings.from_env()
    assert settings.llm_provider == "mistral-api"
    assert settings.llm_base_url == "https://api.mistral.ai/v1"
    assert settings.llm_model == "mistral-small-2603"
    assert "MISTRAL_API_KEY" not in repr(settings)
    monkeypatch.setenv("MISTRAL_API_KEY", "unit-secret-123456")
    assert "unit-secret-123456" not in repr(Settings.from_env())
    with pytest.raises(ValueError, match="HTTPS"):
        Settings(llm_base_url="http://api.mistral.ai/v1")


def test_development_dotenv_loads_fake_key_without_overriding_environment(tmp_path, monkeypatch):
    dotenv_path = tmp_path / ".env"
    dotenv_path.write_text(
        "LLM_PROVIDER=mistral-api\nMISTRAL_API_KEY=fake-dotenv-key-123456\n"
        "MISTRAL_MODEL=mistral-from-dotenv\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(config_module, "_DEVELOPMENT_DOTENV_PATH", dotenv_path)
    monkeypatch.setenv("KUBEAI_ENV", "development")
    monkeypatch.setenv("MISTRAL_MODEL", "mistral-from-process")
    monkeypatch.delenv("MISTRAL_API_KEY", raising=False)

    settings = Settings.from_env()

    assert settings.llm_provider == "mistral-api"
    assert settings.llm_model == "mistral-from-process"
    assert settings.llm_api_key == "fake-dotenv-key-123456"
    assert "fake-dotenv-key-123456" not in repr(settings)


def test_dotenv_is_not_loaded_without_explicit_development_mode(tmp_path, monkeypatch):
    dotenv_path = tmp_path / ".env"
    dotenv_path.write_text("MISTRAL_API_KEY=fake-dotenv-key-123456\n", encoding="utf-8")
    monkeypatch.setattr(config_module, "_DEVELOPMENT_DOTENV_PATH", dotenv_path)
    monkeypatch.setenv("KUBEAI_ENV", "production")
    monkeypatch.delenv("MISTRAL_API_KEY", raising=False)

    settings = Settings.from_env()

    assert settings.llm_api_key == ""


def test_missing_projected_kubernetes_ca_does_not_block_local_startup(monkeypatch):
    monkeypatch.delenv("KUBEAI_ENV", raising=False)
    monkeypatch.delenv("KUBERNETES_CA_FILE", raising=False)
    settings = Settings.from_env()
    if not Path(Settings.ca_file).is_file():
        assert settings.ca_file == ""


@pytest.mark.parametrize("configured,expected", [
    ("mistral-api", "mistral-api"), ("mistral", "mistral-api"),
    ("local", "local"), ("apertus", "local"),
])
def test_provider_profiles_and_legacy_aliases(monkeypatch, configured, expected):
    monkeypatch.setenv("KUBEAI_ENV", "production")
    monkeypatch.setenv("LLM_PROVIDER", configured)
    monkeypatch.delenv("MISTRAL_API_KEY", raising=False)
    settings = Settings.from_env()
    assert settings.llm_provider == expected
    if expected == "local":
        assert settings.llm_base_url == "http://127.0.0.1:11435/v1"
        assert settings.llm_model == "apertus-8b"
        assert settings.llm_api_key_file == ""
        assert settings.llm_api_key == ""
    else:
        assert settings.llm_base_url == "https://api.mistral.ai/v1"


def test_invalid_provider_is_explicit():
    with pytest.raises(ValueError, match="mistral-api ou local"):
        Settings(llm_provider="unknown")


def test_selecting_local_directly_does_not_inherit_mistral_defaults():
    settings = Settings(llm_provider="local")
    assert settings.llm_base_url == "http://127.0.0.1:11435/v1"
    assert settings.llm_model == "apertus-8b"
    assert settings.llm_api_key_file == ""
    assert settings.llm_timeout == 180


def test_secret_file_fallback_and_environment_priority(tmp_path):
    path = tmp_path / "api-key"
    path.write_text("file-key-123456\n", encoding="utf-8")
    assert Settings(llm_api_key_file=str(path)).llm_key() == "file-key-123456"
    assert Settings(llm_api_key="env-key-123456", llm_api_key_file=str(path)).llm_key() == "env-key-123456"


def test_json_mistral_and_openwebui_model_contract():
    client, calls = make_client(lambda request: completion_response())
    with client:
        models = client.get("/v1/models").json()["data"]
        assert models[0]["id"] == "kubeai"
        assert {item["id"] for item in models} == {"kubeai", "kubeai-apertus"}
        response = client.post("/v1/chat/completions", json=chat_body())
        assert response.status_code == 200
        body = response.json()
        assert body["object"] == "chat.completion"
        assert body["model"] == "kubeai"
        assert body["choices"][0]["message"]["role"] == "assistant"
        assert "Sources disponibles" in body["choices"][0]["message"]["content"]
        assert body["usage"]["total_tokens"] == 15
        assert len(calls) == 1
        payload = json.loads(calls[0].content)
        assert payload["messages"][0]["role"] == "system"
        assert "Réponds en français" in payload["messages"][0]["content"]
        assert payload["messages"][1]["role"] == "user"
        assert len(payload["messages"]) == 2


def test_provider_status_uses_mocked_models_endpoint():
    def handler(request):
        assert request.method == "GET"
        assert request.url.path == "/v1/models"
        return httpx.Response(200, json={"data": [{"id": "mistral-small-2603"}]})

    client, calls = make_client(handler)
    with client:
        result = client.get("/llm/status")
    assert result.status_code == 200
    assert result.json()["provider"] == "mistral-api"
    assert result.json()["model"] == "mistral-small-2603"
    assert len(calls) == 1


def test_local_profile_keeps_same_openwebui_model_and_needs_no_mistral_key():
    calls = []

    def local_handler(request):
        calls.append(request)
        assert request.url.host == "127.0.0.1"
        assert "authorization" not in request.headers
        if request.method == "GET":
            assert request.url.path == "/v1/models"
            return httpx.Response(200, json={"data": [{"id": "apertus-8b"}]})
        assert request.url.path == "/v1/chat/completions"
        payload = json.loads(request.content)
        assert payload["model"] == "apertus-8b"
        return sse_response(["Apertus local répond\n"]) if payload.get("stream") else completion_response()

    settings = Settings(ca_file="", token_file="", llm_provider="local", llm_base_url="http://127.0.0.1:11435/v1",
                        llm_model="apertus-8b", llm_api_key_file="")
    client = TestClient(create_app(settings,
                                   kubernetes_transport=httpx.MockTransport(lambda request: httpx.Response(200, json={"items": [pod()]})),
                                   prometheus_transport=httpx.MockTransport(lambda request: httpx.Response(503)),
                                   loki_transport=httpx.MockTransport(lambda request: httpx.Response(503)),
                                   llm_transport=httpx.MockTransport(local_handler)))
    with client:
        listed = client.get("/v1/models").json()["data"]
        assert {item["id"] for item in listed} == {"kubeai", "kubeai-apertus"}
        assert listed[0]["name"] == "kubeAI · Apertus local"
        ready = client.get("/llm/status")
        assert ready.status_code == 200
        assert ready.json()["provider"] == "local"
        assert ready.json()["hosting"] == "configured_endpoint"
        response = client.post("/v1/chat/completions", json=chat_body())
        assert response.status_code == 200
        assert response.json()["model"] == "kubeai"
        streamed = client.post("/v1/chat/completions", json=chat_body(stream=True))
        assert streamed.status_code == 200
        assert "data: [DONE]" in streamed.text
        streamed_chunks = events(streamed)
        assert all(item == "[DONE]" or item["model"] == "kubeai" for item in streamed_chunks)
    assert len(calls) == 3


def test_local_profile_failure_is_explicit_and_does_not_expose_upstream_details():
    def local_failure(request):
        if request.method == "GET":
            return httpx.Response(429, json={"detail": "private-upstream-token"})
        raise httpx.ConnectError("private host address", request=request)

    settings = Settings(ca_file="", token_file="", llm_provider="local",
                        llm_base_url="http://127.0.0.1:11435/v1", llm_model="apertus-8b",
                        llm_api_key_file="")
    client = TestClient(create_app(settings,
                                   kubernetes_transport=httpx.MockTransport(lambda request: httpx.Response(200, json={"items": [pod()]})),
                                   prometheus_transport=httpx.MockTransport(lambda request: httpx.Response(503)),
                                   loki_transport=httpx.MockTransport(lambda request: httpx.Response(503)),
                                   llm_transport=httpx.MockTransport(local_failure)))
    with client:
        status = client.get("/llm/status")
        assert status.status_code == 503
        assert "Quota ou limite de requêtes Apertus local" in status.json()["detail"]["message"]
        response = client.post("/v1/chat/completions", json=chat_body())
        assert response.status_code == 503
        assert "Connexion au service Apertus local impossible" in response.json()["detail"]["message"]
        assert "private-upstream-token" not in response.text


def test_legacy_virtual_model_alias_uses_current_provider():
    client, calls = make_client(lambda request: completion_response())
    with client:
        response = client.post("/v1/chat/completions", json=chat_body(model="kubeai-apertus"))
    assert response.status_code == 200
    assert response.json()["model"] == "kubeai-apertus"
    assert len(calls) == 1


def test_sse_progressive_output_and_split_secret_are_sanitized():
    key = "unit-secret-123456"
    client, calls = make_client(lambda request: sse_response(
        ["### 1. État observé\n", "Le pod web est Ready [K8S].\n", "Clé vue : ",
         key[:8], key[8:] + "\n", "### 2. Preuves\n"]), key=key)
    with client:
        response = client.post("/v1/chat/completions", json=chat_body(stream=True))
    assert response.status_code == 200
    assert "text/event-stream" in response.headers["content-type"]
    assert key not in response.text
    assert "[MASQUÉ]" in response.text
    chunks = events(response)
    assert chunks[-1] == "[DONE]"
    assert all(item["model"] == "kubeai" for item in chunks[:-1])
    assert chunks[-2]["choices"][0]["finish_reason"] == "stop"
    assert "Sources disponibles" in chunks[-3]["choices"][0]["delta"]["content"]
    assert json.loads(calls[0].content)["stream"] is True


def test_missing_key_is_explicit_and_does_not_contact_provider():
    client, calls = make_client(lambda request: pytest.fail("provider contacted"), key="")
    with client:
        for streaming in (False, True):
            response = client.post("/v1/chat/completions", json=chat_body(stream=streaming))
            assert response.status_code == 503
            assert "Clé Mistral manquante" in response.json()["detail"]["message"]
        assert client.get("/cluster/status").status_code == 200
    assert calls == []


def test_deterministic_health_is_french_structured_and_needs_no_key():
    client, calls = make_client(lambda request: pytest.fail("provider contacted"), key="", mimir_code=503)
    with client:
        response = client.post("/diagnose", json={"question": "Quel est l'état de santé du namespace kubeai ?"})
    assert response.status_code == 200
    body = response.json()
    answer = body["answer"]
    for heading in ("État observé", "Preuves et sources", "Hypothèses de cause",
                    "Informations manquantes", "Vérifications recommandées"):
        assert heading in answer
    assert body["observation"]["observed_at"] in answer
    assert "[K8S]" in answer
    assert "partielles" in answer
    assert calls == []


@pytest.mark.parametrize("status,phrase", [(401, "Authentification"), (403, "Authentification"),
                                           (429, "Quota"), (500, "indisponible"), (503, "indisponible")])
def test_provider_http_errors_have_sanitized_messages(status, phrase):
    client, _ = make_client(lambda request: httpx.Response(status, json={"detail": "private-token-123"}))
    with client:
        for streaming in (False, True):
            response = client.post("/v1/chat/completions", json=chat_body(stream=streaming))
            assert response.status_code == 503
            assert phrase in response.json()["detail"]["message"]
            assert "private-token-123" not in response.text


@pytest.mark.parametrize("error,phrase", [(httpx.ReadTimeout, "Délai"), (httpx.ConnectError, "Connexion")])
def test_timeout_or_connection_failure(error, phrase):
    def handler(request):
        raise error("transport details with unit-secret-123456", request=request)

    client, _ = make_client(handler)
    with client:
        response = client.post("/v1/chat/completions", json=chat_body())
    assert response.status_code == 503
    assert phrase in response.json()["detail"]["message"]
    assert "unit-secret-123456" not in response.text


@pytest.mark.parametrize("response", [httpx.Response(200, text="not-json"),
                                     httpx.Response(200, json={"choices": []}),
                                     httpx.Response(200, json={"choices": [{"message": {"content": ""}}]}),
                                     httpx.Response(200, json={"choices": [{"message": {"content": None}}]})])
def test_invalid_json_completion_is_explicit(response):
    client, _ = make_client(lambda request: response)
    with client:
        result = client.post("/v1/chat/completions", json=chat_body())
    assert result.status_code == 503
    assert "JSON" in result.json()["detail"]["message"]


@pytest.mark.parametrize("body", ["data: not-json\n\n", "data: [DONE]\n\n",
                                  'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}\n\n',
                                  'data: {"choices":[{"delta":{"content":42},"finish_reason":null}]}\n\n'])
def test_malformed_or_incomplete_sse_is_signalled(body):
    client, _ = make_client(lambda request: httpx.Response(200, text=body))
    with client:
        result = client.post("/v1/chat/completions", json=chat_body(stream=True))
    assert result.status_code == 200
    frames = events(result)
    assert frames[-1] == "[DONE]"
    assert "error" in frames[-2]
    assert all("finish_reason" not in frame or frame["choices"][0]["finish_reason"] is None
               for frame in frames[:-2])


class BrokenStream(httpx.AsyncByteStream):
    async def __aiter__(self):
        yield b'data: {"choices":[{"delta":{"content":"Debut\\n"},"finish_reason":null}]}\n\n'
        raise httpx.ReadError("broken connection with unit-secret-123456")


def test_sse_connection_break_is_signalled_without_secret():
    client, _ = make_client(lambda request: httpx.Response(200, stream=BrokenStream()))
    with client:
        result = client.post("/v1/chat/completions", json=chat_body(stream=True))
    assert result.status_code == 200
    assert "error" in events(result)[-2]
    assert "unit-secret-123456" not in result.text


def test_partial_monitoring_still_states_missing_evidence():
    client, calls = make_client(lambda request: completion_response(), mimir_code=503, loki_code=503)
    with client:
        result = client.post("/diagnose", json={"question": "Explique les observations."})
    assert result.status_code == 200
    assert result.json()["observation"]["data_status"] == "partial"
    assert result.json()["observation"]["sources"]["mimir"]["status"] == "unavailable"
    assert result.json()["observation"]["sources"]["loki"]["status"] == "unavailable"
    assert "Données partielles" in result.json()["answer"]
    context = json.loads(json.loads(calls[0].content)["messages"][-1]["content"].split(
        "\n\nOBSERVATION_JSON", 1)[1].split(":\n", 1)[1].split("\n\nCURRENT_KUBERNETES_STATE", 1)[0])
    assert context["pods"][0]["metrics"]["cpu_cores"] is None
    assert context["logs"] == []


def test_kubernetes_unavailable_prevents_provider_call():
    client, calls = make_client(lambda request: pytest.fail("provider contacted"), kube_code=403)
    with client:
        response = client.post("/v1/chat/completions", json=chat_body())
    assert response.status_code == 503
    assert calls == []


def test_detectable_secrets_in_observations_and_question_are_masked():
    key = "unit-secret-123456"
    seen = []

    def handler(request):
        seen.append(json.loads(request.content))
        return completion_response("Le contact alice@example.org et 10.2.3.4 sont masqués.")

    client, _ = make_client(handler, key=key,
                            log_message=f"Bearer abcDEF123, password=guess, {key}, alice@example.org, 10.2.3.4")
    with client:
        response = client.post("/v1/chat/completions", json={"model": "kubeai", "messages": [
            {"role": "user", "content": "Explique cette clé " + key},
        ]})
    assert response.status_code == 200
    payload_text = json.dumps(seen[0], ensure_ascii=False)
    assert key not in payload_text
    assert "abcDEF123" not in payload_text
    assert "guess" not in payload_text
    assert "alice@example.org" not in payload_text
    assert "10.2.3.4" not in payload_text
    assert "alice@example.org" not in response.text
    assert "10.2.3.4" not in response.text
    assert "[MASQUÉ]" in payload_text


def test_redaction_preserves_evidence_timestamps_and_does_not_mutate():
    original = {"pod": "web", "timestamp_ns": "1792347000000000000", "patient_id": "1234",
                "accessToken": "token-secret",
                "log": "token=abc123 alice@example.org 10.2.3.4"}
    cleaned = redact_data(original)
    assert original["patient_id"] == "1234"
    assert cleaned["pod"] == "web"
    assert cleaned["timestamp_ns"] == original["timestamp_ns"]
    assert cleaned["patient_id"] == "[MASQUÉ]"
    assert cleaned["accessToken"] == "[MASQUÉ]"
    assert "abc123" not in cleaned["log"]
    assert redact_text("Bearer abcDEF123") == "Bearer [MASQUÉ]"
    assert "token-secret" not in redact_text('{"accessToken":"token-secret"}')
