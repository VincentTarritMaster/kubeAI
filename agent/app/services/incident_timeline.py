"""Deterministic incident timeline from bounded historical sources."""

import asyncio
from datetime import datetime, timezone
import json
import re
import unicodedata

from app.clients.base import UpstreamError
from app.services.privacy import redact_data, redact_text

HELP = ("Précisez une fenêtre complète avec fuseau, par exemple début=<ISO-8601> fin=<ISO-8601>, "
        "ou une date explicite telle que « le 19 octobre 2026 entre 18:25 et 18:33 UTC ». "
        "Ajoutez namespace=<namespace> et pod=<pod> (ou « pod <nom> dans le namespace <nom> »). "
        "Une heure isolée ou « ce matin » reste ambiguë.")


def incident_question(chat):
    question = next((m.content for m in reversed(chat.messages) if m.role == "user"), "").lower()
    return any(s in question for s in ("incident", "chronologie", "redémarr", "redemarr",
                                        "début=", "debut=",
                                        "que s'est-il passé", "que s’est-il passé"))


def _arg(question, name):
    found = re.search(r"(?<![\w-])" + name + r"\s*=\s*([^\s,;]+)", question, re.IGNORECASE)
    return found.group(1).rstrip(".?!)»\"'") if found else None


def _plain(value):
    return unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode().lower()


_MONTHS = {"janvier": 1, "fevrier": 2, "mars": 3, "avril": 4, "mai": 5, "juin": 6,
           "juillet": 7, "aout": 8, "septembre": 9, "octobre": 10, "novembre": 11,
           "decembre": 12}


def _clock(value):
    match = re.fullmatch(r"(\d{1,2})(?::|h)(\d{2})", value, re.IGNORECASE)
    if not match:
        return None
    hour, minute = map(int, match.groups())
    if hour > 23 or minute > 59:
        return None
    return hour, minute


def _components(question):
    """Extract only user-provided incident parameters; no example defaults."""
    first = _arg(question, "début") or _arg(question, "debut")
    last = _arg(question, "fin")
    start = end = None
    tz_label = None
    if first and last:
        try:
            start = datetime.fromisoformat(first.replace("Z", "+00:00"))
            end = datetime.fromisoformat(last.replace("Z", "+00:00"))
            tz_label = first[-6:] if re.search(r"[+-]\d{2}:?\d{2}$", first) else "UTC"
        except ValueError:
            start = end = None
    if start is None or end is None:
        plain = _plain(question)
        date = re.search(r"(?:\ble\s+|\bdu\s+)?(\d{1,2})\s+([a-z]+)\s+(\d{4})", plain)
        if not date:
            date = re.search(r"\b(\d{1,2})/(\d{1,2})/(\d{4})\b", plain)
            if date:
                day, month, year = map(int, date.groups())
            else:
                day = month = year = None
        else:
            day, month, year = int(date.group(1)), _MONTHS.get(date.group(2)), int(date.group(3))
        times = re.search(r"\b(?:entre\s+|de\s+)(\d{1,2}(?::|h)\d{2})\s+(?:et|a)\s+(\d{1,2}(?::|h)\d{2})\b", plain)
        has_zone = bool(re.search(r"\b(?:utc|gmt)\b|(?<!\w)z\b", plain))
        if day and month and year and times and has_zone:
            first_time, last_time = (_clock(times.group(i)) for i in (1, 2))
            if first_time and last_time:
                try:
                    start = datetime(year, month, day, *first_time, tzinfo=timezone.utc)
                    end = datetime(year, month, day, *last_time, tzinfo=timezone.utc)
                    tz_label = "UTC"
                except ValueError:
                    start = end = None
    namespace = _arg(question, "namespace")
    pod = _arg(question, "pod")
    plain = _plain(question)
    if not namespace:
        match = re.search(r"\bnamespace\s+([a-z0-9](?:[-a-z0-9]*[a-z0-9])?)\b", plain)
        namespace = match.group(1) if match else None
    if not pod:
        match = re.search(r"\bpod\s+([a-z0-9](?:[-a-z0-9]*[a-z0-9])?)\b", plain)
        pod = match.group(1) if match else None
    return {"start": start, "end": end, "timezone": tz_label, "namespace": namespace, "pod": pod}


