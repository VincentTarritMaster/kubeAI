"""OpenAI-compatible chat surface for Open WebUI and namespace diagnosis."""
import json
import asyncio
import time
import uuid
import logging
import traceback

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
import httpx

from app.models import AgentChatRequest, ChatMessage, ChatRequest, DiagnosisRequest
from app.api.auth import require_api_key
from app.services.diagnosis import CHAT_MODEL, source_note
from app.services.source_notes import SourceNoteFilter, filter_source_notes
from app.services.health_answer import health_answer
from app.conversation import OUT_OF_SCOPE, dialogue
from app.services.privacy import redact_text
from app.services.tool_agent import ToolAgent
from app.clients.base import UpstreamError

router = APIRouter(dependencies=[Depends(require_api_key)])
LEGACY_MODEL = "kubeai-apertus"


def diagnostic_route(chat: ChatRequest) -> str:
    """One dialogue resolver shared by JSON, streaming and /diagnose."""
    return dialogue(chat).route


@router.get("/v1/models", tags=["chat"], operation_id="list_chat_models")
async def models(request: Request):
    name = "kubeAI · " + request.app.state.diagnosis.llm.provider_name
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


async def finish_nonstream(service, chat, audit=None):
    if service.llm.settings.chat_mode == "agent":
        text, observation = await ToolAgent(service, audit=audit).run(chat)
        return completion(text, chat.model), observation
    route = diagnostic_route(chat)
    if route == "out_of_scope":
        return completion(OUT_OF_SCOPE, chat.model), {"scope": "out_of_scope", "status": "unsupported_question"}
    if route == "correlation":
        text, observation = await service.correlation(chat)
        return completion(text, chat.model), observation
    if route == "incident":
        text, observation = await service.incident(chat)
        return completion(text, chat.model), observation
    if route == "cluster":
        text, observation = await service.global_health()
        return completion(text, chat.model), observation
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
    # Deterministic A/B/C answers already contain their own bounded proof section.
    # Appending the generic note duplicated dates and made JSON differ from SSE.
    answer["choices"][0]["message"]["content"] = clean + (source_note(observation) if route == "chat" else "")
    answer["model"] = chat.model
    return answer, observation


@router.post("/diagnose", tags=["diagnosis"], operation_id="diagnose_namespace")
async def diagnose(body: DiagnosisRequest, request: Request):
    schema = AgentChatRequest if request.app.state.settings.chat_mode == "agent" else ChatRequest
    chat = schema(messages=[ChatMessage(role="user", content=body.question)])
    answer, observation = await finish_nonstream(request.app.state.diagnosis, chat, getattr(request.state, "audit", None))
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
async def chat_completions(body: AgentChatRequest, request: Request):
    """OpenAI-compatible text endpoint for Open WebUI, including SSE streaming."""
    if body.model not in {CHAT_MODEL, LEGACY_MODEL}:
        raise HTTPException(404, detail="Modèle inconnu : sélectionnez kubeai")
    service = request.app.state.diagnosis
    if service.llm.settings.chat_mode == "legacy":
        body = ChatRequest.model_validate(await request.json())
    if not body.stream:
        answer, _ = await finish_nonstream(service, body, getattr(request.state, "audit", None))
        return answer

    if service.llm.settings.chat_mode == "agent":
        common = {"id": "chatcmpl-" + uuid.uuid4().hex, "object": "chat.completion.chunk",
                  "created": int(time.time()), "model": body.model}

        async def agent_events():
            task = asyncio.create_task(ToolAgent(service, audit=getattr(request.state, "audit", None)).run(body))
            try:
                yield _event(common, role="assistant")
                while not task.done():
                    await asyncio.wait({task}, timeout=10)
                    if not task.done():
                        yield ": working\n\n"
                text, _ = await task
                for part in text.splitlines(keepends=True):
                    yield _event(common, content=part)
                yield _event(common, finish_reason="stop")
                yield "data: [DONE]\n\n"
            except UpstreamError as error:
                yield _error_event(error.message)
                yield "data: [DONE]\n\n"
            except Exception:
                # SSE has already started, so the normal HTTP exception logger
                # cannot report the failure. Keep diagnostics server-side and
                # redact credentials before logging the traceback.
                details = redact_text(traceback.format_exc(), (*service.sensitive_values(), service.llm.settings.api_key))
                logging.getLogger("uvicorn.error").error("Agent streaming failed:\n%s", details)
                yield _error_event("The analysis was interrupted by an internal error.")
                yield "data: [DONE]\n\n"
            finally:
                if not task.done():
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)

        return _stream_response(agent_events())

    route = diagnostic_route(body)
    if route != "chat":
        answer, _ = ((OUT_OF_SCOPE, {}) if route == "out_of_scope" else
                     await service.correlation(body) if route == "correlation" else
                     await service.incident(body) if route == "incident" else
                     await service.global_health())
        common = {"id": "chatcmpl-" + uuid.uuid4().hex, "object": "chat.completion.chunk",
                  "created": int(time.time()), "model": body.model}

        async def cluster_events():
            yield _event(common, role="assistant")
            for part in answer.splitlines(keepends=True):
                yield _event(common, content=part)
            yield _event(common, finish_reason="stop")
            yield "data: [DONE]\n\n"

        return _stream_response(cluster_events())

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
        except Exception:
            # Once SSE headers are sent, surface a terminal frame instead of
            # leaving Open WebUI waiting on a server-side exception.
            yield _error_event("Diagnostic interrompu par une erreur interne")
            yield "data: [DONE]\n\n"
        finally:
            await upstream.aclose()

    return _stream_response(events())
