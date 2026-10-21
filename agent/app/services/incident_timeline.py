"""Deterministic incident timeline from bounded historical sources."""

import asyncio
from datetime import datetime, timedelta, timezone
import json
import re
import unicodedata

from app.clients.base import UpstreamError
from app.clients.loki_client import HistoricalLogs
from app.clients.prometheus_client import HistoricalMetrics, HistoricalPoint
from app.conversation import dialogue, explicit_intent
from app.services.privacy import redact_data, redact_text
from app.services.presentation import DEFAULT_ZONE, day_heading, full_time, local_time, missing_summary, offset_label, short_time, technical_details

HELP = ("Indiquez au moins une date explicite, par exemple « incident du pod mon-pod le 19 octobre », "
        "ou une fenêtre avec fuseau, par exemple début=<ISO-8601> fin=<ISO-8601>. "
        "Une heure isolée ou « ce matin » reste ambiguë.")


def incident_question(chat):
    question = next((m.content for m in reversed(chat.messages) if m.role == "user"), "").lower()
    return is_incident_text(question)


def is_incident_text(question):
    return explicit_intent(question) == "incident"


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
    day_date = None
    if start is None or end is None:
        plain = _plain(question)
        date = re.search(r"\b(\d{1,2})\s+([a-z]+)(?:\s+(\d{4}))?\b", plain)
        if not date:
            date = re.search(r"\b(\d{1,2})/(\d{1,2})/(\d{4})\b", plain)
            if date:
                day, month, year = map(int, date.groups())
            else:
                day = month = year = None
        else:
            day, month = int(date.group(1)), _MONTHS.get(date.group(2))
            year = int(date.group(3)) if date.group(3) else None
        times = re.search(r"\b(?:entre\s+|de\s+)(\d{1,2}(?::|h)\d{2})\s+(?:et|a)\s+(\d{1,2}(?::|h)\d{2})\b", plain)
        has_zone = bool(re.search(r"\b(?:utc|gmt)\b|(?<!\w)z\b", plain))
        if day and month and not year:
            # An omitted year means the most recent occurrence of this day in UTC.
            now = datetime.now(timezone.utc)
            year = next((candidate for candidate in range(now.year, now.year - 5, -1)
                         if _valid_past_day(candidate, month, day, now)), None)
        if day and month and year:
            try:
                day_date = datetime(year, month, day, tzinfo=timezone.utc)
            except ValueError:
                pass
        if day_date and times and has_zone:
            first_time, last_time = (_clock(times.group(i)) for i in (1, 2))
            if first_time and last_time:
                try:
                    start = datetime(year, month, day, *first_time, tzinfo=timezone.utc)
                    end = datetime(year, month, day, *last_time, tzinfo=timezone.utc)
                    tz_label = "UTC"
                except ValueError:
                    start = end = None
        if day_date is None and not (first or last):
            today = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
            if re.search(r"\bavant.hier\b", plain):
                day_date = today - timedelta(days=2)
            elif re.search(r"\bhier\b", plain):
                day_date = today - timedelta(days=1)
            elif re.search(r"\baujourd.hui\b", plain):
                day_date = today
    namespace = _arg(question, "namespace")
    pod = _arg(question, "pod")
    plain = _plain(question)
    if not namespace:
        match = re.search(r"\bnamespace\s+([a-z0-9](?:[-a-z0-9]*[a-z0-9])?)\b", plain)
        namespace = match.group(1) if match else None
    if not pod:
        match = re.search(r"\bpod\s+([a-z0-9](?:[-a-z0-9]*[a-z0-9])?)\b", plain)
        pod = match.group(1) if match and match.group(1) not in {"a-t-il", "est", "precedent", "concerne", "suivant"} else None
    if not pod:
        match = re.search(r"\bincident\s+([a-z0-9](?:[-a-z0-9]*[a-z0-9])?)\b", plain)
        if match and match.group(1) not in {"precedent", "precedente", "actuel", "passe", "suivant", "du", "de", "le", "debut", "fin"}:
            pod = match.group(1)
    qualified = re.search(r"\b([a-z0-9][a-z0-9-]{0,62})/([a-z0-9][a-z0-9-]{0,252})\b", plain)
    if qualified and re.search(r"[a-z]", qualified.group(0)):
        namespace, pod = qualified.groups()
    return {"start": start, "end": end, "timezone": tz_label, "namespace": namespace,
            "pod": pod, "date": day_date if not (first or last) else None}


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
    return _validate_components(chat_components(chat))


