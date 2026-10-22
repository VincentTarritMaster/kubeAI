"""Deterministic incident timeline from bounded historical sources."""

import asyncio
from datetime import datetime, timedelta, timezone
import json
import re
import unicodedata
from zoneinfo import ZoneInfo

from app.clients.base import UpstreamError
from app.clients.loki_client import HistoricalLogs
from app.clients.prometheus_client import HistoricalMetrics, HistoricalPoint
from app.conversation import dialogue, explicit_intent
from app.services.privacy import redact_data, redact_text
from app.services.presentation import (DEFAULT_ZONE, date_label, day_heading, local_time,
                                       missing_summary, offset_label, short_time, technical_details)

HELP = ("Indiquez une date complète et une plage horaire explicite, par exemple "
        "« 22 octobre 2026 entre 09:40 et 09:55 » ou début=<ISO-8601> fin=<ISO-8601>. "
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


_MONTHS = {"janvier": 1, "jan": 1, "january": 1, "fevrier": 2, "fev": 2,
           "feb": 2, "february": 2, "mars": 3, "mar": 3, "march": 3,
           "avril": 4, "avr": 4, "apr": 4, "april": 4, "mai": 5, "may": 5,
           "juin": 6, "jun": 6, "june": 6, "juillet": 7, "juil": 7,
           "jul": 7, "july": 7, "aout": 8, "aug": 8, "august": 8,
           "septembre": 9, "sept": 9, "sep": 9, "september": 9,
           "octobre": 10, "oct": 10, "october": 10,
           "novembre": 11, "nov": 11, "november": 11,
           "decembre": 12, "dec": 12, "december": 12}


def _date_fields(text):
    """Return only a date actually present in the request (year may be absent)."""
    found = re.search(r"\b(\d{4})-(\d{1,2})-(\d{1,2})\b", text)
    if found:
        return tuple(map(int, found.groups()))
    found = re.search(r"\b(\d{1,2})[./-](\d{1,2})[./-](\d{4})\b", text)
    if found:
        day, month, year = map(int, found.groups())
        return year, month, day
    found = re.search(r"\b(\d{1,2})\s+([a-z]+)\.?(?:\s+(\d{4}))?\b", text)
    if found and found.group(2) in _MONTHS:
        return (int(found.group(3)) if found.group(3) else None,
                _MONTHS[found.group(2)], int(found.group(1)))
    found = re.search(r"\b([a-z]+)\.?\s+(\d{1,2}),?\s+(\d{4})\b", text)
    if found and found.group(1) in _MONTHS:
        return int(found.group(3)), _MONTHS[found.group(1)], int(found.group(2))
    return None


def _timestamp(value, default_zone=DEFAULT_ZONE):
    """Keep explicit UTC/offsets; interpret offset-free local input in Zurich."""
    parsed = datetime.fromisoformat(value.rstrip(".,;!?»").replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=ZoneInfo(default_zone))


def _date_span(fields, zone):
    year, month, day = fields
    start = datetime(year, month, day, tzinfo=zone)
    following = datetime(year, month, day) + timedelta(days=1)
    end = following.replace(tzinfo=zone)
    return start.astimezone(timezone.utc), end.astimezone(timezone.utc)


def _local_clock(fields, clock, zone):
    year, month, day = fields
    naive = datetime(year, month, day, *clock)
    choices = []
    for fold in (0, 1):
        aware = naive.replace(tzinfo=zone, fold=fold)
        back = aware.astimezone(timezone.utc).astimezone(zone).replace(tzinfo=None)
        if back == naive:
            choices.append(aware)
    offsets = {value.utcoffset() for value in choices}
    if len(offsets) != 1:
        raise ValueError("Heure locale inexistante ou ambiguë lors du changement d'heure ; indiquez un décalage UTC explicite.")
    return choices[0]


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
            start = _timestamp(first)
            end = _timestamp(last)
            tz_label = ("UTC" if first.upper().endswith("Z") else
                        first[-6:] if re.search(r"[+-]\d{2}:?\d{2}$", first) else DEFAULT_ZONE)
        except ValueError:
            start = end = None
    plain = _plain(question)
    fields = _date_fields(plain) if not (first or last) else None
    times = re.search(r"\b(?:entre|between|de|from)\s+(\d{1,2}(?::|h)\d{2})\s+(?:et|a|to|and|-)\s+(\d{1,2}(?::|h)\d{2})\b", plain)
    clocks = tuple(_clock(times.group(i)) for i in (1, 2)) if times else None
    if clocks and not all(clocks):
        clocks = None
    explicit_utc = bool(re.search(r"\b(?:utc|gmt)\b", plain))
    zone = timezone.utc if explicit_utc else ZoneInfo(DEFAULT_ZONE)
    year_hint = re.search(r"\b(?:annee|year)\s*=\s*(\d{4})\b", plain)
    if not year_hint:
        year_hint = re.fullmatch(r"\s*(\d{4})\s*", plain)
    instant = None
    if not (first or last):
        match = re.search(r"\b\d{4}-\d\d-\d\d[Tt]\d\d:\d\d(?::\d\d(?:\.\d+)?)?(?:Z|[+-]\d\d:\d\d)?\b", question)
        if match:
            try:
                instant = _timestamp(match.group(0))
            except ValueError:
                pass
    day_date = day_end = None
    time_error = None
    if fields and fields[0] is not None:
        try:
            day_date, day_end = _date_span(fields, zone)
            tz_label = "UTC" if explicit_utc else DEFAULT_ZONE
            if clocks:
                start = _local_clock(fields, clocks[0], zone)
                end = _local_clock(fields, clocks[1], zone)
        except ValueError as error:
            if "changement d'heure" in str(error):
                time_error = str(error)
            fields = None
            day_date = day_end = None
    if not fields and not (first or last):
        relative = (-1 if re.search(r"\bhier\b", plain) else
                    0 if re.search(r"\baujourd.hui\b", plain) else None)
        if relative is not None:
            local_day = datetime.now(ZoneInfo(DEFAULT_ZONE)).date() + timedelta(days=relative)
            fields = local_day.year, local_day.month, local_day.day
            day_date, day_end = _date_span(fields, ZoneInfo(DEFAULT_ZONE))
            tz_label = DEFAULT_ZONE
    namespace = _arg(question, "namespace")
    pod = _arg(question, "pod")
    plain = _plain(question)
    if not namespace:
        match = re.search(r"\bnamespace\s+([a-z0-9](?:[-a-z0-9]*[a-z0-9])?)\b", plain)
        namespace = match.group(1) if match else None
    if not pod:
        match = re.search(r"\bpods?\s+([a-z0-9](?:[-a-z0-9]*[a-z0-9])?)\b", plain)
        pod = match.group(1) if match and match.group(1) not in {"a-t-il", "est", "precedent", "concerne", "concernes", "et", "suivant", "dans", "du", "de", "le", "les"} else None
    if not pod:
        match = re.search(r"\bincident\s+([a-z0-9](?:[-a-z0-9]*[a-z0-9])?)\b", plain)
        if match and match.group(1) not in {"precedent", "precedente", "actuel", "passe", "suivant", "du", "de", "le", "debut", "fin", "cree", "creee", "dernier"}:
            pod = match.group(1)
    qualified = re.search(r"\b([a-z0-9][a-z0-9-]{0,62})/([a-z0-9][a-z0-9-]{0,252})\b", plain)
    filler = {"concernes", "concerne", "et", "les", "le", "du", "de", "dans", "pour", "avec"}
    if (qualified and re.search(r"[a-z]", qualified.group(0))
            and not (set(qualified.groups()) & filler)):
        namespace, pod = qualified.groups()
    if pod and not namespace:
        match = re.search(r"\bdans\s+(?:le\s+namespace\s+)?([a-z0-9][a-z0-9-]*)\b", plain)
        if match and match.group(1) not in {"le", "la", "les", "un", "une"}:
            namespace = match.group(1)
    return {"start": start, "end": end, "timezone": tz_label, "namespace": namespace,
            "pod": pod, "date": day_date if not (first or last or instant) and not clocks else None,
            "date_end": day_end if not (first or last or instant) and not clocks else None,
            "date_fields": fields, "clocks": clocks,
            "explicit_utc": explicit_utc if fields else None,
            "year_hint": int(year_hint.group(1)) if year_hint else None,
            "missing_year": fields[0] is None if fields else None, "instant": instant,
            "time_error": time_error,
            "pod_family": bool(re.search(r"\bpods\s+" + re.escape(pod) + r"\b", plain)) if pod else None}


def _validate_components(parts):
    if parts.get("time_error"):
        return None, parts["time_error"]
    if parts.get("missing_year"):
        return None, "Quelle année pour cette date ? Indiquez l'année à quatre chiffres, par exemple 2026."
    start, end = parts["start"], parts["end"]
    if start is None and end is None and parts.get("date") and parts.get("date_end"):
        return {"start": parts["date"], "end": parts["date_end"],
                "timezone": parts["timezone"] or DEFAULT_ZONE,
                "namespace": parts["namespace"], "pod": parts["pod"]}, None
    if start is None or end is None:
        if parts.get("instant"):
            return None, "Horodatage compris. Indiquez la date et l'heure de fin de la plage à examiner."
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
    return {"start": start, "end": end, "timezone": parts["timezone"] or DEFAULT_ZONE,
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
        if update["date_fields"]:
            parts["start"] = parts["end"] = None
        if update["start"] or update["end"]:
            parts["date"] = None
            parts["date_end"] = None
            parts["date_fields"] = None
            parts["missing_year"] = False
        if update["year_hint"] and parts["date_fields"] and not update["date_fields"]:
            old = parts["date_fields"]
            update["date_fields"] = (update["year_hint"], old[1], old[2])
            update["clocks"] = parts["clocks"]
            update["explicit_utc"] = parts["explicit_utc"]
            update["missing_year"] = False
        parts.update({key: value for key, value in update.items() if value is not None})
        if ((update["date_fields"] or update["year_hint"]) and not update["instant"]
                and parts["date_fields"] and parts["date_fields"][0] is not None):
            zone = timezone.utc if parts["explicit_utc"] else ZoneInfo(DEFAULT_ZONE)
            try:
                parts["date"], parts["date_end"] = _date_span(parts["date_fields"], zone)
                parts["timezone"] = "UTC" if parts["explicit_utc"] else DEFAULT_ZONE
                if parts["clocks"]:
                    parts["start"] = _local_clock(parts["date_fields"], parts["clocks"][0], zone)
                    parts["end"] = _local_clock(parts["date_fields"], parts["clocks"][1], zone)
                    parts["date"] = parts["date_end"] = None
            except ValueError as error:
                parts["date"] = parts["date_end"] = None
                parts["time_error"] = str(error)
    return parts


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
    start_local, end_local = local_time(report["start"], zone), local_time(report["end"], zone)
    start_clock, end_clock = short_time(report["start"], zone), short_time(report["end"], zone)
    if start_local.date() == end_local.date() and offset_label(start_local) != offset_label(end_local):
        start_clock += f" ({offset_label(start_local)})"
        end_clock += f" ({offset_label(end_local)})"
    period = (f"{start_clock}–{end_clock}"
              if start_local.date() == end_local.date() else
              f"du {date_label(start_local)} à {start_clock} "
              f"au {date_label(end_local)} à {end_clock}")
    lines = ["## Incident observé", "",
             f"**Ressource :** `{resource}`. **Période :** {period}.", "",
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
        # An explicit HTML break survives Open WebUI's incremental Markdown rendering.
        lines.extend([f"{number}. **{clock} — {title}**<br>", "", f"   {description}", ""])
    if not significant:
        lines.append("Aucune observation datée significative n'a été retrouvée dans la fenêtre consultée.")
    lines += ["", "**CPU du processus applicatif**"]
    if episode and episode["baseline"] and episode["peak"]:
        qualifier = "hausse mesurée" if episode["measured_rise"] else "pas de hausse significative mesurée"
        baseline = f"{episode['baseline'].value:.4f}".replace(".", ",")
        peak = f"{episode['peak'].value:.4f}".replace(".", ",")
        lines.append(f"- Cycle retenu à {short_time(episode['start_marker']['timestamp'], zone)} : "
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
    log_request = (service.loki.logs_between(
        window.get("start_ns", int(window["start"].timestamp() * 10**9)),
        window.get("end_ns", int(window["end"].timestamp() * 10**9)),
        window["namespace"], window["pod"]) if window["namespace"] else None)
    # For the controlled demo, establish the actual marker evidence in Loki
    # before asking the other sources about that exact UTC interval.
    demo_logs = None
    if log_request and window["pod"] == "kubeai-demo-incident":
        demo_logs = (await asyncio.gather(log_request, return_exceptions=True))[0]
        log_request = None
    tasks = [service.cluster_status.inventory(),
             service.cluster_status.prometheus.history(window["start"].timestamp(),
                                                       window["end"].timestamp(), window["namespace"], window["pod"],
                                                       include_correlation=include_correlation)]
    if log_request:
        tasks.append(log_request)
    results = await asyncio.gather(*tasks, return_exceptions=True)
    if demo_logs is not None:
        results.append(demo_logs)
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
    if parts.get("time_error"):
        return None, parts["time_error"], None
    if parts["missing_year"]:
        return None, "Quelle année pour cette date ? Indiquez l'année à quatre chiffres.", None
    if not (parts["start"] or parts["end"] or parts["date"]):
        question = " ".join(dialogue(chat).incident_turns)
        normalized = _plain(question)
        latest_demo = ("dernier" in normalized and
                       ("kubeai_demo_incident" in normalized or
                        ("script" in normalized and "demonstration" in normalized)))
        if latest_demo:
            try:
                episode = await service.loki.latest_demo_episode()
            except UpstreamError as error:
                return None, f"Recherche des marqueurs Loki impossible : {error.message}", None
            if not episode:
                return None, ("Aucun cycle complet KUBEAI_DEMO_INCIDENT conservé dans Loki. "
                              "Indiquez une date et un pod précis, ou exécutez le script de démonstration."), None
            start_ns, end_ns = episode["start_ns"], episode["end_ns"]
            return {"start": datetime.fromtimestamp(start_ns / 10**9, timezone.utc),
                    "end": datetime.fromtimestamp(end_ns / 10**9, timezone.utc),
                    "start_ns": start_ns, "end_ns": end_ns,
                    "timezone": "UTC", "namespace": "kubeai", "pod": "kubeai-demo-incident",
                    "search_note": "Fenêtre issue des quatre marqueurs KUBEAI_DEMO_INCIDENT réels dans Loki : "
                                   + ", ".join(f"{item['phase']}={item['timestamp_ns']} ns UTC"
                                               for item in episode["markers"][:4])}, None, None
        if parts.get("instant"):
            return None, "Horodatage compris. Quelle date et heure de fin délimitent l'incident ?", None
        return None, HELP, None
    inventory = None
    if parts["pod"] and (not parts["namespace"] or parts.get("pod_family") or
                         re.search(r"-x{3,}$", parts["pod"])):
        try:
            inventory = await service.cluster_status.inventory()
        except UpstreamError:
            return None, "Kubernetes est indisponible : indiquez le namespace du pod pour poursuivre.", None
        pod = re.sub(r"-x{3,}$", "", parts["pod"])
        resources = {(item.namespace, item.name) for item in inventory.pods or [] if item.name}
        resources.update((item.namespace, item.involved_name) for item in inventory.events or []
                         if item.involved_kind == "Pod" and item.involved_name and item.namespace)
        exact = {(ns, name) for ns, name in resources if name == pod and
                 (not parts["namespace"] or ns == parts["namespace"])}
        matches = exact or {(ns, name) for ns, name in resources if name.startswith(pod + "-") and
                            (not parts["namespace"] or ns == parts["namespace"])}
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
                          "conservés. Précisez son nom exact et son namespace."), None

    valid = r"[a-z0-9](?:[-a-z0-9]*[a-z0-9])?"
    if parts["namespace"] and (len(parts["namespace"]) > 63 or
                               not re.fullmatch(valid, parts["namespace"])):
        return None, "Namespace invalide ; indiquez son nom Kubernetes exact.", None
    if parts["pod"] and (len(parts["pod"]) > 253 or not re.fullmatch(valid, parts["pod"])):
        return None, "Nom de pod invalide ; indiquez son nom Kubernetes exact.", None

    if parts["start"] or parts["end"]:
        window, error = _validate_components(parts)
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


def _refined_day_window(day, candidates, logs, day_end=None):
    """Keep nearby before/after evidence while retaining a useful CPU cadence."""
    day_end = day_end or day + timedelta(days=1)
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
    end = min(day_end, max(nearby) + timedelta(minutes=2))
    # Ten minutes supplies baseline and recovery samples even for a single log.
    # The +/- ten-minute neighborhood plus padding stays under thirty minutes,
    # so all six historical series fit the existing budget at a 30-second step.
    if end - start < timedelta(minutes=10):
        middle = start + (end - start) / 2
        start = max(day, middle - timedelta(minutes=5))
        end = min(day_end, start + timedelta(minutes=10))
        start = max(day, end - timedelta(minutes=10))
    return start, end, anchor


async def scan_date(service, parts, inventory=None):
    """Search the requested civil day in source-bounded UTC segments."""
    day = parts["date"]
    day_end = parts.get("date_end") or day + timedelta(days=1)
    if inventory is None:
        try:
            inventory = await service.cluster_status.inventory()
        except UpstreamError:
            inventory = None
    all_points, all_logs, candidate_points, candidate_logs, failures = [], [], [], [], {}
    scan_warnings = []
    metrics_ok = logs_ok = 0
    metrics_partial = metrics_truncated = logs_truncated = False

    async def segment(start):
        end = min(day_end, start + timedelta(hours=6))
        requests = [service.cluster_status.prometheus.history(start.timestamp(), end.timestamp(),
                                                               parts["namespace"], parts["pod"])]
        signals = service.cluster_status.prometheus.incident_signals(
            start.timestamp(), end.timestamp(), parts["namespace"], parts["pod"])
        requests.append(signals)
        requests.append(service.loki.logs_between(int(start.timestamp() * 10**9),
                                                  int(end.timestamp() * 10**9),
                                                  parts["namespace"], parts["pod"]))
        return await asyncio.gather(*requests, return_exceptions=True)

    starts = []
    cursor = day
    while cursor < day_end:
        starts.append(cursor)
        cursor += timedelta(hours=6)
    for metrics, signals, logs in await asyncio.gather(*(segment(start) for start in starts)):
        if isinstance(metrics, Exception):
            failures["mimir"] = metrics.message if isinstance(metrics, UpstreamError) else "Lecture indisponible"
            metrics = None
        else:
            metrics_ok += 1
            scan_warnings.extend(metrics.warnings[:4])
            metrics_partial |= metrics.partial
            metrics_truncated |= metrics.truncated
        if isinstance(signals, Exception):
            scan_warnings.append("Mimir : lecture du CPU applicatif indisponible pendant le balayage.")
            metrics_partial = True
            if metrics is None:
                failures["mimir"] = signals.message if isinstance(signals, UpstreamError) else "Lecture indisponible"
        else:
            scan_warnings.extend(signals.warnings[:4])
            metrics_partial |= signals.partial
            metrics_truncated |= signals.truncated
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
        if signals:
            points = sorted(signals.points, key=lambda item: item.timestamp)
            candidate_points.extend(points)
            stride = max(1, (len(points) + 23) // 24)
            all_points.extend(points[::stride][:24])
        if logs:
            candidate_logs.extend(logs.entries)
            all_logs.extend(logs.entries[:12])

    candidates = []
    if inventory:
        for event in inventory.events or []:
            if (event.observed_at and day <= event.observed_at < day_end
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

    note = (f"Journée civile demandée en {parts.get('timezone') or 'UTC'} : "
            f"{stamp(day)} → {stamp(day_end)} ; balayage en fenêtres de six heures maximum. "
            "L'heure de l'incident n'a pas été supposée.")
    if candidates:
        start, end, moment = _refined_day_window(day, candidates, candidate_logs, day_end=day_end)
        detail = (f" Fenêtre approfondie autour d'un signal daté à {stamp(moment)}, avec contexte "
                  "avant/après et un pas métrique de 30 secondes pour les séries disponibles.")
        if any(not start <= candidate <= end for _, candidate in candidates):
            detail += " D'autres signaux existent hors de cette fenêtre ; ils ne sont pas attribués au même incident."
        window = {"start": start, "end": end, "timezone": parts.get("timezone") or "UTC",
                  "namespace": parts["namespace"], "pod": parts["pod"],
                  "search_note": note + detail}
        return window, None, None

    # No identifiable anomaly: report the whole searched day from bounded scans.
    combined = (HistoricalMetrics(all_points, scan_warnings, metrics_truncated,
                                  metrics_partial or metrics_ok < len(starts)) if metrics_ok else None)
    combined_logs = HistoricalLogs(all_logs, logs_truncated) if logs_ok else None
    if metrics_ok:
        failures.pop("mimir", None)
    if logs_ok:
        failures.pop("loki", None)
    day_window = {"start": day, "end": day_end, "timezone": parts.get("timezone") or "UTC",
                  "namespace": parts["namespace"], "pod": parts["pod"]}
    report = redact_data(build(day_window, inventory, combined, combined_logs, failures), service.sensitive_values())
    if metrics_ok < len(starts) or logs_ok < len(starts):
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
