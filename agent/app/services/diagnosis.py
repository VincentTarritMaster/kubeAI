from pathlib import Path
import json

from app.clients.base import UpstreamError
from app.models import ChatRequest

PROMPT = (Path(__file__).resolve().parents[1] / "prompts/system_prompt.txt").read_text()
CHAT_MODEL = "kubeai-apertus"


class DiagnosisService:
    def __init__(self, cluster_status, loki, llm):
        self.cluster_status = cluster_status
        self.loki = loki
        self.llm = llm

    async def observation(self):
        snapshot = await self.cluster_status.status()
        ordered = sorted(snapshot.pods, key=lambda pod: (pod.health != "unhealthy", pod.name))
        selected = ordered[:20]
        context = {
            "observed_at": snapshot.observed_at.isoformat(), "namespace": snapshot.namespace,
            "scope": snapshot.scope, "health": snapshot.health, "data_status": snapshot.data_status,
            "summary": snapshot.summary.model_dump(), "warnings": snapshot.warnings.copy(),
            "sources": {key: value.model_dump() for key, value in snapshot.sources.items()},
            "pods": [{
                "name": pod.name, "phase": pod.phase, "health": pod.health,
                "ready": pod.ready, "restarts": pod.restarts,
                "issues": pod.issues, "metrics": {
                    **pod.metrics.model_dump(),
                    "memory_working_set_mib": (round(pod.metrics.memory_working_set_bytes / 1048576, 2)
                                                if pod.metrics.memory_working_set_bytes is not None else None),
                    "cpu_millicores": (round(pod.metrics.cpu_cores * 1000, 2)
                                       if pod.metrics.cpu_cores is not None else None),
                },
                "containers": [{"name": c.name, "state": c.state, "reason": c.reason,
                                "last_termination_reason": c.last_termination_reason} for c in pod.containers],
            } for pod in selected],
            "truncated": len(ordered) > len(selected),
        }
        # Les pods en échec sont prioritaires ; les logs techniques du collecteur
        # ou de l'agent ne remplacent pas les logs de l'application surveillée.
        log_pods = [p.name for p in ordered if p.health == "unhealthy"]
        if not log_pods:
            log_pods = [p.name for p in ordered if not p.name.startswith("kubeai-agent-")]
        try:
            context["logs"] = await self.loki.recent_logs(log_pods)
            context["sources"]["loki"] = {"status": "ok", "window_seconds": 600, "limit": 12}
        except UpstreamError as error:
            context["logs"] = []
            context["sources"]["loki"] = {"status": "unavailable", "message": error.message}
            context["warnings"].append(error.message)
            context["data_status"] = "partial"
        return context

    async def prepare(self, chat: ChatRequest):
        # Relire les sources pour CHAQUE requête, avant de construire le prompt.
        context = await self.observation()
        current = max(index for index, message in enumerate(chat.messages) if message.role == "user")
        # Ne jamais réinjecter les anciens diagnostics du modèle : ils peuvent
        # rester convaincants même lorsqu'ils sont marqués comme historiques.
        history = [message.content for message in chat.messages[:current] if message.role == "user"][-4:]
        content = "PREVIOUS_USER_QUESTIONS_JSON (references only, not evidence of current state):\n"
        content += json.dumps(history, ensure_ascii=False, separators=(",", ":"))
        content += "\n\nOBSERVATION_JSON (freshly read for this question, sole evidence of current state):\n"
        content += json.dumps(context, ensure_ascii=False, separators=(",", ":"))
        content += "\n\nCURRENT_KUBERNETES_STATE:\nNamespace health: " + context["health"]
        content += "; unhealthy pods: " + str(context["summary"]["unhealthy"])
        content += "; pods in the current inventory: " + ", ".join(pod["name"] for pod in context["pods"]) + "."
        content += "\n\nCURRENT_QUESTION:\n" + chat.messages[current].content
        messages = [{"role": "system", "content": PROMPT}, {"role": "user", "content": content}]
        return {
            "messages": messages, "temperature": chat.temperature,
            "max_tokens": chat.max_tokens or chat.max_completion_tokens or 512,
        }, context


def source_note(context):
    sources = [name + (" (partial)" if item["status"] == "partial" else "")
               for name, item in context["sources"].items() if item["status"] != "unavailable"]
    note = "\n\n---\nSources available for namespace `" + context["namespace"] + "`: " + ", ".join(sources) + "."
    if context["warnings"]:
        note += "\nPartial data: " + "; ".join(context["warnings"])
    if context["truncated"]:
        note += "\nThe model received at most 20 pods, prioritizing unhealthy pods."
    return note