def _validate_components(parts):
    start, end = parts["start"], parts["end"]
    if start is None or end is None:
        return None, HELP
    if start.tzinfo is None or end.tzinfo is None or start.utcoffset() is None or end.utcoffset() is None:
        return None, HELP
    start, end = start.astimezone(timezone.utc), end.astimezone(timezone.utc)
    if not start < end or (end - start).total_seconds() > 21600:
        return None, "Intervalle positif de six heures maximum requis. " + HELP
    namespace, pod = parts["namespace"], parts["pod"]
    valid = r"[a-z0-9](?:[-a-z0-9]*[a-z0-9])?"
    if namespace and (len(namespace) > 63 or not re.fullmatch(valid, namespace)):
        return None, "Namespace invalide. " + HELP
    if pod and (not namespace or len(pod) > 253 or not re.fullmatch(valid, pod)):
        return None, "Précisez le namespace et un pod valide. " + HELP
    return {"start": start, "end": end, "timezone": parts["timezone"] or "UTC",
            "namespace": namespace, "pod": pod}, None


def parse_window(question):
    return _validate_components(_components(question))


def parse_chat_window(chat):
    """Resolve a current request, inheriting only explicit same-incident context."""
    users = [message.content for message in chat.messages if message.role == "user"]
    current = users[-1] if users else ""
    parts = _components(current)
    normalized = _plain(current)
    if "meme incident" in normalized or "same incident" in normalized:
        missing = [key for key in ("start", "end", "namespace", "pod") if not parts.get(key)]
        for previous in reversed(users[:-1]):
            old = _components(previous)
            for key in missing:
                if old.get(key):
                    parts[key] = old[key]
            missing = [key for key in missing if not parts.get(key)]
            if not missing:
                break
    return _validate_components(parts)


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
            units = {"cpu_cores": "cœur(s) CPU", "app_cpu_cores": "cœur(s) CPU du processus applicatif",
                     "memory_working_set_bytes": "octets de mémoire active",
                     "restart_count": "redémarrage(s) cumulés", "cpu_limit_cores": "cœur(s) de limite CPU",
                     "memory_limit_bytes": "octets de limite mémoire"}
            unit = units.get(point.metric, point.metric)
            value = f"{point.value:.4f}" if point.metric in {"cpu_cores", "app_cpu_cores", "cpu_limit_cores"} else str(round(point.value))
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


async def collect(service, window, *, include_correlation=False):
    tasks = [service.cluster_status.inventory(),
             service.cluster_status.prometheus.history(window["start"].timestamp(),
                                                       window["end"].timestamp(), window["namespace"], window["pod"],
                                                       include_correlation=include_correlation)]
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
    return report, inventory, metrics, logs


async def investigate(service, chat):
    window, error = parse_chat_window(chat)
    if error:
        return error, {"scope": "incident", "status": "clarification_required", "message": error}
    # Include bounded KSM and application-process CPU history when available so
    # demo B can show all real, timestamped sources in one ordered timeline.
    report, _, _, _ = await collect(service, window, include_correlation=True)
    provider_name = service.llm.provider_name
    ai_note = f"{provider_name} indisponible ; chronologie déterministe conservée."
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
                ai_note = f"{provider_name} suggère de vérifier d'abord {names[focus]} ; aucune cause n'en est déduite."
            elif focus != "none":
                ai_note = f"Suggestion {provider_name} non étayée écartée ; chronologie déterministe conservée."
        except (UpstreamError, KeyError, IndexError, TypeError, ValueError):
            pass
    return render(report, ai_note), report
