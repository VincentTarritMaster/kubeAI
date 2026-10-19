"""Deterministic incident timeline from bounded historical sources."""

import asyncio
from datetime import datetime, timezone
import json
import re

from app.clients.base import UpstreamError
from app.services.privacy import redact_data, redact_text

HELP = ("Précisez une fenêtre avec fuseau : début=2026-10-19T14:00:00+02:00 "
        "fin=2026-10-19T14:30:00+02:00 namespace=kubeai pod=kubeai-demo-failure. "
        "Une heure isolée ou « ce matin » reste ambiguë.")


def incident_question(chat):
    question = next((m.content for m in reversed(chat.messages) if m.role == "user"), "").lower()
    return any(s in question for s in ("incident", "chronologie", "redémarr", "redemarr",
                                        "début=", "debut=",
                                        "que s'est-il passé", "que s’est-il passé"))


def _arg(question, name):
    found = re.search(r"(?<![\w-])" + name + r"=([^\s,;]+)", question, re.IGNORECASE)
    return found.group(1) if found else None


def parse_window(question):
    first = _arg(question, "début") or _arg(question, "debut")
    last = _arg(question, "fin")
    namespace, pod = _arg(question, "namespace"), _arg(question, "pod")
    if not first or not last:
        return None, HELP
    try:
        start, end = (datetime.fromisoformat(s.replace("Z", "+00:00")) for s in (first, last))
    except ValueError:
        return None, HELP
    if start.tzinfo is None or end.tzinfo is None or start.utcoffset() is None or end.utcoffset() is None:
        return None, HELP
    start, end = start.astimezone(timezone.utc), end.astimezone(timezone.utc)
    if not start < end or (end - start).total_seconds() > 21600:
        return None, "Intervalle positif de six heures maximum requis. " + HELP
    valid = r"[a-z0-9](?:[-a-z0-9]*[a-z0-9])?"
    if namespace and (len(namespace) > 63 or not re.fullmatch(valid, namespace)):
        return None, "Namespace invalide. " + HELP
    if pod and (not namespace or len(pod) > 253 or not re.fullmatch(valid, pod)):
        return None, "Précisez le namespace et un pod valide. " + HELP
    return {"start": start, "end": end, "timezone": first[-6:] if first[-6] in "+-" else "UTC",
            "namespace": namespace, "pod": pod}, None


