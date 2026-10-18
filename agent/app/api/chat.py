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

router = APIRouter()


@router.get("/v1/models", tags=["chat"], operation_id="list_chat_models")
async def models():
    return {"object": "list", "data": [{
        "id": CHAT_MODEL, "object": "model", "created": 0, "owned_by": "kubeai",
        "name": "KubeAI · Local Apertus 8B",
    }]}


@router.get("/llm/status", tags=["health"], operation_id="get_llm_status")
async def llm_status(request: Request):
    """Check that the local engine exposes the configured Apertus model."""
    return await request.app.state.diagnosis.llm.status()


async def finish_nonstream(service, chat):
    payload, observation = await service.prepare(chat)
    grounded = health_answer(chat, observation)
    answer = completion(grounded) if grounded is not None else await service.llm.complete(payload)
    answer["model"] = CHAT_MODEL
    message = answer["choices"][0]["message"]
    message["content"] = filter_source_notes(message["content"]) + source_note(observation)
    return answer, observation


def completion(content):
    return {"id": "chatcmpl-" + uuid.uuid4().hex, "object": "chat.completion", "created": int(time.time()),
            "model": CHAT_MODEL, "choices": [{"index": 0, "message": {"role": "assistant", "content": content},
                                             "finish_reason": "stop"}]}


@router.post("/diagnose", tags=["diagnosis"], operation_id="diagnose_namespace")
async def diagnose(body: DiagnosisRequest, request: Request):
    """Analyze a question using fresh observations and return the evidence."""
    chat = ChatRequest(messages=[ChatMessage(role="user", content=body.question)])
    answer, observation = await finish_nonstream(request.app.state.diagnosis, chat)
    return {"model": CHAT_MODEL, "answer": answer["choices"][0]["message"]["content"],
            "observation": observation, "usage": answer.get("usage")}


@router.post("/v1/chat/completions", tags=["chat"], operation_id="chat_with_cluster")
async def chat_completions(body: ChatRequest, request: Request):
    """OpenAI-compatible text endpoint for Open WebUI, including SSE streaming."""
    if body.model != CHAT_MODEL:
        raise HTTPException(404, detail="Unknown model: select kubeai-apertus")
    service = request.app.state.diagnosis
    if not body.stream:
        answer, _ = await finish_nonstream(service, body)
        return answer
    payload, observation = await service.prepare(body)
    grounded = health_answer(body, observation)
    if grounded is not None:
        answer = completion(grounded + source_note(observation))

        async def grounded_events():
            common = {key: answer[key] for key in ("id", "created", "model")}
            common["object"] = "chat.completion.chunk"
            for part in answer["choices"][0]["message"]["content"].splitlines(keepends=True):
                yield "data: " + json.dumps({**common, "choices": [{"index": 0, "delta": {"content": part}, "finish_reason": None}]}) + "\n\n"
            yield "data: " + json.dumps({**common, "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]}) + "\n\n"
            yield "data: [DONE]\n\n"

        return StreamingResponse(grounded_events(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
    upstream = await service.llm.open_completion({**payload, "stream": True})

    async def events():
        finished = False
        note_filter = SourceNoteFilter()
        try:
            async for line in upstream.aiter_lines():
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    break
                chunk = json.loads(data)
                chunk["model"] = CHAT_MODEL
                if "error" in chunk:
                    yield "data: " + json.dumps({"error": {"message": "Apertus server error"}}) + "\n\n"
                    break
                choices = chunk.get("choices", [])
                if choices:
                    delta = choices[0].get("delta", {})
                    if isinstance(delta.get("content"), str):
                        delta["content"] = note_filter.feed(delta["content"])
                if choices and choices[0].get("finish_reason") and not finished:
                    final_text = choices[0].get("delta", {}).pop("content", "") or ""
                    footer = {"id": chunk.get("id"), "object": "chat.completion.chunk",
                              "created": chunk.get("created", int(time.time())), "model": CHAT_MODEL,
                              "choices": [{"index": 0, "delta": {"content": final_text + note_filter.finish() + source_note(observation)},
                                           "finish_reason": None}]}
                    yield "data: " + json.dumps(footer, ensure_ascii=False) + "\n\n"
                    finished = True
                yield "data: " + json.dumps(chunk, ensure_ascii=False) + "\n\n"
            if not finished:
                yield "data: " + json.dumps({"error": {"message": "Apertus response interrupted"}}) + "\n\n"
            yield "data: [DONE]\n\n"
        except (httpx.HTTPError, ValueError, KeyError, TypeError):
            yield "data: " + json.dumps({"error": {"message": "Apertus stream interrupted"}}) + "\n\n"
            yield "data: [DONE]\n\n"
        finally:
            await upstream.aclose()

    return StreamingResponse(events(), media_type="text/event-stream", headers={
        "Cache-Control": "no-cache", "X-Accel-Buffering": "no",
    })
