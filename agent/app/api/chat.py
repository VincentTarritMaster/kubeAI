"""OpenAI-compatible chat surface for Open WebUI and namespace diagnosis."""
import json
import time
import uuid

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
import httpx

from app.models import ChatMessage, ChatRequest, DiagnosisRequest
from app.services.diagnosis import CHAT_MODEL, source_note
from app.services.source_notes import SourceNoteFilter, filter_source_notes
from app.services.health_answer import health_answer
from app.services.privacy import redact_text

router = APIRouter()
LEGACY_MODEL = "kubeai-apertus"


@router.get("/v1/models", tags=["chat"], operation_id="list_chat_models")
async def models(request: Request):
    provider = request.app.state.diagnosis.llm.settings.llm_provider
    name = "kubeAI · Mistral API" if provider == "mistral" else "kubeAI · Apertus local"
    return {"object": "list", "data": [
        {"id": CHAT_MODEL, "object": "model", "created": 0, "owned_by": "kubeai", "name": name},
        {"id": LEGACY_MODEL, "object": "model", "created": 0, "owned_by": "kubeai",
         "name": name + " (identifiant historique)"},
    ]}


@router.get("/llm/status", tags=["health"], operation_id="get_llm_status")
async def llm_status(request: Request):
    """Check configured model availability; this endpoint contacts the provider."""
    return await request.app.state.diagnosis.llm.status()


def completion(content, model=CHAT_MODEL):
    return {"id": "chatcmpl-" + uuid.uuid4().hex, "object": "chat.completion", "created": int(time.time()),
            "model": model, "choices": [{"index": 0, "message": {"role": "assistant", "content": content},
                                   "finish_reason": "stop"}]}


async def finish_nonstream(service, chat):
    payload, observation = await service.prepare(chat)
    grounded = health_answer(chat, observation)
    if grounded is not None:
        answer = completion(grounded, chat.model)
    else:
        upstream = await service.llm.complete(payload)
        answer = completion(upstream["choices"][0]["message"]["content"], chat.model)
        usage = upstream.get("usage")
        if isinstance(usage, dict) and all(isinstance(usage.get(key), int) and usage[key] >= 0
                                            for key in ("prompt_tokens", "completion_tokens", "total_tokens")):
            answer["usage"] = {key: usage[key] for key in ("prompt_tokens", "completion_tokens", "total_tokens")}
    content = answer["choices"][0]["message"]["content"]
    clean = redact_text(filter_source_notes(content), service.sensitive_values())
    answer["choices"][0]["message"]["content"] = clean + source_note(observation)
    answer["model"] = chat.model
    return answer, observation


@router.post("/diagnose", tags=["diagnosis"], operation_id="diagnose_namespace")
async def diagnose(body: DiagnosisRequest, request: Request):
    chat = ChatRequest(messages=[ChatMessage(role="user", content=body.question)])
    answer, observation = await finish_nonstream(request.app.state.diagnosis, chat)
    return {"model": CHAT_MODEL, "answer": answer["choices"][0]["message"]["content"],
            "observation": observation, "usage": answer.get("usage")}


class _SafeLines:
    """Buffer until a newline so a secret split across provider chunks is never emitted."""

    def __init__(self, sensitive_values):
        self.pending = ""
        self.sensitive_values = sensitive_values

    def feed(self, content):
        self.pending += content
        if len(self.pending) > 65536:
            raise ValueError("Ligne de réponse trop longue")
        lines = []
        while "\n" in self.pending:
            line, self.pending = self.pending.split("\n", 1)
            lines.append(redact_text(line + "\n", self.sensitive_values))
        return "".join(lines)

    def finish(self):
        content, self.pending = self.pending, ""
        return redact_text(content, self.sensitive_values)


def _event(common, *, content=None, role=None, finish_reason=None):
    delta = {}
    if role:
        delta["role"] = role
    if content is not None:
        delta["content"] = content
    chunk = {**common, "choices": [{"index": 0, "delta": delta, "finish_reason": finish_reason}]}
    return "data: " + json.dumps(chunk, ensure_ascii=False) + "\n\n"


