"""The LLM chooses validated read-only tools, then writes the user-facing answer."""
import asyncio
from datetime import datetime, timezone
import json
import logging
from pathlib import Path
import re

from pydantic import ValidationError

from app.clients.base import UpstreamError
from app.services.llm_service import _unique_json_object
from app.services.observability_tools import ObservabilityTools, bounded_result
from app.services.privacy import redact_text

PROMPT = (Path(__file__).resolve().parents[1] / "prompts/tool_agent.txt").read_text()
logger = logging.getLogger("uvicorn.error")


class ToolAgent:
    MAX_CONTEXT_CHARS = 20000

    def __init__(self, diagnosis):
        self.diagnosis = diagnosis
        self.settings = diagnosis.llm.settings
        self.tools = ObservabilityTools(diagnosis)

    def sensitive(self):
        return (*self.diagnosis.sensitive_values(), self.settings.api_key)

    def verify_target(self, arguments, references, known):
        """Validate argument provenance, independently of question intent/language."""
        namespace = arguments.get("namespace") or self.settings.namespace
        pod = arguments.get("pod")
        if pod and (namespace, pod) not in known and not any(pod in text for text in references):
            raise ValueError("Unverified pod name. Never invent resource names. Omit pod in "
                             "find_latest_incident, or read get_cluster_inventory to discover real resources.")

    def remember_targets(self, result, known):
        if isinstance(result, dict):
            namespace = result.get("namespace") or self.settings.namespace
            pod = result.get("pod")
            if isinstance(pod, str):
                known.add((namespace, pod))
            pods = result.get("pods")
            # Inventory summaries also contain a numeric `pods` count, while
            # source status maps use `pods` for a status object. Neither is a
            # resource list; only actual list entries establish provenance.
            if isinstance(pods, list):
                for pod in pods:
                    if isinstance(pod, dict) and isinstance(pod.get("name"), str):
                        known.add((pod.get("namespace") or namespace, pod["name"]))
            for value in result.values():
                self.remember_targets(value, known)
        elif isinstance(result, list):
            for value in result:
                self.remember_targets(value, known)

    def bound_context(self, messages, protected):
        def size():
            return len(json.dumps({"messages": messages, "tools": self.tools.schemas}, ensure_ascii=False))
        while size() > self.MAX_CONTEXT_CHARS:
            disposable = next((i for i in range(1, len(messages) - 1)
                               if messages[i] is not protected and messages[i]["role"] in {"user", "assistant"}
                               and not messages[i].get("tool_calls")), None)
            if disposable is not None:
                messages.pop(disposable)
                continue
            tool = next((item for item in messages if item["role"] == "tool" and len(item["content"]) > 1800), None)
            if tool is None:
                raise UpstreamError("agent", "The analysis context is full; narrow the question or time window.")
            tool["content"] = json.dumps(bounded_result(json.loads(tool["content"]), limit=1700), ensure_ascii=False)

    def verify_claims(self, answer, trace):
        """Reject explicit numeric claims absent from the collected evidence."""
        evidence = json.dumps(trace, ensure_ascii=False)
        numbers = {float(value.replace(",", ".")) for value in re.findall(r"(?<![\w.])\d+(?:[.,]\d+)?", evidence)}
        claim = re.compile(r"(?<![\w.])(\d+(?:[.,]\d+)?)\s*(%|pods?|restarts?|requests?|errors?|5xx|4xx|seconds?|ms)\b", re.I)
        for match in claim.finditer(answer):
            value = float(match.group(1).replace(",", "."))
            if not any(abs(value - candidate) <= max(abs(value) * 0.01, 0.05) for candidate in numbers):
                raise UpstreamError(
                    "agent",
                    "The model produced a numeric claim that is not present in the tool evidence; "
                    "ask a narrower question.",
                )

    async def run(self, chat):
        try:
            return await asyncio.wait_for(self._run(chat), timeout=self.settings.agent_timeout)
        except asyncio.TimeoutError as error:
            raise UpstreamError("agent", "The analysis timed out before it could finish.") from error

    async def _run(self, chat):
        clock = datetime.now(timezone.utc).isoformat()
        preface = (PROMPT + f"\nCurrent UTC time: {clock}. Display timezone: {self.settings.display_timezone}."
                   f"\nObserved cluster: {self.settings.observed_cluster or 'configured cluster'}."
                   f" Collected application namespace: {self.settings.namespace}.")
        messages = [{"role": "system", "content": preface}]
        current = max(i for i, message in enumerate(chat.messages) if message.role == "user")
        for message in chat.messages[max(0, current - 6):current + 1]:
            if message.role in {"user", "assistant"}:
                messages.append({"role": message.role, "content": redact_text(message.content, self.sensitive())})
        question_message = messages[-1]
        trace, successful, cache = [], 0, {}
        references = [message["content"] for message in messages if message["role"] in {"user", "assistant"}]
        known = set()
        tool_count = 0
        for step in range(self.settings.agent_max_steps):
            messages[0]["content"] = (preface + f"\nExecution progress: {successful} fresh evidence reads; "
                f"{self.settings.agent_max_steps - step} decisions remaining. "
                "Do not repeat completed calls. If the evidence answers the user's question, finish with respond. "
                "Request another function only for a specific missing fact.")
            self.bound_context(messages, question_message)
            payload = {
                "messages": messages, "tools": self.tools.schemas, "tool_choice": "required",
                "parallel_tool_calls": False, "temperature": 0,
                "max_tokens": min(1024, max(512, chat.max_tokens or chat.max_completion_tokens or 768)),
            }
            try:
                decision = await self.diagnosis.llm.tool_completion(payload)
            except UpstreamError as error:
                if error.source != "llm" or "appel d'outil valide" not in error.message:
                    raise
                logger.warning("Malformed tool-call response; retrying once")
                messages.append({"role": "user", "content":
                                 "The previous function call was invalid. Return exactly one valid function call "
                                 "with a JSON arguments object, or finish with respond."})
                decision = await self.diagnosis.llm.tool_completion({
                    **payload, "messages": messages,
                })
            calls = decision.get("tool_calls", [])
            if not calls:
                messages.append({"role": "user", "content": "Use the provided functions. Finish with respond; do not emit unverified prose."})
                continue
            messages.append(decision)
            for call in calls:
                tool_count += 1
                if tool_count > self.settings.agent_max_steps:
                    raise UpstreamError("agent", "The tool-call budget was reached.")
                name = call["function"]["name"]
                try:
                    arguments = json.loads(call["function"]["arguments"], object_pairs_hook=_unique_json_object)
                    if not isinstance(arguments, dict) or name not in self.tools.registry:
                        raise ValueError("Unknown function or invalid arguments")
                    tool = self.tools.registry[name]
                    # Validate before any I/O, including the finishing function.
                    tool.args_schema.model_validate(arguments)
                    if name == "respond":
                        if len(calls) != 1:
                            raise ValueError("Call respond separately from evidence tools")
                        if arguments["kind"] == "answer" and not successful:
                            raise ValueError("Read fresh evidence before making monitoring claims")
                        answer = redact_text(arguments["answer"], self.sensitive())
                        windows = []
                        for entry in trace:
                            data = entry["result"]
                            if ("error" not in data and data.get("pod") and data.get("namespace")
                                    and data.get("start") and data.get("end") and entry["name"] != "find_latest_incident"):
                                reference = (f"`{data['namespace']}/{data['pod']}` · "
                                             f"`{data['start']}` → `{data['end']}`")
                                if reference not in windows:
                                    windows.append(reference)
                        if arguments["kind"] == "answer":
                            self.verify_claims(answer, trace)
                        if windows and arguments["kind"] == "answer":
                            answer += "\n\n---\n" + "\n".join(windows[-3:])
                        return answer, {"scope": "agent", "status": arguments["kind"], "observed_at": clock,
                                        "namespace": self.settings.namespace, "tools": trace}
                    self.verify_target(arguments, references, known)
                    signature = name + json.dumps(arguments, sort_keys=True)
                    if signature in cache:
                        result = cache[signature]
                    else:
                        result = bounded_result(await tool.ainvoke(arguments), self.sensitive())
                        cache[signature] = result
                    if "error" not in result and name != "find_latest_incident":
                        successful += 1
                    if "error" not in result and result.get("status") != "unresolved":
                        self.remember_targets(result, known)
                    logger.info("Observability tool completed: %s", name)
                except (ValueError, ValidationError) as error:
                    result = {"error": "invalid_tool_call", "message": str(error)[:600]}
                    logger.info("Observability tool rejected: %s", name if name in self.tools.registry else "unknown")
                except UpstreamError as error:
                    result = {"error": "source_unavailable", "source": error.source, "message": error.message}
                safe = bounded_result(result, self.sensitive())
                trace.append({"name": name, "result": safe})
                messages.append({"role": "tool", "tool_call_id": call["id"], "name": name,
                                 "content": json.dumps(safe, ensure_ascii=False)})
        raise UpstreamError("agent", "The tool budget was reached before a supported answer was produced.")
