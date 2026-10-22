"""Evidence-based correlation rules; no model output becomes a cluster fact."""

from datetime import datetime, timezone
import json
import re
import unicodedata

from app.clients.base import UpstreamError
from app.services.incident_timeline import HELP, application_cpu_episode, collect, resolve_request, stamp
from app.services.presentation import (DEFAULT_ZONE, date_label, day_heading, local_time,
                                       missing_summary, offset_label, readable_observation, short_time,
                                       technical_details)

ALIGN_SECONDS = 300


def correlation_question(chat):
    question = next((m.content for m in reversed(chat.messages) if m.role == "user"), "").lower()
    question = unicodedata.normalize("NFKD", question).encode("ascii", "ignore").decode()
    return any(word in question for word in ("lente", "lenteur", "latence", "correl",
                                              "hypothèse", "hypothese", "cause"))


def _evidence(item):
    return {key: item[key] for key in ("timestamp", "source", "resource", "observation", "reference")}


def _near(first, second):
    return abs(first - second) <= ALIGN_SECONDS


def _metric_entries(report, kind):
    return [entry for entry in report["timeline"] if entry["source"] == "Mimir"
            and f"query_range {kind} " in entry["reference"]]


def _points(metrics, kind, namespace, pod):
    if metrics is None:
        return []
    return sorted((p for p in metrics.points if p.metric == kind
                   and p.labels.get("namespace") == namespace and p.labels.get("pod") == pod),
                  key=lambda p: p.timestamp)


def _point_evidence(report, kind, moment):
    candidates = _metric_entries(report, kind)
    target = stamp(datetime.fromtimestamp(moment, tz=timezone.utc))
    return next((_evidence(item) for item in candidates if item["timestamp"] == target), None)


def _hypothesis(identifier, description, evidence, report, confidence, alternatives, contradictions, checks):
    return {"id": identifier, "description": description, "supporting_observations": [e for e in evidence if e],
            "resources": [f"{report['namespace']}/{report['pod']}"],
            "interval": {"start": report["start"], "end": report["end"]},
            "confidence": confidence, "alternatives": alternatives,
            "contradictions": contradictions, "recommended_checks": checks}