def chat_components(chat):
    """Use the incident thread folded before trimming, including clarifications."""
    state = dialogue(chat)
    users = [message.content for message in chat.messages if message.role == "user"]
    parts = _components("")
    for turn in state.incident_turns or users[-1:]:
        update = _components(turn)
        if update["date"] and not update["start"]:
            parts["start"] = parts["end"] = None
        if update["start"] or update["end"]:
            parts["date"] = None
        parts.update({key: value for key, value in update.items() if value is not None})
    return parts


def _valid_past_day(year, month, day, now):
    try:
        return datetime(year, month, day, tzinfo=timezone.utc) <= now
    except ValueError:
        return False


def stamp(value):
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _representative(items, budget, *, extrema=False, required=()):
    if len(items) <= budget:
        return items
    selected = {0, len(items) - 1}
    selected.update(index for index, item in enumerate(items) if item["reference"] in required)
    if extrema:
        def value(index):
            found = re.search(r"mesuré\s*:\s*([0-9.]+)", items[index]["observation"])
            return float(found.group(1).rstrip(".")) if found else 0
        selected.update((min(range(len(items)), key=value), max(range(len(items)), key=value)))
    for index in range(max(1, budget)):
        selected.add(round(index * (len(items) - 1) / max(1, budget - 1)))
        if len(selected) >= budget:
            break
    return [items[index] for index in sorted(selected)[:budget]]


def _bounded_timeline(ordered):
    """Keep late logs and metric extrema when the evidence budget is reached."""
    if len(ordered) <= 100:
        return ordered
    logs = [item for item in ordered if item["type"] == "log"]
    events = [item for item in ordered if item["type"] == "événement"]
    retained = _representative(logs, 50)
    retained.extend(_representative(events, 60 - len(retained)))
    series = {}
    for item in ordered:
        if item["type"] == "métrique":
            key = (item["reference"].split(" ")[1], item["resource"])
            series.setdefault(key, []).append(item)
    budget = 100 - len(retained)
    groups = sorted(series.values(), key=lambda group: "app_cpu_cores" not in group[0]["reference"])
    for index, group in enumerate(groups):
        share = max(0, budget // max(1, len(groups) - index))
        required = set()
        if "app_cpu_cores" in group[0]["reference"]:
            share = max(0, min(24, budget - 2 * (len(groups) - index - 1)))
            for marker in logs:
                if marker["resource"] == group[0]["resource"] and "phase=" in marker["observation"]:
                    before = [item for item in group if item["timestamp"] < marker["timestamp"]]
                    after = [item for item in group if item["timestamp"] >= marker["timestamp"]]
                    required.update(item["reference"] for item in before[-1:] + after[:1])
        if share:
            chosen = _representative(group, share, extrema=True, required=required)
            retained.extend(chosen)
            budget -= len(chosen)
    return sorted(retained, key=lambda item: (item["timestamp"], item["source"], item["reference"]))[:100]


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
        ordered = _bounded_timeline(ordered)
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
                              ("log", "aucun log pertinent conservé ; expiration ou absence possible"),
                              ("métrique", "aucun échantillon métrique pertinent")):
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


