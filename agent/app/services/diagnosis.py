import asyncio
import copy
from pathlib import Path
from datetime import datetime, timezone
import json
import time
import uuid

from app.clients.base import UpstreamError
from app.models import ChatRequest
from app.services.privacy import redact_data, redact_text
from app.services.global_health import ClusterHealthReport, assess, focus_note, model_focus, render
from app.services.incident_timeline import investigate
from app.services.correlation import correlate
from app.services.presentation import DEFAULT_ZONE, full_time, technical_details
from app.services.user_errors import language_of

PROMPT = (Path(__file__).resolve().parents[1] / "prompts/system_prompt.txt").read_text(encoding="utf-8")
CHAT_MODEL = "kubeai"
HEALTH_SNAPSHOT_TTL_SECONDS = 120
HISTORICAL_SNAPSHOT_TTL_SECONDS = 300


class DiagnosisService:
    def __init__(self, cluster_status, loki, llm):
        self.cluster_status = cluster_status
        self.loki = loki
        self.llm = llm
        self._health_cache = {}
        self._health_cache_at = 0.0
        self._historical_cache = {}

    def sensitive_values(self):
        try:
            key = self.llm.settings.llm_key()
        except (OSError, UnicodeError):
            key = ""
        return (key,) if key else ()

    async def _historical(self, kind, chat, operation):
        if self.llm.settings.chat_mode != "agent":
            return await operation()
        key = (kind, chat.messages[-1].content)
        now = time.monotonic()
        cached = self._historical_cache.get(key)
        if cached and now - cached[0] < HISTORICAL_SNAPSHOT_TTL_SECONDS:
            return copy.deepcopy(cached[1])
        result = await operation()
        self._historical_cache[key] = (now, copy.deepcopy(result))
        return result

    async def incident(self, chat):
        return await self._historical("incident", chat, lambda: investigate(self, chat, language=language_of(chat.messages[-1].content)))

    async def correlation(self, chat):
        return await self._historical("correlation", chat, lambda: correlate(self, chat, language=language_of(chat.messages[-1].content)))

    async def global_health(self, *, refresh=False, question="", namespace=None):
        """Deterministic health with an immutable short-lived LIVE snapshot."""
        lang = language_of(question)
        cache_key = namespace or "*"
        now = time.monotonic()
        cached = self._health_cache.get(cache_key)
        if not refresh and cached and now - cached[1] < HEALTH_SNAPSHOT_TTL_SECONDS:
            report = copy.deepcopy(cached[0])
            return await self._render_health(report, lang), report

        inventory, usage = await asyncio.gather(
            self.cluster_status.inventory(),
            self.cluster_status.prometheus.cluster_usage(datetime.now(timezone.utc).timestamp()),
            return_exceptions=True,
        )
        if isinstance(inventory, Exception):
            raise inventory
        mimir_error = usage.message if isinstance(usage, UpstreamError) else (
            "Mimir read failed" if isinstance(usage, Exception) else None)
        report = assess(inventory, None if isinstance(usage, Exception) else usage, mimir_error,
                        namespace=namespace)
        report["snapshot_id"] = "live-" + uuid.uuid4().hex[:12]
        report["cluster"] = self.llm.settings.observed_cluster
        report["mode"] = "LIVE"
        report = ClusterHealthReport.model_validate(redact_data(report, self.sensitive_values())).model_dump()
        previous = cached[0] if cached else None
        if previous:
            changes = []
            if previous.get("health") != report.get("health"):
                changes.append({"field": "health", "label": "état",
                                "before": previous.get("health"), "after": report.get("health")})
            for key in ("pending", "failed", "succeeded", "unknown", "unhealthy", "running"):
                before = (previous.get("pods") or {}).get(key)
                after = (report.get("pods") or {}).get(key)
                if before != after:
                    changes.append({"field": "pods." + key, "label": "pods " + key,
                                    "before": before, "after": after})
            before_nodes = (previous.get("nodes") or {}).get("not_ready")
            after_nodes = (report.get("nodes") or {}).get("not_ready")
            if before_nodes != after_nodes:
                changes.append({"field": "nodes.not_ready", "label": "nœuds NotReady",
                                "before": before_nodes, "after": after_nodes})
            for key, label, digits in (("cpu_cores", "CPU (cores)", 3),
                                       ("memory_working_set_bytes", "memory (bytes)", 0)):
                before = (previous.get("metrics") or {}).get(key)
                after = (report.get("metrics") or {}).get(key)
                if (before is not None and after is not None
                        and abs(float(before) - float(after)) > (0.01 if digits else 1048576)):
                    changes.append({"field": "metrics." + key, "label": label,
                                    "before": round(float(before), digits),
                                    "after": round(float(after), digits)})
            if changes:
                report["changes"] = changes
                report["previous_snapshot_id"] = previous.get("snapshot_id")
        self._health_cache[cache_key] = (copy.deepcopy(report), now)
        return await self._render_health(report, lang), report

    async def _render_health(self, report, lang):
        if lang == "en":
            ai_note = ("**Deterministic rules:** status, counters and metrics are calculated "
                       "by code. Mistral is not used to modify this diagnosis.")
        else:
            ai_note = ("**Règles déterministes :** le statut, les compteurs et les métriques sont calculés "
                       "par le code. Mistral n'est pas utilisé pour modifier ce diagnostic.")
        # Legacy mode keeps its optional focus note; the agent/demo route never
        # lets Mistral alter facts or wording.
        if (lang == "fr" and self.llm.settings.chat_mode != "agent"
                and (report["state_code"] != "indeterminate" or report["nodes"]["total"] is not None
                     or report["pods"]["total"] is not None)):
            prompt = (
                "Choisis uniquement une priorité étayée par le relevé. Réponds par un objet JSON "
                "avec la seule clé focus : nodes, pods, restarts, events, availability ou none. "
                "N'ajoute aucun fait, texte libre, cause ou commande."
            )
            try:
                model_report = {**report, "recent_warnings": [
                    {key: value for key, value in event.items() if key != "message"}
                    for event in report["recent_warnings"]]}
                selected = await self.llm.select_option(
                    prompt=prompt, data=model_report, key="focus",
                    options=("nodes", "pods", "restarts", "events", "availability", "none"))
                focus = model_focus(report, json.dumps({"focus": selected}))
                if focus:
                    ai_note = focus_note(focus, self.llm.provider_name)
                else:
                    ai_note = "**Analyse IA avancée :** proposition non étayée écartée ; le bilan reste déterministe."
            except UpstreamError as error:
                if "Sélection structurée" in error.message:
                    ai_note = "**Analyse IA avancée :** proposition non étayée écartée ; le bilan reste déterministe."
            except (KeyError, IndexError, TypeError, ValueError):
                pass
        return render(report, ai_note=ai_note, zone=self.llm.settings.display_timezone, language=lang)

    async def observation(self):
        snapshot = await self.cluster_status.status()
        ordered = sorted(snapshot.pods, key=lambda pod: (pod.health != "unhealthy", pod.name))
        selected = ordered[:20]
        context = {
            "observed_at": snapshot.observed_at.isoformat(), "namespace": snapshot.namespace,
            "scope": snapshot.scope, "health": snapshot.health, "data_status": snapshot.data_status,
            "summary": snapshot.summary.model_dump(),
            "warnings": [warning[:240] for warning in snapshot.warnings[:8]],
            "warnings_truncated": len(snapshot.warnings) > 8,
            "sources": {key: {**value.model_dump(), "message": value.message[:240] if value.message else None}
                        for key, value in snapshot.sources.items()},
            "pods": [{
                "name": pod.name, "phase": pod.phase, "health": pod.health,
                "ready": pod.ready, "restarts": pod.restarts,
                "issues": [issue[:240] for issue in pod.issues[:3]], "metrics": {
                    **pod.metrics.model_dump(),
                    "memory_working_set_mib": (round(pod.metrics.memory_working_set_bytes / 1048576, 2)
                                                if pod.metrics.memory_working_set_bytes is not None else None),
                    "cpu_millicores": (round(pod.metrics.cpu_cores * 1000, 2)
                                       if pod.metrics.cpu_cores is not None else None),
                },
                "containers": [{"name": c.name, "state": c.state,
                                "reason": c.reason[:120] if c.reason else None,
                                "last_termination_reason": (c.last_termination_reason[:120]
                                                            if c.last_termination_reason else None)}
                               for c in pod.containers[:8]],
                "containers_truncated": len(pod.containers) > 8,
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
            for entry in context["logs"]:
                try:
                    seconds, nanos = divmod(int(entry["timestamp_ns"]), 1_000_000_000)
                    entry["timestamp"] = datetime.fromtimestamp(seconds, tz=timezone.utc).replace(
                        microsecond=nanos // 1000).isoformat()
                except (OverflowError, OSError, ValueError):
                    entry["timestamp"] = str(entry["timestamp_ns"]) + " ns"
            context["sources"]["loki"] = {"status": "ok", "window_seconds": 600, "limit": 12}
        except UpstreamError as error:
            context["logs"] = []
            context["sources"]["loki"] = {"status": "unavailable", "message": error.message}
            context["warnings"].append(error.message)
            context["data_status"] = "partial"
        return redact_data(context, self.sensitive_values())

    async def prepare(self, chat: ChatRequest):
        # Relire les sources pour CHAQUE requête, avant de construire le prompt.
        context = await self.observation()
        sensitive_values = self.sensitive_values()
        current = max(index for index, message in enumerate(chat.messages) if message.role == "user")
        # Ne jamais réinjecter les anciens diagnostics du modèle : ils peuvent
        # rester convaincants même lorsqu'ils sont marqués comme historiques.
        history = [redact_text(message.content, sensitive_values) for message in chat.messages[:current]
                   if message.role == "user"][-4:]
        content = "PREVIOUS_USER_QUESTIONS_JSON (references only, not evidence of current state):\n"
        content += json.dumps(history, ensure_ascii=False, separators=(",", ":"))
        content += "\n\nOBSERVATION_JSON (freshly read for this question, sole evidence of current state):\n"
        content += json.dumps(context, ensure_ascii=False, separators=(",", ":"))
        content += "\n\nCURRENT_KUBERNETES_STATE:\nCluster health: " + context["health"]
        content += "; unhealthy pods: " + str(context["summary"]["unhealthy"])
        content += "; pods in the current inventory: " + ", ".join(pod["name"] for pod in context["pods"]) + "."
        content += "\n\nCURRENT_QUESTION:\n" + redact_text(chat.messages[current].content, sensitive_values)
        messages = [{"role": "system", "content": PROMPT}, {"role": "user", "content": content}]
        return {
            "messages": messages, "temperature": chat.temperature,
            "max_tokens": chat.max_tokens or chat.max_completion_tokens or 512,
        }, context


def source_note(context, *, zone=DEFAULT_ZONE):
    sources = [name + (" (partielle)" if item["status"] == "partial" else "")
               for name, item in context["sources"].items() if item["status"] != "unavailable"]
    note = ("\n\n---\nSources disponibles pour "
            + ("le cluster" if context["namespace"] == "*" else "le namespace `" + context["namespace"] + "`")
            + " ; relevé le " + full_time(context["observed_at"], zone) + " : " + ", ".join(sources) + ".")
    if context["warnings"]:
        unavailable = [name for name, item in context["sources"].items() if item["status"] == "unavailable"]
        note += "\nDonnées partielles : " + (
            "sources indisponibles : " + ", ".join(unavailable) + "." if unavailable else
            "certaines mesures ou observations sont incomplètes.")
    if context["warnings_truncated"]:
        note += "\nLa liste des avertissements transmise au modèle est limitée à huit éléments."
    if context["truncated"]:
        note += "\nLe modèle a reçu au plus 20 pods, en priorité ceux en défaut."
    note += "\n" + technical_details([f"Relevé UTC : {context['observed_at']}"])
    return note