def assess(report, metrics):
    """Qualitative rules; thresholds are local heuristics, never causal proof."""
    namespace, pod = report["namespace"], report["pod"]
    cpu = _points(metrics, "cpu_cores", namespace, pod)
    app_cpu = _points(metrics, "app_cpu_cores", namespace, pod)
    memory = _points(metrics, "memory_working_set_bytes", namespace, pod)
    restarts = _points(metrics, "restart_count", namespace, pod)
    cpu_limits = _points(metrics, "cpu_limit_cores", namespace, pod)
    memory_limits = _points(metrics, "memory_limit_bytes", namespace, pod)
    latency = _points(metrics, "http_latency_p95_seconds", namespace, pod)
    error_ratio = _points(metrics, "http_error_ratio", namespace, pod)
    resource = f"{namespace}/{pod}"
    logs = [e for e in report["timeline"] if e["source"] == "Loki" and e["resource"] == resource]
    events = [e for e in report["timeline"] if e["source"] == "Kubernetes" and e["resource"] == resource]
    hypotheses = []
    limitations = []
    if not latency:
        limitations.append("Aucune métrique de latence HTTP NestJS n'est collectée par la configuration active.")
    if not error_ratio:
        limitations.append("Aucun taux d'erreurs HTTP applicatif n'est collecté par la configuration active.")
    if not restarts:
        limitations.append("Aucune série historique de redémarrages disponible ; le cumul actuel ne date pas un incident.")
    if not cpu_limits:
        limitations.append("Limite CPU historique absente : aucune saturation CPU n'est déduite du seul usage.")
    if not memory_limits:
        limitations.append("Limite mémoire historique absente : aucune saturation mémoire n'est déduite du seul usage.")

    oom_events = [e for e in events if re.search(r"oomkilled|oomkilling", e["observation"], re.I)]
    for event in oom_events[:1]:
        moment = datetime.fromisoformat(event["timestamp"].replace("Z", "+00:00")).timestamp()
        pair = next(((a, b) for a, b in zip(restarts, restarts[1:])
                     if a.timestamp <= moment <= b.timestamp and b.value > a.value
                     and _near(a.timestamp, moment) and _near(b.timestamp, moment)), None)
        mem = next((p for p in memory if _near(p.timestamp, moment)), None)
        limit = next((p for p in memory_limits if _near(p.timestamp, moment) and p.value > 0), None)
        if pair and mem and limit and mem.value >= 0.9 * limit.value:
            proof = [_evidence(event), _point_evidence(report, "restart_count", pair[0].timestamp),
                     _point_evidence(report, "restart_count", pair[1].timestamp),
                     _point_evidence(report, "memory_working_set_bytes", mem.timestamp),
                     _point_evidence(report, "memory_limit_bytes", limit.timestamp)]
            hypotheses.append(_hypothesis(
                "memory_oom", "Pression mémoire compatible avec l'événement OOMKilled et une hausse des redémarrages.",
                proof, report, "étayée par plusieurs sources, causalité non prouvée",
                ["Un pic bref entre deux échantillons ou un autre conteneur du pod peut expliquer l'écart."], [],
                ["Comparer les limites mémoire et l'état du conteneur au moment de l'incident."]))
        else:
            limitations.append("OOMKilled observé, mais hausse historique des redémarrages et proximité d'une limite mémoire non établies.")
            if mem and limit and mem.value < 0.5 * limit.value:
                limitations.append("Mesure mémoire du pod faible face à sa limite près de l'événement ; les preuves se contredisent ou manquent de granularité.")

    error_logs = [e for e in logs if re.search(r"connection refused|econnreset|econnrefused|connection timed out|timeout|erreur de connexion", e["observation"], re.I)]
    network_events = [e for e in events if re.search(r"networkunavailable|failedcreatepodsandbox|network.*error", e["observation"], re.I)]
    if error_logs:
        log = error_logs[0]
        log_time = datetime.fromisoformat(log["timestamp"].replace("Z", "+00:00")).timestamp()
        event = next((e for e in network_events if _near(
            datetime.fromisoformat(e["timestamp"].replace("Z", "+00:00")).timestamp(), log_time)), None)
        http_signal = next((p for p in error_ratio
                            if _near(p.timestamp, log_time) and p.value >= 0.2), None)
        proof = [_evidence(log)]
        if event:
            proof.append(_evidence(event))
        if http_signal:
            proof.append(_point_evidence(report, "http_error_ratio", http_signal.timestamp))
        if event and http_signal:
            confidence = "étayée par log, événement et erreurs HTTP, causalité non prouvée"
        elif event:
            confidence = "étayée par log et événement, causalité non prouvée"
        elif http_signal:
            confidence = "étayée par log et erreurs HTTP, causalité non prouvée"
        else:
            confidence = "preuves insuffisantes"
        contradictions = []
        if not event:
            contradictions.append("Aucun événement réseau corroborant le log dans cinq minutes.")
        if http_signal:
            contradictions.append("La hausse des 5xx peut aussi provenir d'une erreur applicative locale.")
        description = ("Défaillance de connexion possible pendant l'intervalle étudié, "
                       "avec erreurs HTTP 5xx associées." if http_signal else
                       "Défaillance de connexion possible pendant l'intervalle étudié.")
        hypotheses.append(_hypothesis(
            "connectivity", description, proof, report, confidence,
            ["Service distant indisponible", "Erreur applicative locale", "Latence réseau non mesurée"],
            contradictions,
            ["Comparer les logs du service appelé et les événements réseau, sans modifier le cluster."]))

    if cpu and cpu_limits and latency and len(latency) >= 2:
        slow = next((p for p in latency[1:] if p.value >= 0.2 and p.value >= 2 * latency[0].value), None)
        if slow:
            busy = next((p for p in cpu if _near(p.timestamp, slow.timestamp)), None)
            limit = next((p for p in cpu_limits if _near(p.timestamp, slow.timestamp) and p.value > 0), None)
            if busy and limit and busy.value >= 0.8 * limit.value:
                hypotheses.append(_hypothesis(
                    "cpu_latency", "Contention CPU possible lors d'une hausse de latence HTTP réellement mesurée.",
                    [_point_evidence(report, "cpu_cores", busy.timestamp),
                     _point_evidence(report, "cpu_limit_cores", limit.timestamp),
                     _point_evidence(report, "http_latency_p95_seconds", latency[0].timestamp),
                     _point_evidence(report, "http_latency_p95_seconds", slow.timestamp)],
                    report, "étayée par métriques, causalité non prouvée",
                    ["Attente d'une dépendance", "Charge concurrente", "Artefact de mesure"], [],
                    ["Comparer la latence par route et les limites CPU sur la même fenêtre."]))

    marker = next((e for e in logs if "KUBEAI_DEMO_CPU_LOAD" in e["observation"]), None)
    if marker and len(cpu) >= 2:
        marker_time = datetime.fromisoformat(marker["timestamp"].replace("Z", "+00:00")).timestamp()
        before = next((p for p in reversed(cpu) if p.timestamp < marker_time), None)
        after = next((p for p in cpu if p.timestamp >= marker_time and _near(p.timestamp, marker_time)), None)
        if before and after and after.value - before.value >= 0.05 and after.value >= 2 * max(before.value, 0.001):
            hypotheses.append(_hypothesis(
                "demo_cpu_activity", "Activité CPU accrue compatible avec la charge de démonstration annoncée dans le log.",
                [_evidence(marker), _point_evidence(report, "cpu_cores", before.timestamp),
                 _point_evidence(report, "cpu_cores", after.timestamp)], report,
                "limitée : corrélation temporelle, sans preuve de causalité",
                ["Autre activité du conteneur pendant la même fenêtre"], [],
                ["Comparer les séries CPU avant, pendant et après le marqueur de charge."]))

    incident_markers = [e for e in logs if re.search(
        r"\bphase\s*=\s*(?:normal|anomaly_start|recovered|complete)\b", e["observation"], re.I)]
    episode = application_cpu_episode(incident_markers, app_cpu)
    if episode and app_cpu:
        start_marker, recovery_marker = episode["start_marker"], episode["recovery_marker"]
        baseline, peak = episode["baseline"], episode["peak"]
        if episode["count"] > 1:
            limitations.append(f"Plusieurs cycles observés ; corrélation CPU limitée à celui débutant à "
                               f"{stamp(datetime.fromtimestamp(episode['start_time'], timezone.utc))}, sans mélanger les exécutions.")
        if episode["measured_rise"]:
            proof = [_evidence(start_marker), _point_evidence(report, "app_cpu_cores", baseline.timestamp),
                     _point_evidence(report, "app_cpu_cores", peak.timestamp)]
            if recovery_marker:
                proof.append(_evidence(recovery_marker))
                after_recovery = episode["after"][0] if episode["after"] else None
                if after_recovery:
                    proof.append(_point_evidence(report, "app_cpu_cores", after_recovery.timestamp))
                    if after_recovery.value >= peak.value:
                        limitations.append("Un échantillon CPU après le marqueur de retour reste élevé ; le retour métrique à la base n'est pas établi.")
                else:
                    limitations.append("Marqueur de retour à la normale présent, mais aucun échantillon CPU postérieur ne permet de le vérifier.")
            hypotheses.append(_hypothesis(
                "demo_app_cpu", "Hausse du temps CPU du processus observée pendant la fenêtre d'anomalie signalée dans les logs.",
                proof, report, "limitée : association temporelle, causalité non prouvée",
                ["Une autre activité du processus peut contribuer à la hausse."], [],
                ["Comparer les échantillons avant, pendant et après l'anomalie et vérifier la cadence de scrape."]
            ))
        elif baseline is None:
            limitations.append("Marqueur d'anomalie applicative présent, mais aucun échantillon CPU de base dans le même cycle ; la hausse n'est pas vérifiable.")
        elif peak is None:
            limitations.append("Marqueur d'anomalie applicative présent, mais aucun échantillon CPU pendant ce cycle ; la hausse n'est pas vérifiable.")
        else:
            limitations.append("Le marqueur d'anomalie applicative existe, mais les échantillons CPU applicatifs ne montrent pas de hausse mesurable par rapport à la base.")
    elif incident_markers and not app_cpu:
        limitations.append("Marqueurs d'incident présents, mais série process_cpu_seconds_total absente ou pas encore scrappée.")
    elif not app_cpu:
        limitations.append("Métrique CPU applicative process_cpu_seconds_total absente ; aucune corrélation CPU applicative n'est possible.")
    if app_cpu:
        limitations.append("process_cpu_seconds_total mesure le temps CPU du processus ; son taux n'est ni le CPU global du conteneur ni un pourcentage de sa limite.")

    anomaly_marker = next((e for e in incident_markers if re.search(
        r"\bphase\s*=\s*anomaly_start\b", e["observation"], re.I)), None)
    if anomaly_marker and error_ratio:
        start = datetime.fromisoformat(anomaly_marker["timestamp"].replace("Z", "+00:00")).timestamp()
        before = next((p for p in reversed(error_ratio)
                       if p.timestamp < start and start - p.timestamp <= 300), None)
        during = [p for p in error_ratio if start <= p.timestamp <= start + 600]
        peak = max(during, key=lambda p: p.value, default=None)
        if peak and peak.value >= 0.2 and (
                before is None or peak.value >= max(before.value * 2, before.value + 0.1)):
            proof = [_evidence(anomaly_marker),
                     _point_evidence(report, "http_error_ratio", peak.timestamp)]
            if before:
                proof.append(_point_evidence(report, "http_error_ratio", before.timestamp))
            latency_before = next((p for p in reversed(latency)
                                   if p.timestamp < start and start - p.timestamp <= 300), None)
            latency_during = max(
                (p for p in latency if start <= p.timestamp <= start + 600),
                key=lambda p: p.value, default=None)
            if latency_before and latency_during:
                proof.extend([
                    _point_evidence(report, "http_latency_p95_seconds", latency_before.timestamp),
                    _point_evidence(report, "http_latency_p95_seconds", latency_during.timestamp),
                ])
            hypotheses.append(_hypothesis(
                "demo_http_errors",
                "Hausse du taux d'erreurs HTTP 5xx pendant la fenêtre d'anomalie signalée dans les logs.",
                proof, report, "limitée : association temporelle, causalité non prouvée",
                ["Dépendance externe indisponible", "Erreur applicative locale", "Trafic de démonstration"], [],
                ["Comparer les logs du service appelé et les compteurs d'erreurs sur la même fenêtre."]))
        elif peak:
            limitations.append("Taux d'erreurs HTTP mesuré sans hausse nette pendant la fenêtre d'anomalie.")
    elif anomaly_marker and not error_ratio:
        limitations.append("Marqueur d'anomalie présent, mais aucune série http_requests_total pour mesurer les erreurs HTTP.")

    if cpu and cpu_limits and not latency and not marker:
        high = next((p for p in cpu if any(_near(p.timestamp, lim.timestamp) and lim.value > 0
                                         and p.value >= 0.8 * lim.value for lim in cpu_limits)), None)
        if high:
            limitations.append("CPU proche d'une limite observée, mais aucune latence HTTP mesurée ne relie ce signal à la lenteur.")
    near_limit_pairs = [
        (point, limit) for point in app_cpu for limit in cpu_limits
        if _near(point.timestamp, limit.timestamp) and limit.value > 0
        and point.value >= 0.8 * limit.value
    ]
    process_near_limit = bool(near_limit_pairs)
    if process_near_limit:
        limitations.append("CPU du processus proche de la limite CPU configurée : vérifier la saturation et le throttling ; aucun des deux n'est prouvé par cette comparaison.")
        if not incident_markers:
            process_point, limit_point = max(
                near_limit_pairs, key=lambda pair: pair[0].value / pair[1].value)
            hypotheses.append(_hypothesis(
                "process_cpu_near_limit",
                "Le CPU du processus a approché la limite CPU configurée ; vérifier une éventuelle saturation ou un throttling.",
                [_point_evidence(report, "app_cpu_cores", process_point.timestamp),
                 _point_evidence(report, "cpu_limit_cores", limit_point.timestamp)],
                report, "faible : mesures Mimir seules, sans preuve de saturation ni de throttling",
                ["L'activité mesurée peut être normale pour ce processus.",
                 "La métrique du processus ne représente pas tout le conteneur."],
                (["Les logs Loki de la fenêtre ne sont pas disponibles ; aucun lien avec un marqueur d'incident n'est confirmé."]
                 if not logs else []),
                ["Vérifier les compteurs de throttling CPU du conteneur et sa limite effective à cette heure."]))
    report = {**report, "scope": "correlation", "hypotheses": hypotheses,
              "conclusion": "hypothèses non confirmées" if hypotheses else "cause indéterminée",
              "correlation_limits": list(dict.fromkeys(limitations)),
              "process_near_limit": process_near_limit}
    if limitations and report["data_status"] == "complete":
        report["data_status"] = "partial"
    return report