def application_cpu_episode(logs, points):
    """Choose one observed phase cycle without mixing separate executions."""
    markers = []
    for item in logs:
        match = re.search(r"\bphase\s*=\s*(normal|anomaly_start|recovered|complete)\b", item["observation"], re.I)
        if match:
            moment = datetime.fromisoformat(item["timestamp"].replace("Z", "+00:00")).timestamp()
            markers.append((moment, match.group(1).lower(), item))
    markers.sort(key=lambda item: item[0])
    points = sorted(points, key=lambda point: point.timestamp)
    episodes = []
    for index, (start, phase, marker) in enumerate(markers):
        if phase != "anomaly_start":
            continue
        reset = next((moment for moment, kind, _ in reversed(markers[:index])
                      if kind in {"normal", "anomaly_start"}), start - 300)
        boundary = next((moment for moment, kind, _ in markers[index + 1:]
                         if kind in {"normal", "anomaly_start"}), float("inf"))
        recovery = next(((moment, item) for moment, kind, item in markers[index + 1:]
                         if kind == "recovered" and moment < boundary), None)
        recovery_time = recovery[0] if recovery else None
        before = [point for point in points if max(reset, start - 300) <= point.timestamp < start]
        during = [point for point in points if start <= point.timestamp < min(
            boundary, recovery_time if recovery else start + 300)]
        after = [point for point in points if recovery and recovery_time <= point.timestamp <= recovery_time + 90
                 and point.timestamp < boundary]
        baseline = before[-1] if before else None
        peak = max(during, key=lambda point: point.value, default=None)
        measured_rise = bool(baseline and peak and peak.value >= max(baseline.value * 2, baseline.value + 0.01))
        episodes.append({"start_marker": marker, "start_time": start,
                         "recovery_marker": recovery[1] if recovery else None,
                         "recovery_time": recovery_time, "before": before, "during": during,
                         "after": after, "baseline": baseline, "peak": peak,
                         "measured_rise": measured_rise})
    if not episodes:
        return None
    # A later run with measured context is useful even if an earlier run was
    # interrupted before its first process_cpu_seconds_total scrape.
    selected = max(episodes, key=lambda episode: (episode["measured_rise"],
                   bool(episode["baseline"] and episode["peak"]), episode["start_time"]))
    return {**selected, "count": len(episodes)}


