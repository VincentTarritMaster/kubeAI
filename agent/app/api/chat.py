"""OpenAI-compatible chat surface for Open WebUI and namespace diagnosis."""
import json
import asyncio
import re
import time
import uuid
import logging
import traceback

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
import httpx

from app.models import AgentChatRequest, ChatMessage, ChatRequest, DiagnosisRequest
from app.api.auth import require_api_key
from app.services.diagnosis import CHAT_MODEL, source_note
from app.services.source_notes import SourceNoteFilter, filter_source_notes
from app.services.health_answer import health_answer
from app.services.global_health import cluster_health_question
from app.conversation import OUT_OF_SCOPE, dialogue, explicit_intent, latest_incident_request
from app.services.privacy import redact_text
from app.services.tool_agent import ToolAgent
from app.clients.base import UpstreamError
from app.services.user_errors import language_of, new_diagnostic_id, user_error
from app.services.intents import is_write_request, refusal_text

router = APIRouter(dependencies=[Depends(require_api_key)])
LEGACY_MODEL = "kubeai-apertus"


def diagnostic_route(chat: ChatRequest) -> str:
    """One dialogue resolver shared by JSON, streaming and /diagnose."""
    return dialogue(chat).route


def deterministic_demo_route(chat):
    """Route demo intents by language-independent intent, not exact wording."""
    text = chat.messages[-1].content
    if is_write_request(text):
        return "write_refusal"
    intent = explicit_intent(text)
    if intent in {"incident", "correlation"}:
        return intent
    if latest_incident_request(text):
        return "incident"
    normalized = text.lower()
    has_window = bool(re.search(
        r"\b\d{1,2}\s+(?:janvier|fevrier|mars|avril|mai|juin|juillet|aout|septembre|octobre|novembre|decembre)\s+\d{4}\b"
        r"|\b\d{4}-\d{2}-\d{2}\b|\bentre\s+\d{1,2}(?::|h)\d{2}\b", normalized))
    has_target = bool(re.search(r"\b(pod|pods|namespace|applications|kube-system)\b", normalized))
    if has_window and has_target and re.search(
            r"\b(incident|chronologie|passe|arrive|logs?|journaux|analyse)\b", normalized):
        return "incident"
    if cluster_health_question(chat):
        if re.search(r"\bsupervision\b", text, re.IGNORECASE):
            return "supervision"
        return "cluster"
    return None


def health_namespace(chat):
    """Extract a namespace filter without confusing an all-namespace question."""
    text = chat.messages[-1].content
    normalized = text.lower()
    if re.search(r"\b(tous les namespaces|toutes les namespaces|all namespaces|tous les espaces)\b", normalized):
        return None
    match = re.search(r"\bnamespace\s*(?:=|:)?\s*([a-z0-9][a-z0-9-]{0,62})\b", normalized)
    if match:
        return match.group(1)
    match = re.search(r"\b(?:espace de noms|espace)\s+(?:=|:)?\s*([a-z0-9][a-z0-9-]{0,62})\b", normalized)
    if match:
        return match.group(1)
    match = re.search(r"\b(kube-system|applications|collecte)\b", normalized)
    return match.group(1) if match else None


SUPERVISION_SCOPE = (
    "Je surveille uniquement le cluster **kubeai-observed**. Le cluster "
    "**kubeai-supervision** n'est pas exposé comme cible de diagnostic à l'agent : "
    "je ne peux donc pas produire un rapport de santé sur ses nœuds ou ses pods. "
    "Aucune donnée du cluster de supervision n'est inventée."
)
SUPERVISION_SCOPE_EN = (
    "I only monitor the **kubeai-observed** cluster. The **kubeai-supervision** "
    "cluster is not exposed as a diagnostic target to the agent, so I cannot "
    "produce a health report for its nodes or pods. No supervision-cluster data "
    "is invented."
)


def supervision_text(question):
    return SUPERVISION_SCOPE_EN if language_of(question) == "en" else SUPERVISION_SCOPE

