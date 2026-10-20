"""Evidence-based correlation rules; no model output becomes a cluster fact."""

from datetime import datetime, timezone
import json
import re
import unicodedata

from app.clients.base import UpstreamError
from app.services.incident_timeline import HELP, collect, parse_chat_window, stamp

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
    resource = f"{namespace}/{pod}"
    logs = [e for e in report["timeline"] if e["source"] == "Loki" and e["resource"] == resource]
    events = [e for e in report["timeline"] if e["source"] == "Kubernetes" and e["resource"] == resource]
    hypotheses = []
    limitations = []
    if not latency:
        limitations.append("Aucune métrique de latence HTTP NestJS n'est collectée par la configuration active.")
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
        hypotheses.append(_hypothesis(
            "connectivity", "Défaillance de connexion possible pendant l'intervalle étudié.",
            [_evidence(log), _evidence(event) if event else None], report,
            "étayée par log et événement, causalité non prouvée" if event else "preuves insuffisantes",
            ["Service distant indisponible", "Erreur applicative locale", "Latence réseau non mesurée"],
            [] if event else ["Aucun événement réseau corroborant le log dans cinq minutes."],
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

    incident_markers = [e for e in logs if "KUBEAI_DEMO_INCIDENT" in e["observation"]]
    start_marker = next((e for e in incident_markers if "phase=anomaly_start" in e["observation"]), None)
    recovery_marker = next((e for e in incident_markers if "phase=recovered" in e["observation"]), None)
    if start_marker and app_cpu:
        start_time = datetime.fromisoformat(start_marker["timestamp"].replace("Z", "+00:00")).timestamp()
        recovery_time = (datetime.fromisoformat(recovery_marker["timestamp"].replace("Z", "+00:00")).timestamp()
                         if recovery_marker else None)
        baseline = next((p for p in reversed(app_cpu) if p.timestamp < start_time), None)
        anomaly_samples = [p for p in app_cpu if p.timestamp >= start_time and
                           (recovery_time is None or p.timestamp <= recovery_time)]
        peak = max(anomaly_samples, key=lambda p: p.value, default=None)
        if baseline and peak and peak.value >= max(baseline.value * 2, baseline.value + 0.01):
            proof = [_evidence(start_marker), _point_evidence(report, "app_cpu_cores", baseline.timestamp),
                     _point_evidence(report, "app_cpu_cores", peak.timestamp)]
            if recovery_marker:
                proof.append(_evidence(recovery_marker))
                after_recovery = next((p for p in app_cpu if p.timestamp >= recovery_time
                                       and p.timestamp <= recovery_time + 90), None)
                if after_recovery:
                    proof.append(_point_evidence(report, "app_cpu_cores", after_recovery.timestamp))
                    if after_recovery.value >= peak.value:
                        limitations.append("Un échantillon CPU après le marqueur de retour reste élevé ; le retour métrique à la base n'est pas établi.")
                else:
                    limitations.append("Marqueur de retour à la normale présent, mais aucun échantillon CPU postérieur ne permet de le vérifier.")
            hypotheses.append(_hypothesis(
                "demo_app_cpu", "Hausse du temps CPU du processus observée pendant la fenêtre d'anomalie synthétique.",
                proof, report, "limitée : association temporelle, causalité non prouvée",
                ["Une autre activité du processus peut contribuer à la hausse."], [],
                ["Comparer les échantillons avant, pendant et après l'anomalie et vérifier la cadence de scrape."]
            ))
        else:
            limitations.append("Le marqueur d'anomalie applicative existe, mais les échantillons CPU applicatifs ne montrent pas de hausse mesurable par rapport à la base.")
    elif incident_markers and not app_cpu:
        limitations.append("Marqueurs d'incident présents, mais série process_cpu_seconds_total absente ou pas encore scrappée.")
    elif not app_cpu:
        limitations.append("Métrique CPU applicative process_cpu_seconds_total absente ; aucune corrélation CPU applicative n'est possible.")
    if app_cpu:
        limitations.append("process_cpu_seconds_total mesure le temps CPU du processus ; son taux n'est ni le CPU global du conteneur ni un pourcentage de sa limite.")

    if cpu and cpu_limits and not latency and not marker:
        high = next((p for p in cpu if any(_near(p.timestamp, lim.timestamp) and lim.value > 0
                                         and p.value >= 0.8 * lim.value for lim in cpu_limits)), None)
        if high:
            limitations.append("CPU proche d'une limite observée, mais aucune latence HTTP mesurée ne relie ce signal à la lenteur.")
    report = {**report, "scope": "correlation", "hypotheses": hypotheses,
              "conclusion": "hypothèses non confirmées" if hypotheses else "cause indéterminée",
              "correlation_limits": list(dict.fromkeys(limitations))}
    if limitations and report["data_status"] == "complete":
        report["data_status"] = "partial"
    return report


def render(report, model_note):
    lines = ["**Corrélation de l'incident**", f"Ressource : {report['namespace']}/{report['pod']} ; "
             f"fenêtre {report['start']} → {report['end']} ({report['timezone']}).",
             f"Conclusion : {report['conclusion']}.", "", "**Faits observés — preuves**"]
    lines += [f"- {e['timestamp']} · {e['source']} · {e['resource']} · {e['observation']} "
              f"[preuve : {e['reference']}]" for e in report["timeline"][:20]] or ["- Aucune observation datée."]
    lines += ["", "**Hypothèses**"]
    for item in report["hypotheses"]:
        lines.append(f"- {item['id']} : {item['description']} Confiance : {item['confidence']}.")
        lines += [f"  - Appui : {proof['source']} {proof['timestamp']} [{proof['reference']}]"
                  for proof in item["supporting_observations"]]
        lines += ["  - Alternative : " + alternative for alternative in item["alternatives"]]
        lines += ["  - Contradiction : " + contradiction for contradiction in item["contradictions"]]
        lines += ["  - Vérifier : " + check for check in item["recommended_checks"]]
    if not report["hypotheses"]:
        lines.append("- Preuves insuffisantes pour attribuer une cause.")
    lines += ["", "**Données manquantes et limites**"]
    lines += ["- " + item for item in report["missing"] + report["correlation_limits"]] or ["- Aucune limite signalée."]
    lines += ["", "Une coïncidence temporelle ne démontre pas la cause racine.", model_note]
    return "\n".join(lines)


async def correlate(service, chat):
    window, error = parse_chat_window(chat)
    if error or not window["namespace"] or not window["pod"]:
        message = error or ("Précisez namespace et pod pour comparer des preuves de la même ressource. " + HELP)
        return message, {"scope": "correlation", "status": "clarification_required", "message": message}
    report, _, metrics, _ = await collect(service, window, include_correlation=True)
    report = assess(report, metrics)
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
            response = await service.llm.complete({"messages": [
                {"role": "system", "content": "Choisis uniquement un identifiant d'hypothèse fournie ou none. Réponds par JSON avec la clé priority. N'ajoute aucun fait ni texte libre."},
                {"role": "user", "content": json.dumps(candidates, ensure_ascii=False)}],
                "temperature": 0, "max_tokens": 32})
            priority = json.loads(response["choices"][0]["message"]["content"])["priority"]
            if priority in {h["id"] for h in report["hypotheses"]}:
                report["hypotheses"].sort(key=lambda h: h["id"] != priority)
                model_note = f"{provider_name} a sélectionné la priorité {priority} parmi les hypothèses calculées ; aucun fait supplémentaire n'a été retenu."
            elif priority == "none":
                model_note = f"{provider_name} a répondu ; aucune hypothèse n'a été priorisée. Diagnostic déterministe conservé."
            else:
                model_note = f"Réponse de {provider_name} rejetée : priorité absente des hypothèses étayées."
        except UpstreamError as error:
            if "Réponse JSON" in error.message:
                model_note = f"Réponse API de {provider_name} rejetée : format de réponse invalide. Diagnostic déterministe conservé."
            else:
                model_note = f"Appel à {provider_name} échoué ({error.message}) ; diagnostic déterministe conservé."
        except (KeyError, IndexError, TypeError, ValueError):
            model_note = f"Réponse de {provider_name} rejetée : format ou priorité invalide. Diagnostic déterministe conservé."
    return render(report, model_note), report