def stamp(value):
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def build(window, inventory, metrics, logs, failures):
    start, end = window["start"], window["end"]
    entries, warnings = [], []
    sources = {
        "kubernetes_events": inventory.sources["events"].model_dump() if inventory else
        {"status": "unavailable", "message": failures.get("kubernetes", "Lecture impossible")},
        "kubernetes_pods": inventory.sources["pods"].model_dump() if inventory else
        {"status": "unavailable", "message": failures.get("kubernetes", "Lecture impossible")},
        "mimir": {"status": "unavailable" if "mimir" in failures else
                  "partial" if metrics and (metrics.truncated or metrics.partial or metrics.warnings) else "ok",
                  "message": failures.get("mimir")},
        "loki": {"status": "unavailable" if "loki" in failures or not window["namespace"] else
                 "partial" if logs and (logs.truncated or logs.outside_window) else "ok",
                 "message": failures.get("loki") or ("Namespace requis pour les logs" if not window["namespace"] else None)},
    }
    if inventory:
        warnings.extend(inventory.warnings[:8])
        if inventory.events is None or not inventory.events:
            warnings.append("Événements absents ou indisponibles ; les anciens événements peuvent avoir expiré.")
        event_versions = {}
        for event in inventory.events or []:
            if window["namespace"] and event.namespace != window["namespace"]:
                continue
            if window["pod"] and event.involved_name != window["pod"]:
                continue
            if event.observed_at is None:
                warnings.append("Événement sans horodatage ignoré.")
                continue
            if not start <= event.observed_at <= end:
                continue
            key = (event.namespace, event.name)
            signature = (event.reason, event.message)
            if key in event_versions and event_versions[key] != signature:
                warnings.append("Versions contradictoires d'un même événement Kubernetes.")
            event_versions[key] = signature
            resource = "/".join(s for s in (event.namespace, event.involved_name) if s)
            detail = " / ".join(s for s in (event.type, event.reason) if s)
            if event.message:
                detail += " : " + event.message[:300]
            if event.count and event.count > 1:
                detail += f" (compteur {event.count} ; dernier horodatage connu)"
            entries.append({"timestamp": stamp(event.observed_at), "type": "événement",
                            "source": "Kubernetes", "resource": resource or "inconnue",
                            "observation": detail,
                            "reference": f"Event {event.namespace or '?'} / {event.name}"})
    if metrics:
        warnings.extend(metrics.warnings[:8])
        for point in metrics.points:
            moment = datetime.fromtimestamp(point.timestamp, tz=timezone.utc)
            resource = "/".join(s for s in (point.labels.get("namespace"), point.labels.get("pod")) if s) or "cluster"
            unit = "cœur(s) CPU" if point.metric == "cpu_cores" else "octets de mémoire active"
            value = f"{point.value:.4f}" if point.metric == "cpu_cores" else str(round(point.value))
            entries.append({"timestamp": stamp(moment), "type": "métrique", "source": "Mimir",
                            "resource": resource, "observation": f"Échantillon mesuré : {value} {unit}.",
                            "reference": f"query_range {point.metric} {resource} @ {stamp(moment)}"})
    if logs:
        if logs.truncated:
            warnings.append("Limite de lignes Loki atteinte.")
        if logs.outside_window:
            warnings.append("Loki a renvoyé des lignes hors fenêtre ; vérifier les horloges des sources.")
        for log in logs.entries:
            nanoseconds = int(log["timestamp_ns"])
            moment = datetime.fromtimestamp(nanoseconds / 1_000_000_000, tz=timezone.utc)
            resource = "/".join(s for s in (log.get("namespace"), log.get("pod")) if s)
            entries.append({"timestamp": stamp(moment), "type": "log", "source": "Loki",
                            "resource": resource or "inconnue",
                            "observation": f"{log.get('container') or 'conteneur inconnu'} : {redact_text(log['message'])[:400]}",
                            "reference": f"Loki {resource} timestamp_ns={log['timestamp_ns']}"})
    unique = {(e["timestamp"], e["type"], e["source"], e["resource"], e["observation"]): e for e in entries}
    ordered = sorted(unique.values(), key=lambda e: (e["timestamp"], e["source"], e["reference"]))
    if len(ordered) > 100:
        warnings.append("Limite de 100 observations atteinte ; chronologie partielle.")
        ordered = ordered[:100]
    current = []
    if inventory and inventory.pods is not None:
        for pod in inventory.pods:
            if (not window["namespace"] or pod.namespace == window["namespace"]) and (
                    not window["pod"] or pod.name == window["pod"]):
                current.append({"resource": f"{pod.namespace}/{pod.name}", "phase": pod.phase,
                                "restarts_cumulative": pod.restarts, "observed_at": stamp(inventory.observed_at)})
                if len(current) >= 10:
                    warnings.append("Contexte de pods actuels limité à dix entrées.")
                    break
    missing = [name for name, value in sources.items() if value["status"] != "ok"]
    for kind, description in (("événement", "aucun événement pertinent conservé ; expiration possible"),
                              ("log", "aucun log pertinent"), ("métrique", "aucun échantillon métrique pertinent")):
        if not any(e["type"] == kind for e in ordered):
            missing.append(description)
    incomplete = bool(missing or warnings or any(v["status"] != "ok" for v in sources.values()))
    warnings.append("L'état et les redémarrages des pods sont actuels et ne prouvent pas leur état passé.")
    warnings.append("Les horloges peuvent diverger ; la proximité temporelle ne prouve aucune cause.")
    return {"scope": "incident", "start": stamp(start), "end": stamp(end),
            "timezone": window["timezone"], "namespace": window["namespace"], "pod": window["pod"],
            "timeline": ordered, "current_pods": current, "sources": sources,
            "missing": list(dict.fromkeys(missing)), "warnings": list(dict.fromkeys(warnings)),
            "data_status": "partial" if incomplete else "complete"}


