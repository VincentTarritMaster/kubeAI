"""Locate a recent observed anomaly before reading its bounded historical evidence."""
import asyncio
from datetime import datetime, timezone
import re


LOOKBACK_NS = 30 * 86400 * 10**9


async def discover(service, parts, score_log, state):
    namespace = parts.get("namespace") or service.loki.settings.namespace
    pod = parts.get("pod")
    valid = r"[a-z0-9](?:[-a-z0-9]*[a-z0-9])?"
    if len(namespace) > 63 or not re.fullmatch(valid, namespace):
        return None, "Namespace invalide ; indiquez un namespace Kubernetes valide.", None
    if pod and (len(pod) > 253 or not re.fullmatch(valid, pod)):
        return None, "Nom de pod invalide ; indiquez son nom Kubernetes exact.", None
    now = datetime.now(timezone.utc)
    now_ns = int(now.timestamp() * 10**9)
    results = await asyncio.gather(
        service.loki.incident_candidates(namespace, pod),
        service.cluster_status.inventory(),
        service.loki.latest_demo_episode(),
        return_exceptions=True,
    )
    logs, inventory, episode = results
    candidates, limitations = [], []
    if isinstance(logs, Exception):
        limitations.append("recherche des anomalies Loki indisponible")
    else:
        if logs.truncated:
            limitations.append("recherche limitée aux 200 derniers logs candidats")
        for entry in logs.entries:
            if score_log(entry["message"]) > 0:
                candidates.append({"namespace": namespace, "pod": entry["pod"],
                                   "at": int(entry["timestamp_ns"]), "source": "Loki"})
    if isinstance(inventory, Exception) or inventory.events is None:
        limitations.append("événements Kubernetes indisponibles")
    else:
        if inventory.sources["events"].status != "ok":
            limitations.append("événements Kubernetes partiels")
        for event in inventory.events:
            if (event.type != "Warning" or event.involved_kind != "Pod" or event.namespace != namespace
                    or not event.involved_name or not re.fullmatch(valid, event.involved_name)
                    or len(event.involved_name) > 253 or (pod and event.involved_name != pod) or event.observed_at is None):
                continue
            moment = int(event.observed_at.timestamp() * 10**9)
            if now_ns - LOOKBACK_NS <= moment <= now_ns:
                candidates.append({"namespace": namespace, "pod": event.involved_name,
                                   "at": moment, "source": "Kubernetes"})
    if isinstance(episode, Exception):
        episode = None
        limitations.append("recherche du cycle de démonstration indisponible")
    if episode and namespace == service.loki.settings.namespace and pod in (None, "kubeai-demo-incident"):
        anomalies = [int(item["timestamp_ns"]) for item in episode["markers"] if item["phase"] == "anomaly_start"]
        if anomalies:
            candidates.append({"namespace": namespace, "pod": "kubeai-demo-incident", "at": max(anomalies), "source": "Loki"})
    if not candidates:
        note = ("Aucun incident horodaté identifiable dans le namespace `" + namespace +
                "` parmi les logs conservés sur 30 jours et les événements Kubernetes encore disponibles. "
                "Cela ne prouve pas l'absence d'incident.")
        if limitations:
            note += " Recherche incomplète : " + "; ".join(limitations) + "."
        return None, note, None
    candidates.sort(key=lambda item: item["at"], reverse=True)
    latest = candidates[0]["at"]
    tied = sorted({(item["namespace"], item["pod"]) for item in candidates if item["at"] == latest})
    selected = candidates[0]
    if len(tied) > 1:
        if state.selection is not None and 0 <= state.selection < len(state.choices) and state.choices[state.selection] in tied:
            target = state.choices[state.selection]
            selected = next(item for item in candidates if (item["namespace"], item["pod"]) == target)
        else:
            return None, ("Plusieurs pods correspondent au dernier signal, au même horodatage : "
                          + ", ".join(ns + "/" + name for ns, name in tied[:6])
                          + ". Lequel faut-il examiner ?"), None
    at = selected["at"]
    # Les marqueurs complets fournissent une vraie période observée, y compris
    # après disparition du pod. Un signal plus récent n'est jamais masqué.
    if (episode and selected["pod"] == "kubeai-demo-incident" and namespace == service.loki.settings.namespace
            and episode["start_ns"] <= at <= episode["end_ns"]):
        start_ns, end_ns = episode["start_ns"], episode["end_ns"]
        note = "Dernier incident identifié automatiquement ; période délimitée par les quatre marqueurs de démonstration dans Loki."
    else:
        start_ns, end_ns = max(1, at - 120 * 10**9), min(now_ns, at + 300 * 10**9)
        if end_ns <= start_ns:
            return None, "Le signal le plus récent n'a pas de fenêtre temporelle exploitable.", None
        note = ("Dernier signal d'anomalie horodaté trouvé dans " + selected["source"]
                + ". Fenêtre de recherche : deux minutes avant à cinq minutes après ce signal, "
                "bornée à l'heure actuelle ; le début réel, la fin et la récupération de l'incident restent à vérifier.")
    note += " Recherche limitée au namespace `" + namespace + "`, sur 30 jours de logs conservés et les événements Kubernetes disponibles."
    if limitations:
        note += " Sources partielles : " + "; ".join(limitations) + "."
    return {"start": datetime.fromtimestamp(start_ns / 10**9, timezone.utc),
            "end": datetime.fromtimestamp(end_ns / 10**9, timezone.utc), "start_ns": start_ns, "end_ns": end_ns,
            "timezone": "UTC", "namespace": namespace, "pod": selected["pod"], "search_note": note,
            "automatic_discovery": True}, None, None