def render(report, ai_note, *, zone=DEFAULT_ZONE):
    resource = "/".join(s for s in (report["namespace"], report["pod"]) if s) or "cluster"
    timeline = report["timeline"]
    phases = [item for item in timeline if item["source"] == "Loki" and re.search(
        r"\bphase\s*=\s*(normal|anomaly_start|recovered|complete)\b", item["observation"], re.I)]
    app_points = []
    for item in timeline:
        if item["source"] == "Mimir" and "query_range app_cpu_cores " in item["reference"]:
            match = re.search(r"Échantillon mesuré\s*:\s*([0-9]+(?:\.[0-9]+)?)", item["observation"])
            if match:
                moment = datetime.fromisoformat(item["timestamp"].replace("Z", "+00:00")).timestamp()
                app_points.append(HistoricalPoint(moment, "app_cpu_cores", float(match.group(1)), {}))
    episode = application_cpu_episode(phases, app_points)
    significant = list(phases[:10])
    significant.extend(item for item in timeline if item["source"] == "Kubernetes" and re.search(
        r"warning|failed|oomkilled|unhealthy|backoff", item["observation"], re.I))
    if episode and episode["measured_rise"]:
        peak = episode["peak"]
        significant.extend(item for item in timeline if item["source"] == "Mimir"
                           and "query_range app_cpu_cores " in item["reference"]
                           and abs(datetime.fromisoformat(item["timestamp"].replace("Z", "+00:00")).timestamp()
                                   - peak.timestamp) < 0.001)
    if not significant:
        significant = [item for item in timeline if item["source"] == "Loki" and re.search(
            r"error|failed|anomal|incident|timeout", item["observation"], re.I)][:6]
    significant = sorted({(item["timestamp"], item["reference"]): item for item in significant}.values(),
                         key=lambda item: (item["timestamp"], item["reference"]))[:12]
    anomalies = sum(bool(re.search(r"\bphase\s*=\s*anomaly_start\b", item["observation"], re.I))
                    for item in phases)
    anomaly_summary = (f"{anomalies} séquences d'anomalie signalées" if anomalies != 1
                       else "une séquence d'anomalie signalée")
    start, end = full_time(report["start"], zone), full_time(report["end"], zone)
    lines = ["## Incident observé", "",
             f"**Ressource :** `{resource}`. **Période :** {start} au {end}.", "",
             (f"**Résumé :** {anomaly_summary} dans les logs ; "
              f"{len(timeline)} observations datées disponibles."
              if anomalies else f"**Résumé :** {len(timeline)} observations datées disponibles ; "
              "aucun marqueur de début d'anomalie établi dans les données retenues."),
             "Les marqueurs décrivent le déroulement observé ; ils ne prouvent pas une cause racine.", "",
             "## Chronologie de l'incident", ""]
    previous_day = None
    anomaly_number = 0
    normal_number = 0
    for number, item in enumerate(significant, 1):
        local = local_time(item["timestamp"], zone)
        day = local.date()
        if day != previous_day:
            offsets = {offset_label(local_time(entry["timestamp"], zone)) for entry in significant
                       if local_time(entry["timestamp"], zone).date() == day}
            lines.extend(day_heading(local, zone, mixed_offset=len(offsets) > 1))
            previous_day = day
        phase = re.search(r"\bphase\s*=\s*(normal|anomaly_start|recovered|complete)\b",
                          item["observation"], re.I)
        if phase:
            kind = phase.group(1).lower()
            if kind == "anomaly_start":
                anomaly_number += 1
            if kind == "normal":
                normal_number += 1
            title = {"normal": "Fonctionnement normal signalé" if normal_number == 1
                     else "Retour au fonctionnement normal signalé", "anomaly_start":
                     "Début d'une anomalie" if anomaly_number == 1 else "Nouvelle anomalie",
                     "recovered": "Récupération signalée", "complete": "Fin du scénario"}[kind]
            description = f"Marqueur `{kind}` enregistré dans Loki."
        elif item["source"] == "Mimir":
            title = "Hausse du CPU du processus"
            match = re.search(r"Échantillon mesuré\s*:\s*([0-9.]+)", item["observation"])
            description = (f"Consommation mesurée à {match.group(1).replace('.', ',')} cœur sur Mimir."
                           if match else item["observation"])
        else:
            title = "Avertissement Kubernetes" if item["source"] == "Kubernetes" else "Anomalie dans les logs"
            description = f"{item['source']} : {item['observation']}"
        clock = short_time(item["timestamp"], zone)
        if len({offset_label(local_time(entry["timestamp"], zone)) for entry in significant
                if local_time(entry["timestamp"], zone).date() == day}) > 1:
            clock += f" ({offset_label(local)})"
        lines.extend([f"{number}. **{clock} — {title}**", "", f"   {description}", ""])
    if not significant:
        lines.append("Aucune observation datée significative n'a été retrouvée dans la fenêtre consultée.")
    lines += ["", "**CPU du processus applicatif**"]
    if episode and episode["baseline"] and episode["peak"]:
        qualifier = "hausse mesurée" if episode["measured_rise"] else "pas de hausse significative mesurée"
        baseline = f"{episode['baseline'].value:.4f}".replace(".", ",")
        peak = f"{episode['peak'].value:.4f}".replace(".", ",")
        lines.append(f"- Cycle du {full_time(episode['start_marker']['timestamp'], zone)} : "
                     f"{baseline} → {peak} cœur ; {qualifier}.")
        if episode["count"] > 1:
            lines.append(f"- {episode['count']} cycles détectés ; cette comparaison concerne un seul cycle.")
    else:
        lines.append("- Comparaison avant/après indisponible pour le cycle observé.")
    lines.append("- Cette métrique mesure le processus applicatif ; elle ne prouve ni la saturation du conteneur ni un throttling CPU.")
    if report["missing"]:
        lines += ["", "**Données manquantes :** " + missing_summary(report["missing"]) + "."]
    lines += ["", "**Conclusion :** aucune cause racine certaine à ce stade ; rapprocher les logs et les métriques du même cycle."]
    technical = [f"Fenêtre UTC : {report['start']} → {report['end']} ; fuseau demandé : {report['timezone']}",
                 f"Observations et références détaillées ({len(timeline)} observations) :"]
    technical += [f"{item['timestamp']} | {item['source']} | {item['resource']} | "
                  f"{item['observation']} | preuve : {item['reference']}" for item in timeline]
    technical += [f"Pod actuel seulement : {pod['resource']} {pod['phase']}, "
                  f"{pod['restarts_cumulative']} redémarrages cumulés au {pod['observed_at']}"
                  for pod in report["current_pods"]]
    technical += [f"Source {name} : {value['status']}" for name, value in report.get("sources", {}).items()]
    technical += ["Donnée manquante : " + item for item in report["missing"]]
    technical += ["Limite : " + item for item in report["warnings"]]
    technical.append(ai_note)
    lines += ["", technical_details(technical)]
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
    if include_correlation and metrics is not None:
        available = {point.metric for point in metrics.points}
        for name, description in (("app_cpu_cores", "CPU applicatif process_cpu_seconds_total absent sur cette fenêtre"),
                                  ("cpu_cores", "CPU historique des conteneurs absent sur cette fenêtre"),
                                  ("memory_working_set_bytes", "mémoire historique des conteneurs absente sur cette fenêtre")):
            if name not in available:
                report["missing"].append(description)
                report["data_status"] = "partial"
    return report, inventory, metrics, logs