def render(report, ai_note):
    resource = "/".join(s for s in (report["namespace"], report["pod"]) if s) or "cluster"
    lines = ["**Incident concerné**", f"Ressource : {resource} ; intervalle {report['start']} → {report['end']} "
             f"(fuseau fourni : {report['timezone']}).", "", "**Chronologie**"]
    lines += [f"- {e['timestamp']} · {e['source']} · {e['resource']} · {e['observation']} "
              f"[preuve : {e['reference']}]" for e in report["timeline"]] or ["- Aucune observation datée disponible."]
    lines += ["", "**Constats**", f"{len(report['timeline'])} observation(s) datée(s)."]
    for pod in report["current_pods"]:
        lines.append(f"État actuel seulement : {pod['resource']} {pod['phase']}, "
                     f"{pod['restarts_cumulative']} redémarrage(s) cumulés au relevé {pod['observed_at']}.")
    lines += ["", "**Hypothèses**", "Aucune cause racine certaine. Une proximité temporelle ne suffit pas "
              "à établir une causalité.", "", "**Données manquantes**"]
    lines += ["- " + item for item in report["missing"]] or ["- Aucune source signalée manquante."]
    lines += ["", "**Limites**"] + ["- " + item for item in report["warnings"]]
    lines += ["", "**Vérifications recommandées (lecture seule)**",
              "- Comparer les événements datés avec kubectl get events -A.",
              "- Consulter kubectl logs --timestamps -n <namespace> <pod> si le pod existe encore.",
              "- Comparer Mimir et Loki aux mêmes bornes UTC.", "", ai_note]
    return "\n".join(lines)


async def investigate(service, chat):
    question = next(m.content for m in reversed(chat.messages) if m.role == "user")
    window, error = parse_window(question)
    if error:
        return error, {"scope": "incident", "status": "clarification_required", "message": error}
    tasks = [service.cluster_status.inventory(),
             service.cluster_status.prometheus.history(window["start"].timestamp(),
                                                       window["end"].timestamp(), window["namespace"], window["pod"])]
    if window["namespace"]:
        tasks.append(service.loki.logs_between(int(window["start"].timestamp() * 10**9),
                                               int(window["end"].timestamp() * 10**9),
                                               window["namespace"], window["pod"]))
    results = await asyncio.gather(*tasks, return_exceptions=True)
    failures = {name: (value.message if isinstance(value, UpstreamError) else "Lecture indisponible")
                for name, value in zip(("kubernetes", "mimir", "loki"), results) if isinstance(value, Exception)}
    inventory = None if isinstance(results[0], Exception) else results[0]
    metrics = None if isinstance(results[1], Exception) else results[1]
    logs = None if len(results) < 3 or isinstance(results[2], Exception) else results[2]
    report = redact_data(build(window, inventory, metrics, logs, failures), service.sensitive_values())
    ai_note = "Mistral indisponible ; chronologie déterministe conservée."
    if service.sensitive_values() and report["timeline"]:
        try:
            # La sélection de priorité ne requiert ni le contenu des logs ni les messages des événements.
            model_input = {"sources": report["sources"], "timeline": [
                {key: entry[key] for key in ("timestamp", "type", "source", "resource")}
                for entry in report["timeline"]]}
            answer = await service.llm.complete({"messages": [
                {"role": "system", "content": "Choisis seulement une source à vérifier : kubernetes_events, mimir, loki ou none. Réponds en JSON avec la clé focus. Aucun fait ni cause."},
                {"role": "user", "content": json.dumps(model_input, ensure_ascii=False)}],
                "temperature": 0, "max_tokens": 32})
            focus = json.loads(answer["choices"][0]["message"]["content"])["focus"]
            names = {"kubernetes_events": "Kubernetes", "mimir": "Mimir", "loki": "Loki"}
            if focus in names and any(e["source"] == names[focus] for e in report["timeline"]):
                ai_note = f"Mistral suggère de vérifier d'abord {names[focus]} ; aucune cause n'en est déduite."
            elif focus != "none":
                ai_note = "Suggestion Mistral non étayée écartée ; chronologie déterministe conservée."
        except (UpstreamError, KeyError, IndexError, TypeError, ValueError):
            pass
    return render(report, ai_note), report