def render(report, model_note, *, zone=DEFAULT_ZONE):
    resource = f"{report['namespace']}/{report['pod']}"
    primary = report["hypotheses"][0] if report["hypotheses"] else None
    http_hypothesis = next((h for h in report["hypotheses"] if h["id"] == "demo_http_errors"), None)
    supported = primary["supporting_observations"] if primary else []
    signal = next((item for item in supported if item["source"] == "Loki" and
                   re.search(r"\bphase\s*=\s*anomaly_start\b", item["observation"], re.I)), None)
    if signal is None:
        signal = next((item for item in report["timeline"] if item["source"] == "Loki" and
                       re.search(r"\bphase\s*=\s*anomaly_start\b", item["observation"], re.I)), None)
    start_local, end_local = local_time(report["start"], zone), local_time(report["end"], zone)
    if primary:
        if primary["id"] == "demo_http_errors":
            proof_candidates = primary["supporting_observations"][:5]
        else:
            proof_candidates = primary["supporting_observations"][:4]
        if http_hypothesis and primary["id"] != "demo_http_errors":
            http_proofs = http_hypothesis["supporting_observations"]
            proof_candidates += [p for p in http_proofs if "http_error_ratio" in p["reference"]][:1]
            proof_candidates += [p for p in http_proofs if "http_latency_p95_seconds" in p["reference"]][:2]
    else:
        proof_candidates = []
        for source in ("Loki", "Mimir", "Kubernetes"):
            match = next((item for item in report["timeline"] if item["source"] == source
                          and item["resource"] == resource), None)
            if match:
                proof_candidates.append(match)
        if len(proof_candidates) < 2:
            proof_candidates.extend(item for item in report["timeline"]
                                    if item["resource"] == resource and item not in proof_candidates)
        proof_candidates = proof_candidates[:4]
    proof_candidates = list({
        (item["timestamp"], item["reference"]): item for item in proof_candidates
    }.values())
    moments = [start_local, end_local] + [local_time(proof["timestamp"], zone) for proof in proof_candidates]
    if signal:
        moments.append(local_time(signal["timestamp"], zone))
    day_offsets = {}
    for moment in moments:
        day_offsets.setdefault(moment.date(), set()).add(offset_label(moment))

    def clock(value):
        local = local_time(value, zone)
        label = short_time(value, zone)
        return label + (f" ({offset_label(local)})" if len(day_offsets[local.date()]) > 1 else "")

    period = (f"de {clock(report['start'])} à {clock(report['end'])}"
              if start_local.date() == end_local.date() else
              f"du {date_label(start_local)} à {clock(report['start'])} "
              f"au {date_label(end_local)} à {clock(report['end'])}")
    lines = ["## Corrélation de l'incident", "",
             f"**Incident analysé :** `{resource}`.", "",
             *day_heading(start_local, zone, mixed_offset=len(day_offsets[start_local.date()]) > 1),
             f"**Période observée :** {period}.", ""]
    if signal:
        lines.append(f"**Problème observé :** anomalie signalée dans les logs Loki "
                     f"à {clock(signal['timestamp'])}.")
    else:
        lines.append("**Problème observé :** aucun début d'anomalie précis n'est établi par les marqueurs disponibles.")
    if primary:
        if primary["id"] == "demo_app_cpu":
            relation = ("Les marqueurs de Loki encadrent le cycle d'anomalie et Mimir montre une hausse du CPU "
                        "du processus pendant ce cycle.")
            if http_hypothesis:
                relation += (" Mimir mesure aussi une hausse du taux d'erreurs HTTP 5xx pendant ce cycle.")
            hypothesis = ("Une activité CPU accrue du processus a pu contribuer à l'incident ; "
                          "le lien causal reste à vérifier.")
        elif primary["id"] == "demo_http_errors":
            relation = ("Les marqueurs de Loki encadrent une hausse du taux d'erreurs HTTP 5xx "
                        "mesurée dans Mimir.")
            hypothesis = ("Une dépendance ou une erreur applicative a pu provoquer ces 500 ; "
                          "le lien causal reste à vérifier.")
        else:
            relation = primary["description"]
            hypothesis = primary["description"]
        lines += ["", "**Corrélation :** " + relation,
                  "Les sources coïncident dans la fenêtre examinée ; cette proximité ne démontre pas la cause.",
                  "", f"**Hypothèse principale :** {hypothesis}",
                  f"**Niveau de confiance :** {primary['confidence']}.",
                  "**Justification :** les observations datées ci-dessous proviennent des sources indiquées ; "
                  "elles ne confirment pas à elles seules la cause racine."]
        proofs = proof_candidates
        if proofs:
            lines += ["", "**Preuves principales :**"]
            heading_day = start_local.date()
            for proof in sorted(proofs, key=lambda item: item["timestamp"]):
                proof_local = local_time(proof["timestamp"], zone)
                if proof_local.date() != heading_day:
                    lines += ["", *day_heading(proof_local, zone,
                                               mixed_offset=len(day_offsets[proof_local.date()]) > 1)]
                    heading_day = proof_local.date()
                lines.append(f"- {clock(proof['timestamp'])} — {proof['source']} : "
                             f"{readable_observation(proof)}")
        checks = list(primary["recommended_checks"])
        if primary["id"] in {"demo_app_cpu", "cpu_latency"}:
            checks.append("Lire, si disponibles, les métriques de throttling CPU et les limites du conteneur sur la même fenêtre.")
        if report.get("process_near_limit"):
            checks.append("Comparer le CPU du processus, la limite du conteneur et les métriques de throttling sur la même fenêtre.")
        lines += ["", "**Vérifications recommandées (lecture seule) :**"]
        lines += ["- " + check for check in dict.fromkeys(checks)]
    else:
        lines += ["", "**Corrélation :** preuves insuffisantes pour relier avec certitude les logs et les métriques.",
                  "", "**Hypothèse principale :** cause indéterminée.",
                  "**Niveau de confiance :** insuffisant."]
        if proof_candidates:
            lines += ["", "**Preuves principales :**"]
            heading_day = start_local.date()
            for proof in sorted(proof_candidates, key=lambda item: item["timestamp"]):
                proof_local = local_time(proof["timestamp"], zone)
                if proof_local.date() != heading_day:
                    lines += ["", *day_heading(proof_local, zone,
                                               mixed_offset=len(day_offsets[proof_local.date()]) > 1)]
                    heading_day = proof_local.date()
                lines.append(f"- {clock(proof['timestamp'])} — {proof['source']} : "
                             f"{readable_observation(proof)}")
        lines += ["", "**Vérifications recommandées (lecture seule) :**",
                  "- Examiner les logs et les métriques disponibles aux mêmes heures."]
        if report.get("process_near_limit"):
            lines.append("- Comparer le CPU du processus, la limite du conteneur et les métriques de throttling sur la même fenêtre.")
    if any("process_cpu_seconds_total" in limit for limit in report["correlation_limits"]):
        lines += ["", "La mesure CPU du processus ne permet pas d'affirmer une saturation ou un throttling du conteneur."]
    others = report["hypotheses"][1:]
    if others:
        lines += ["", "**Autres hypothèses calculées :**"]
        for item in others:
            proofs = item["supporting_observations"]
            if item["id"] == "demo_http_errors":
                proof = next((p for p in proofs if "http_error_ratio" in p["reference"]),
                             proofs[0] if proofs else None)
            elif item["id"] == "connectivity":
                proof = next((p for p in proofs if "connection refused" in p["observation"].lower()),
                             proofs[0] if proofs else None)
            else:
                proof = proofs[0] if proofs else None
            evidence = f" Preuve : {readable_observation(proof)}" if proof else ""
            lines.append(f"- `{item['id']}` : {item['description']} "
                         f"(confiance : {item['confidence']}).{evidence}")
    if report["missing"] or report["correlation_limits"]:
        lines += ["", "**Données manquantes :** " + missing_summary(
            report["missing"] + report["correlation_limits"], limit=2) + "."]
    technical = [f"Fenêtre UTC : {report['start']} → {report['end']} ; fuseau demandé : {report['timezone']}",
                 f"Conclusion structurée : {report['conclusion']}",
                 f"Observations et références détaillées ({len(report['timeline'])}) :"]
    technical += [f"{item['timestamp']} | {item['source']} | {item['resource']} | "
                  f"{item['observation']} | preuve : {item['reference']}" for item in report["timeline"]]
    for item in report["hypotheses"]:
        technical.append(f"Hypothèse {item['id']} : {item['description']} | confiance : {item['confidence']}")
        technical += [f"Appui : {proof['source']} {proof['timestamp']} | {proof['reference']}"
                      for proof in item["supporting_observations"]]
        technical += [f"Alternative : {value}" for value in item["alternatives"]]
        technical += [f"Contradiction : {value}" for value in item["contradictions"]]
    technical += [f"Source {name} : {value['status']}" for name, value in report["sources"].items()]
    technical += ["Donnée manquante : " + item for item in report["missing"]]
    technical += ["Limite : " + item for item in report["correlation_limits"] + report["warnings"]]
    technical.append(model_note)
    lines += ["", technical_details(technical)]
    return "\n".join(lines)