async def resolve_request(service, chat):
    """Resolve a bounded search from user facts and read-only inventory data."""
    parts = chat_components(chat)
    default_note = None
    if not (parts["start"] or parts["end"] or parts["date"]):
        text = " ".join(dialogue(chat).incident_turns)
        if parts["pod"] and not re.search(r"\b(?:matin|soir|heures?|\d+\s*h)\b|(?:debut|début|fin)=", text, re.I):
            now = datetime.now(timezone.utc)
            parts.update(start=now - timedelta(hours=1), end=now, timezone="UTC")
            default_note = "Aucune date indiquée : recherche sur la dernière heure en UTC ; cet intervalle n'est pas une heure d'incident supposée."
        else:
            return None, HELP, None
    inventory = None
    if parts["pod"] and not parts["namespace"]:
        try:
            inventory = await service.cluster_status.inventory()
        except UpstreamError:
            return None, "Kubernetes est indisponible : indiquez le namespace du pod pour poursuivre.", None
        pod = parts["pod"]
        resources = {(item.namespace, item.name) for item in inventory.pods or [] if item.name}
        resources.update((item.namespace, item.involved_name) for item in inventory.events or []
                         if item.involved_kind == "Pod" and item.involved_name and item.namespace)
        exact = {(ns, name) for ns, name in resources if name == pod}
        matches = exact or {(ns, name) for ns, name in resources if name.startswith(pod + "-")}
        state = dialogue(chat)
        if state.selection is not None:
            if state.selection >= len(state.choices) or state.choices[state.selection] not in matches:
                return None, "Ce choix ne correspond pas aux pods vérifiés. Quel namespace et quel pod souhaitez-vous examiner ?", None
            parts["namespace"], parts["pod"] = state.choices[state.selection]
            matches = {state.choices[state.selection]}
        if len(matches) > 1:
            choices = ", ".join(f"{ns}/{name}" for ns, name in sorted(matches)[:6])
            return None, f"Plusieurs pods correspondent à « {pod} » : {choices}. Lequel faut-il examiner ?", None
        if len(matches) == 1:
            parts["namespace"], parts["pod"] = next(iter(matches))
        else:
            return None, (f"Le pod « {pod} » n'apparaît ni dans l'inventaire actuel ni dans les événements "
                          "conservés. Dans quel namespace se trouvait-il ?"), None

    valid = r"[a-z0-9](?:[-a-z0-9]*[a-z0-9])?"
    if parts["namespace"] and (len(parts["namespace"]) > 63 or
                               not re.fullmatch(valid, parts["namespace"])):
        return None, "Namespace invalide ; indiquez son nom Kubernetes exact.", None
    if parts["pod"] and (len(parts["pod"]) > 253 or not re.fullmatch(valid, parts["pod"])):
        return None, "Nom de pod invalide ; indiquez son nom Kubernetes exact.", None

    if parts["start"] or parts["end"]:
        window, error = _validate_components(parts)
        if window and default_note:
            window["search_note"] = default_note
        return window, error, None
    if not parts["date"]:
        return None, HELP, None
    if not parts["pod"]:
        return None, "Quel pod faut-il examiner pour cette date ?", None
    if not parts["namespace"]:
        return None, "Quel pod ou namespace faut-il examiner pour cette date ?", None
    return await scan_date(service, parts, inventory)