def _error_event(message):
    return "data: " + json.dumps({"error": {"message": message}}, ensure_ascii=False) + "\n\n"


def _stream_response(events):
    return StreamingResponse(events, media_type="text/event-stream", headers={
        "Cache-Control": "no-cache", "X-Accel-Buffering": "no",
    })


@router.post("/v1/chat/completions", tags=["chat"], operation_id="chat_with_cluster")
async def chat_completions(body: ChatRequest, request: Request):
    """OpenAI-compatible text endpoint for Open WebUI, including SSE streaming."""
    if body.model not in {CHAT_MODEL, LEGACY_MODEL}:
        raise HTTPException(404, detail="Modèle inconnu : sélectionnez kubeai")
    service = request.app.state.diagnosis
    if not body.stream:
        answer, _ = await finish_nonstream(service, body)
        return answer

    payload, observation = await service.prepare(body)
    grounded = health_answer(body, observation)
    common = {"id": "chatcmpl-" + uuid.uuid4().hex, "object": "chat.completion.chunk",
              "created": int(time.time()), "model": body.model}
    if grounded is not None:
        answer = redact_text(grounded, service.sensitive_values()) + source_note(observation)

        async def grounded_events():
            yield _event(common, role="assistant")
            for part in answer.splitlines(keepends=True):
                yield _event(common, content=part)
            yield _event(common, finish_reason="stop")
            yield "data: [DONE]\n\n"

        return _stream_response(grounded_events())

    # Opening the response here lets authentication/quota failures retain HTTP 503.
    upstream = await service.llm.open_completion({**payload, "stream": True})

    async def events():
        finished = False
        content_seen = False
        note_filter = SourceNoteFilter()
        safe_lines = _SafeLines(service.sensitive_values())
        try:
            yield _event(common, role="assistant")
            async for line in upstream.aiter_lines():
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    break
                if not data or len(data) > 65536:
                    raise ValueError("Trame SSE invalide")
                chunk = json.loads(data)
                if not isinstance(chunk, dict):
                    raise ValueError("Trame SSE invalide")
                if "error" in chunk:
                    raise ValueError("Erreur fournisseur dans le flux")
                choices = chunk.get("choices")
                if not isinstance(choices, list):
                    raise ValueError("Choix SSE invalides")
                if not choices:
                    continue  # Optional usage frame.
                choice = choices[0]
                if not isinstance(choice, dict) or not isinstance(choice.get("delta"), dict):
                    raise ValueError("Delta SSE invalide")
                content = choice["delta"].get("content")
                if content is not None and not isinstance(content, str):
                    raise ValueError("Contenu SSE non textuel")
                if content:
                    filtered = note_filter.feed(content)
                    content_seen |= bool(filtered)
                    safe = safe_lines.feed(filtered)
                    if safe:
                        yield _event(common, content=safe)
                reason = choice.get("finish_reason")
                if reason is not None:
                    if not isinstance(reason, str):
                        raise ValueError("Fin SSE invalide")
                    tail = safe_lines.feed(note_filter.finish()) + safe_lines.finish()
                    if not content_seen and not tail:
                        raise ValueError("Réponse SSE vide")
                    if tail:
                        yield _event(common, content=tail)
                    yield _event(common, content=source_note(observation))
                    yield _event(common, finish_reason=reason)
                    finished = True
                    break
            if not finished:
                yield _error_event("Réponse du fournisseur interrompue avant sa fin")
            yield "data: [DONE]\n\n"
        except (httpx.HTTPError, ValueError, KeyError, TypeError, UnicodeError):
            yield _error_event("Flux du fournisseur interrompu ou invalide")
            yield "data: [DONE]\n\n"
        finally:
            await upstream.aclose()

    return _stream_response(events())