async def correlate(service, chat):
    window, error, scanned = await resolve_request(service, chat)
    if error or not window["namespace"] or not window["pod"]:
        message = error or ("Précisez namespace et pod pour comparer des preuves de la même ressource. " + HELP)
        return message, {"scope": "correlation", "status": "clarification_required", "message": message}
    report, _, metrics, _ = scanned or await collect(service, window, include_correlation=True)
    report = assess(report, metrics)
    if window.get("search_note"):
        report["correlation_limits"].append(window["search_note"])
    provider_name = service.llm.provider_name
    sensitive_values = service.sensitive_values()
    can_call = bool(sensitive_values) or service.llm.settings.llm_provider == "local"
    if not report["hypotheses"]:
        model_note = f"{provider_name} non sollicité : aucune hypothèse calculée à prioriser ; diagnostic déterministe conservé."
    elif not can_call:
        model_note = f"{provider_name} non sollicité : clé non configurée ; diagnostic déterministe conservé."
    else:
        model_note = f"{provider_name} n'a pas été sollicité ; diagnostic déterministe conservé."
    if can_call and report["hypotheses"]:
        try:
            candidates = [{"id": h["id"], "description": h["description"],
                           "references": [p["reference"] for p in h["supporting_observations"]]}
                          for h in report["hypotheses"]]
            priority = await service.llm.select_option(
                prompt="Choisis uniquement un identifiant d'hypothèse fournie ou none. Aucun fait ni texte libre.",
                data={"hypotheses": candidates}, key="priority",
                options=tuple(h["id"] for h in report["hypotheses"]) + ("none",))
            if priority in {h["id"] for h in report["hypotheses"]}:
                model_note = (f"{provider_name} a sélectionné la priorité {priority} parmi les "
                              "hypothèses calculées ; l'ordre déterministe des preuves est conservé.")
            elif priority == "none":
                model_note = f"{provider_name} a répondu ; aucune hypothèse n'a été priorisée. Diagnostic déterministe conservé."
            else:
                model_note = f"Réponse de {provider_name} rejetée : priorité absente des hypothèses étayées."
        except UpstreamError as error:
            if "Réponse JSON" in error.message or "Sélection structurée" in error.message:
                model_note = f"Réponse de {provider_name} rejetée : format de réponse invalide. Diagnostic déterministe conservé."
            else:
                model_note = f"Appel à {provider_name} échoué ({error.message}) ; diagnostic déterministe conservé."
        except (KeyError, IndexError, TypeError, ValueError):
            model_note = f"Réponse de {provider_name} rejetée : format ou priorité invalide. Diagnostic déterministe conservé."
    return render(report, model_note, zone=service.llm.settings.display_timezone), report