def _historical_log_signal(message):
    """Rank an observed failure, never an application's name or healthy phase."""
    phase = re.search(r"\bphase\s*=\s*([a-z_]+)\b", message, re.I)
    if phase:
        value = phase.group(1).lower()
        if value in {"normal", "healthy", "recovered", "complete", "recovery"}:
            return 0
        if value in {"anomaly_start", "anomaly", "degraded", "failed", "failure", "error"}:
            return 4
    if re.search(r"\b(?:error|exception|failed|timeout|oomkilled|outofmemory|crash|refused|panic)\b|\banomal", message, re.I):
        return 2
    return 0


def _refined_day_window(day, candidates, logs):
    """Keep nearby before/after evidence while retaining a useful CPU cadence."""
    _, anchor = min(candidates, key=lambda item: (-item[0], item[1]))
    nearby = [moment for _, moment in candidates if abs((moment - anchor).total_seconds()) <= 600]
    # A healthy phase is context for a real failure, never an anomaly by itself.
    for log in logs:
        if not re.search(r"\bphase\s*=\s*(?:normal|healthy|recovered|complete|recovery)\b", log["message"], re.I):
            continue
        moment = datetime.fromtimestamp(int(log["timestamp_ns"]) / 10**9, timezone.utc)
        if abs((moment - anchor).total_seconds()) <= 600:
            nearby.append(moment)
    start = max(day, min(nearby) - timedelta(minutes=2))
    end = min(day + timedelta(days=1), max(nearby) + timedelta(minutes=2))
    # Ten minutes supplies baseline and recovery samples even for a single log.
    # The +/- ten-minute neighborhood plus padding stays under thirty minutes,
    # so all six historical series fit the existing budget at a 30-second step.
    if end - start < timedelta(minutes=10):
        middle = start + (end - start) / 2
        start = max(day, middle - timedelta(minutes=5))
        end = min(day + timedelta(days=1), start + timedelta(minutes=10))
        start = max(day, end - timedelta(minutes=10))
    return start, end, anchor