WRITE_REFUSAL = (
    "Je ne peux pas supprimer, redémarrer, corriger ou modifier une ressource Kubernetes : "
    "KubeAI est strictement en lecture seule. Aucune action n'a été exécutée. "
    "Vous pouvez demander un diagnostic, les preuves disponibles ou des vérifications en lecture seule."
)


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
    route = (deterministic_demo_route(chat)
             if service.llm.settings.chat_mode == "agent" else diagnostic_route(chat))
    if route == "out_of_scope":
        return completion(OUT_OF_SCOPE, chat.model), {"scope": "out_of_scope", "status": "unsupported_question"}
    if route == "supervision":
        text = supervision_text(chat.messages[-1].content)
        return completion(text, chat.model), {"scope": "supervision", "status": "not_observed"}
    if route == "write_refusal":
        text = refusal_text(chat.messages[-1].content)
        return completion(text, chat.model), {"scope": "read_only", "status": "refused"}
    if route == "correlation":
        text, observation = await service.correlation(chat)
        return completion(text, chat.model), observation
    if route == "incident":
        text, observation = await service.incident(chat)
        return completion(text, chat.model), observation
    if route == "cluster":
        text, observation = await service.global_health(
            question=chat.messages[-1].content, namespace=health_namespace(chat))
        return completion(text, chat.model), observation
    if service.llm.settings.chat_mode == "agent":
        text, observation = await ToolAgent(service, audit=audit).run(chat)
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
    try:
        answer, observation = await finish_nonstream(
            request.app.state.diagnosis, chat, getattr(request.state, "audit", None))
    except UpstreamError as error:
        return _user_error_response(error, body.question)
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


def _error_event(message, **extra):
    payload = {"message": message}
    payload.update(extra)
    return "data: " + json.dumps({"error": payload}, ensure_ascii=False) + "\n\n"


def _user_error_response(error, question):
    info = user_error(error, question)
    logging.getLogger("uvicorn.error").error(
        "diagnostic_id=%s code=%s component=%s source_error=%s",
        info["diagnostic_id"], info["code"], info["component"], info["source_message"],
    )
    return JSONResponse(status_code=503, content={"detail": {
        "code": info["code"], "message": info["message"], "source": info["source"],
        "diagnostic_id": info["diagnostic_id"], "component": info["component"],
        "complete": False, "retryable": info["retryable"],
    }})


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
        try:
            answer, _ = await finish_nonstream(service, body, getattr(request.state, "audit", None))
            return answer
        except UpstreamError as error:
            return _user_error_response(error, body.messages[-1].content)

    route = (deterministic_demo_route(body)
             if service.llm.settings.chat_mode == "agent" else diagnostic_route(body))
    if route in {"out_of_scope", "supervision", "write_refusal", "correlation", "incident", "cluster"}:
        try:
            if route == "out_of_scope":
                text, observation = OUT_OF_SCOPE, {"scope": "out_of_scope"}
            elif route == "supervision":
                text = supervision_text(body.messages[-1].content)
                observation = {"scope": "supervision", "status": "not_observed"}
            elif route == "write_refusal":
                text = refusal_text(body.messages[-1].content)
                observation = {"scope": "read_only", "status": "refused"}
            elif route == "correlation":
                text, observation = await service.correlation(body)
            elif route == "incident":
                text, observation = await service.incident(body)
            else:
                text, observation = await service.global_health(
                    question=body.messages[-1].content, namespace=health_namespace(body))
        except UpstreamError as error:
            return _user_error_response(error, body.messages[-1].content)
        common = {"id": "chatcmpl-" + uuid.uuid4().hex, "object": "chat.completion.chunk",
                  "created": int(time.time()), "model": body.model}

        async def deterministic_events():
            yield _event(common, role="assistant")
            for part in text.splitlines(keepends=True):
                yield _event(common, content=part)
            yield _event(common, finish_reason="stop")
            yield "data: [DONE]\n\n"

        return _stream_response(deterministic_events())

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
                info = user_error(error, body.messages[-1].content)
                logging.getLogger("uvicorn.error").error(
                    "diagnostic_id=%s code=%s component=%s source_error=%s",
                    info["diagnostic_id"], info["code"], info["component"], info["source_message"],
                )
                yield _error_event(
                    info["message"], code=info["code"], diagnostic_id=info["diagnostic_id"],
                    component=info["component"], complete=False, retryable=info["retryable"],
                )
                yield "data: [DONE]\n\n"
            except Exception:
                # SSE has already started, so the normal HTTP exception logger
                # cannot report the failure. Keep diagnostics server-side and
                # redact credentials before logging the traceback.
                diagnostic_id = new_diagnostic_id()
                details = redact_text(traceback.format_exc(), (*service.sensitive_values(), service.llm.settings.api_key))
                logging.getLogger("uvicorn.error").error(
                    "diagnostic_id=%s code=INTERNAL_ERROR Agent streaming failed:\n%s", diagnostic_id, details)
                info = user_error(RuntimeError("internal"), body.messages[-1].content, diagnostic_id)
                yield _error_event(
                    info["message"], code=info["code"], diagnostic_id=info["diagnostic_id"],
                    component=info["component"], complete=False, retryable=False,
                )
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
                     await service.global_health(question=body.messages[-1].content))
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