async def scan_date(service, parts, inventory=None):
    """Search the UTC day in four independent six-hour source-bounded requests."""
    day = parts["date"]
    if inventory is None:
        try:
            inventory = await service.cluster_status.inventory()
        except UpstreamError:
            inventory = None
    all_points, all_logs, candidate_points, candidate_logs, failures = [], [], [], [], {}
    scan_warnings = []
    metrics_ok = logs_ok = 0
    metrics_partial = metrics_truncated = logs_truncated = False

    async def segment(offset):
        start = day + timedelta(hours=offset)
        end = start + timedelta(hours=6)
        requests = [service.cluster_status.prometheus.history(start.timestamp(), end.timestamp(),
                                                               parts["namespace"], parts["pod"])]
        requests.append(service.loki.logs_between(int(start.timestamp() * 10**9),
                                                  int(end.timestamp() * 10**9),
                                                  parts["namespace"], parts["pod"]))
        return await asyncio.gather(*requests, return_exceptions=True)

    for metrics, logs in await asyncio.gather(*(segment(offset) for offset in range(0, 24, 6))):
        if isinstance(metrics, Exception):
            failures["mimir"] = metrics.message if isinstance(metrics, UpstreamError) else "Lecture indisponible"
            metrics = None
        else:
            metrics_ok += 1
            scan_warnings.extend(metrics.warnings[:4])
            metrics_partial |= metrics.partial
            metrics_truncated |= metrics.truncated
        if isinstance(logs, Exception):
            failures["loki"] = logs.message if isinstance(logs, UpstreamError) else "Lecture indisponible"
            logs = None
        else:
            logs_ok += 1
            if logs.truncated:
                logs_truncated = True
                scan_warnings.append("Limite de lignes Loki atteinte pendant le balayage.")
        if metrics:
            # Sample each segment evenly so later hours remain represented.
            points = sorted(metrics.points, key=lambda item: item.timestamp)
            candidate_points.extend(points)
            stride = max(1, (len(points) + 23) // 24)
            all_points.extend(points[::stride][:24])
        if logs:
            candidate_logs.extend(logs.entries)
            all_logs.extend(logs.entries[:12])

    candidates = []
    if inventory:
        for event in inventory.events or []:
            if (event.observed_at and day <= event.observed_at < day + timedelta(days=1)
                    and event.namespace == parts["namespace"]
                    and (not parts["pod"] or event.involved_name == parts["pod"])
                    and (event.type == "Warning" or re.search(
                        r"failed|backoff|oom|unhealthy|error", event.reason or "", re.I))):
                candidates.append((3, event.observed_at))
    for log in candidate_logs:
        rank = _historical_log_signal(log["message"])
        if rank:
            candidates.append((rank, datetime.fromtimestamp(int(log["timestamp_ns"]) / 10**9, timezone.utc)))
    by_metric = {}
    for point in sorted(candidate_points, key=lambda item: item.timestamp):
        if point.metric not in {"cpu_cores", "app_cpu_cores", "memory_working_set_bytes"}:
            continue
        metric_key = (point.metric, point.labels.get("namespace"), point.labels.get("pod"))
        previous = by_metric.get(metric_key)
        if previous and 0 < point.timestamp - previous.timestamp <= 900 and (
                point.value >= 2 * max(previous.value, 0.001)) and (
                point.value - previous.value >= (0.05 if "cpu" in point.metric else 50 * 1024 * 1024)):
            candidates.append((1, datetime.fromtimestamp(point.timestamp, timezone.utc)))
        by_metric[metric_key] = point

    note = (f"Date interprétée comme le {day:%Y-%m-%d} UTC ; balayage de quatre fenêtres "
            "de six heures. L'heure de l'incident n'a pas été supposée.")
    if candidates:
        start, end, moment = _refined_day_window(day, candidates, candidate_logs)
        detail = (f" Fenêtre approfondie autour d'un signal daté à {stamp(moment)}, avec contexte "
                  "avant/après et un pas métrique de 30 secondes pour les séries disponibles.")
        if any(not start <= candidate <= end for _, candidate in candidates):
            detail += " D'autres signaux existent hors de cette fenêtre ; ils ne sont pas attribués au même incident."
        window = {"start": start, "end": end, "timezone": "UTC",
                  "namespace": parts["namespace"], "pod": parts["pod"],
                  "search_note": note + detail}
        return window, None, None

    # No identifiable anomaly: report the whole searched day from bounded scans.
    combined = (HistoricalMetrics(all_points, scan_warnings, metrics_truncated,
                                  metrics_partial or metrics_ok < 4) if metrics_ok else None)
    combined_logs = HistoricalLogs(all_logs, logs_truncated) if logs_ok else None
    if metrics_ok:
        failures.pop("mimir", None)
    if logs_ok:
        failures.pop("loki", None)
    day_window = {"start": day, "end": day + timedelta(days=1), "timezone": "UTC",
                  "namespace": parts["namespace"], "pod": parts["pod"]}
    report = redact_data(build(day_window, inventory, combined, combined_logs, failures), service.sensitive_values())
    if metrics_ok < 4 or logs_ok < 4:
        report["warnings"].append("Une ou plusieurs fenêtres de balayage n'ont pas pu être lues ; résultat partiel.")
        report["data_status"] = "partial"
    report["warnings"].append(note + " Aucune anomalie horodatée n'a permis de resserrer la recherche.")
    return day_window, None, (report, inventory, combined, combined_logs)


async def investigate(service, chat):
    window, error, scanned = await resolve_request(service, chat)
    if error:
        return error, {"scope": "incident", "status": "clarification_required", "message": error}
    # Include bounded KSM and application-process CPU history when available so
    # demo B can show all real, timestamped sources in one ordered timeline.
    report, _, _, _ = scanned or await collect(service, window, include_correlation=True)
    if window.get("search_note"):
        report["warnings"].append(window["search_note"])
    provider_name = service.llm.provider_name
    sensitive_values = service.sensitive_values()
    local_provider = service.llm.settings.llm_provider == "local"
    can_call = bool(sensitive_values) or local_provider
    if not report["timeline"]:
        ai_note = f"{provider_name} non sollicité : aucune observation historique à prioriser ; chronologie déterministe conservée."
    elif not can_call:
        ai_note = f"{provider_name} non sollicité : clé non configurée ; chronologie déterministe conservée."
    else:
        ai_note = f"{provider_name} n'a pas été sollicité ; chronologie déterministe conservée."
    if can_call and report["timeline"]:
        try:
            # La sélection de priorité ne requiert ni le contenu des logs ni les messages des événements.
            model_input = {"sources": report["sources"], "timeline": [
                {key: entry[key] for key in ("timestamp", "type", "source", "resource")}
                for entry in report["timeline"]]}
            focus = await service.llm.select_option(
                prompt="Choisis seulement une source à vérifier. Aucun fait ni cause.",
                data=model_input, key="focus", options=("kubernetes_events", "mimir", "loki", "none"))
            names = {"kubernetes_events": "Kubernetes", "mimir": "Mimir", "loki": "Loki"}
            if focus in names and any(e["source"] == names[focus] for e in report["timeline"]):
                ai_note = f"{provider_name} suggère de vérifier d'abord {names[focus]} ; aucune cause n'en est déduite."
            elif focus == "none":
                ai_note = f"{provider_name} a répondu ; aucune source n'a été priorisée. Chronologie déterministe conservée."
            else:
                ai_note = f"Réponse de {provider_name} rejetée : priorité proposée non étayée par les sources présentes."
        except UpstreamError as error:
            if "Réponse JSON" in error.message or "Sélection structurée" in error.message:
                ai_note = f"Réponse API de {provider_name} rejetée : format de réponse invalide. Chronologie déterministe conservée."
            else:
                ai_note = f"Appel à {provider_name} échoué ({error.message}) ; chronologie déterministe conservée."
        except (KeyError, IndexError, TypeError, ValueError):
            ai_note = f"Réponse de {provider_name} rejetée : format ou priorité invalide. Chronologie déterministe conservée."
    return render(report, ai_note, zone=service.llm.settings.display_timezone), report
