"""Evidence-based global cluster health assessment for demonstration A."""

from datetime import timedelta
import json
import re
import unicodedata

from app.models import ChatRequest, ClusterInventory
from app.clients.prometheus_client import ClusterUsage
from app.services.privacy import redact_data
from app.services.presentation import (DEFAULT_ZONE, day_heading, local_time, offset_label, short_time,
                                       technical_details)

WARNING_WINDOW = timedelta(minutes=15)
RECENT_RESTART_REASONS = {"backoff", "crashloopbackoff"}


def _probe_kind(message: str | None) -> str | None:
    match = re.search(r"\b(readiness|liveness|startup)\s+probe\b", message or "", re.I)
    return match.group(1).lower() if match else None


def cluster_health_question(chat: ChatRequest) -> bool:
    questions = [message.content for message in chat.messages if message.role == "user"]
    current = questions[-1]
    if re.match(r"\s*(et maintenant|and now|what about now)\b", current, re.I) and len(questions) > 1:
        current = questions[-2] + " " + current
    normalized = unicodedata.normalize("NFKD", current).encode("ascii", "ignore").decode().lower()
    return bool(re.search(r"\b(cluster|grappe)\b", normalized)
                and re.search(r"\b(sante|etat|status|state|health|healthy|problemes?)\b", normalized))


def assess(inventory: ClusterInventory, usage: ClusterUsage | None, mimir_error: str | None = None) -> dict:
    nodes, pods, events = inventory.nodes, inventory.pods, inventory.events
    observed_at = inventory.observed_at
    bad_nodes = [node.name for node in nodes or [] if node.ready is False]
    bad_pods = [{"namespace": pod.namespace, "name": pod.name, "phase": pod.phase,
                 "reason": ", ".join(pod.issues[:2]) or pod.health}
                for pod in pods or [] if pod.health == "unhealthy"]
    restarting = sorted(({"namespace": pod.namespace, "pod": pod.name, "container": container.name,
                          "count": container.restarts}
                         for pod in pods or [] for container in pod.containers
                         if container.restarts is not None and container.restarts > 0),
                        key=lambda item: item["count"], reverse=True)
    pod_by_name = {(pod.namespace, pod.name): pod for pod in pods or []}
    node_by_name = {node.name: node for node in nodes or []}
    recent_warnings = []
    for event in events or []:
        if event.type != "Warning" or event.observed_at is None:
            continue
        age = observed_at - event.observed_at
        if -timedelta(minutes=5) <= age <= WARNING_WINDOW:
            target = (pod_by_name.get((event.namespace, event.involved_name))
                      if event.involved_kind == "Pod" else
                      node_by_name.get(event.involved_name) if event.involved_kind == "Node" else None)
            current_health = ("en défaut" if getattr(target, "health", None) == "unhealthy" else
                              "NotReady" if getattr(target, "ready", None) is False else
                              "Ready" if getattr(target, "ready", None) is True else
                              "état actuel non déterminé")
            first = event.first_observed_at
            recurrence = ("répété dans la fenêtre récente" if event.count and event.count >= 3
                          and first and -timedelta(minutes=5) <= observed_at - first <= WARNING_WINDOW
                          else "compteur cumulé, fréquence récente inconnue" if event.count and event.count > 1
                          else "occurrence isolée ou fréquence inconnue")
            recent_warnings.append({
                "at": event.observed_at.isoformat(), "namespace": event.namespace,
                "kind": event.involved_kind, "name": event.involved_name,
                "reason": event.reason, "message": event.message,
                "probe": _probe_kind(event.message), "count": event.count,
                "first_at": first.isoformat() if first else None,
                "recurrence": recurrence, "current_health": current_health,
                "severity": ("élevée" if current_health in {"en défaut", "NotReady"}
                             else "à surveiller"),
            })

    limits = []
    for name, source in inventory.sources.items():
        if source.status != "ok":
            limits.append(f"{name} : {source.status}")
    if mimir_error:
        limits.append("Mimir : indisponible")
    elif usage is None or usage.cpu_cores is None or usage.memory_working_set_bytes is None:
        limits.append("Mimir : CPU ou mémoire absent")
    elif usage.warnings:
        limits.append("Mimir : mesures partielles")
    if any(pod.restarts is None for pod in pods or []):
        limits.append("redémarrages incomplets pour certains pods")
    if nodes is not None and pods is not None:
        not_ready = set(bad_nodes)
        if any(pod.node in not_ready and pod.health == "healthy" for pod in pods):
            limits.append("états nœud/pod contradictoires au moment du relevé")
    limits.append("aucune source d'alertes dédiée n'est intégrée")

    critical = bool(nodes and inventory.sources["nodes"].status == "ok"
                    and all(node.ready is False for node in nodes))
    # restartCount is cumulative for the current container instance. It does not
    # indicate when a restart happened, so it is context rather than a current
    # health signal. Current pod/node state and recent Warning events drive the
    # classification.
    degraded = bool(bad_nodes or bad_pods)
    essential_missing = (nodes is None or pods is None or not nodes
                         or inventory.sources["nodes"].status != "ok"
                         or inventory.sources["pods"].status != "ok"
                         or any(node.ready is None for node in nodes or [])
                         or any(pod.health == "unknown" for pod in pods or []))
    partial_supervision = (inventory.sources["events"].status != "ok" or mimir_error is not None
                           or usage is None or usage.cpu_cores is None
                           or usage.memory_working_set_bytes is None or bool(usage.warnings))
    if critical:
        state_code, state = "critical", "critique"
    elif degraded:
        state_code, state = "degraded", "dégradé"
    elif essential_missing:
        state_code, state = "indeterminate", "indéterminé"
    elif recent_warnings or partial_supervision:
        state_code = "operational"
        qualifiers = []
        if recent_warnings:
            qualifiers.append("avertissements récents")
        if partial_supervision:
            qualifiers.append("supervision partielle")
        state = "opérationnel avec " + " et ".join(qualifiers)
    else:
        state_code, state = "healthy", "sain selon les contrôles effectués"

    report = {
        "scope": "cluster", "observed_at": observed_at.isoformat(),
        "state": state, "state_code": state_code,
        "nodes": {"total": len(nodes) if nodes is not None else None,
                  "ready": sum(node.ready is True for node in nodes) if nodes is not None else None,
                  "not_ready": len(bad_nodes) if nodes is not None else None,
                  "affected": bad_nodes[:5]},
        "pods": {"total": len(pods) if pods is not None else None,
                 "running": sum(pod.phase == "Running" for pod in pods) if pods is not None else None,
                 "unhealthy": len(bad_pods) if pods is not None else None,
                 "affected": bad_pods[:8]},
        "restarting": restarting[:5], "restarting_total": len(restarting),
        "restart_context": "compteur cumulé sans horodatage ; conservé comme contexte historique",
        "recent_warnings": recent_warnings[:5], "recent_warnings_total": len(recent_warnings),
        "metrics": {"cpu_cores": usage.cpu_cores if usage else None,
                    "memory_working_set_bytes": usage.memory_working_set_bytes if usage else None,
                    "cpu_query": usage.cpu_query if usage else None,
                    "memory_query": usage.memory_query if usage else None},
        "sources": {name: source.status for name, source in inventory.sources.items()}
                   | {"mimir": "unavailable" if mimir_error else
                      "partial" if usage is None or usage.cpu_cores is None
                      or usage.memory_working_set_bytes is None or usage.warnings else "ok",
                      "alerts": "not_configured"},
        "limits": limits,
        "classification_rules": {
            "critical": "tous les nœuds observés sont NotReady et la source nœuds est disponible",
            "degraded": "au moins un nœud NotReady ou un pod actuellement en défaut",
            "operational": "nœuds et pods disponibles, avec avertissements récents ou supervision partielle",
            "indeterminate": "nœuds ou pods indisponibles ou incomplets sans anomalie concluante",
            "healthy": "aucun signal actuel ou récent parmi les contrôles accessibles",
            "historical_restarts": "les restartCount cumulés sont rapportés mais ne dégradent pas seuls l'état",
        },
        "truncated": any(source.status == "partial" for source in inventory.sources.values()),
    }
    return redact_data(report)


def render(report: dict, *, ai_note: str, zone: str = DEFAULT_ZONE) -> str:
    nodes, pods, metrics = report["nodes"], report["pods"], report["metrics"]
    local_observed = local_time(report["observed_at"], zone)
    event_moments = [local_time(item["at"], zone) for item in report["recent_warnings"]]
    day_offsets = {}
    for moment in [local_observed, *event_moments]:
        day_offsets.setdefault(moment.date(), set()).add(offset_label(moment))
    lines = ["## Santé du cluster Kubernetes", "",
             *day_heading(local_observed, zone, mixed_offset=len(day_offsets[local_observed.date()]) > 1),
             f"**État global : {report['state']}**", ""]
    lines.append("- Nœuds : données indisponibles." if nodes["total"] is None else
                 f"- Nœuds : {nodes['ready']}/{nodes['total']} Ready ({nodes['not_ready']} NotReady).")
    lines.append("- Pods : données indisponibles." if pods["total"] is None else
                 f"- Pods : {pods['running']}/{pods['total']} Running ({pods['unhealthy']} en erreur), tous namespaces confondus.")
    restarting = report["restarting"]
    lines.append(f"- Redémarrages cumulés : {report['restarting_total']} conteneur(s) avec un compteur non nul."
                 if restarting else "- Redémarrages : aucun cumul non nul observé dans les données disponibles.")
    if metrics["cpu_cores"] is None and metrics["memory_working_set_bytes"] is None:
        lines.append("- Métriques : CPU et mémoire indisponibles dans Mimir.")
    else:
        parts = []
        if metrics["cpu_cores"] is not None:
            parts.append(f"CPU des conteneurs observés {metrics['cpu_cores']:.3f} cœur(s)")
        if metrics["memory_working_set_bytes"] is not None:
            parts.append(f"mémoire working set {metrics['memory_working_set_bytes'] / 1048576:.1f} Mio")
        lines.append("- Métriques Mimir : " + ", ".join(parts) + ".")
    lines.append(f"- Avertissements récents : {report['recent_warnings_total']}."
                 if report["sources"]["events"] != "unavailable" else
                 "- Avertissements récents : source Kubernetes indisponible.")
    if nodes["affected"] or pods["affected"] or report["recent_warnings"]:
        lines += ["", "**Anomalies détectées**", ""]
    for name in nodes["affected"]:
        lines.append(f"- Nœud `{name}` : NotReady [Kubernetes].")
    for item in pods["affected"][:5]:
        lines.append(f"- Pod `{item['namespace']}/{item['name']}` : {item['phase']}, {item['reason']} [Kubernetes].")
    heading_day = local_observed.date()
    for item in sorted(report["recent_warnings"][:3], key=lambda value: value["at"]):
        event_local = local_time(item["at"], zone)
        event_day = event_local.date()
        if event_day != heading_day:
            lines.extend(["", *day_heading(event_local, zone,
                                           mixed_offset=len(day_offsets[event_day]) > 1)])
            heading_day = event_day
        target = "/".join(part for part in (item["namespace"], item["kind"], item["name"]) if part)
        probe = f" (sonde {item['probe']})" if item["probe"] else ""
        clock = short_time(item["at"], zone)
        if len(day_offsets[event_day]) > 1:
            clock += f" ({offset_label(event_local)})"
        lines.append(f"- {clock} — `{item['reason'] or 'Warning'}`{probe} "
                     f"sur `{target or 'ressource non précisée'}` ; {item['current_health']}, "
                     f"{item['recurrence']}.")
    lines += ["", "**Diagnostic**", "", {
        "critical": "Aucun nœud observé n'est Ready ; la disponibilité du cluster exige une vérification immédiate.",
        "degraded": "Un nœud ou un pod est actuellement en défaut. Le diagnostic porte sur son état présent.",
        "indeterminate": "Les états des nœuds ou des pods sont insuffisants pour conclure sur la santé globale.",
        "operational": "Les nœuds et pods observés fonctionnent. Les avertissements ou les mesures manquantes "
                       "nécessitent une vérification ; une panne générale n'est pas démontrée.",
        "healthy": "Aucune anomalie actuelle ou récente n'est visible dans les contrôles disponibles.",
    }[report["state_code"]]]
    if restarting:
        lines.append("Les redémarrages sont des compteurs cumulés sans horodatage ; ils ne suffisent pas à établir un incident récent.")
    lines += ["", "**Vérifications recommandées (lecture seule)**", ""]
    if nodes["affected"] or pods["affected"]:
        lines.append("- Examiner les conditions des ressources actuellement en défaut et leurs événements Kubernetes.")
    if report["recent_warnings"]:
        lines.append("- Examiner les sondes concernées et comparer les nouveaux avertissements à l'état actuel des ressources.")
    if report["sources"]["mimir"] != "ok":
        lines.append("- Vérifier la collecte CPU et mémoire dans Mimir.")
    if report["state_code"] == "indeterminate":
        lines.append("- Réessayer la lecture des nœuds et des pods pour compléter le relevé.")
    if lines[-1] == "":
        lines.append("- Continuer la surveillance des états Kubernetes et des métriques disponibles.")
    if ai_note.startswith("**Priorité"):
        lines += ["", ai_note]
    technical = [f"Relevé UTC : {report['observed_at']}",
                 "Sources : " + ", ".join(f"{name}={state}" for name, state in report["sources"].items()),
                 f"Redémarrages : {report['restart_context']}"]
    technical += [f"Mimir CPU : {metrics['cpu_cores']} cœur | PromQL : {metrics['cpu_query']}",
                  f"Mimir mémoire : {metrics['memory_working_set_bytes']} octets | PromQL : {metrics['memory_query']}"]
    technical += [f"Nœud NotReady : {name} [Kubernetes]" for name in nodes["affected"]]
    technical += [f"Pod : {item['namespace']}/{item['name']} | {item['phase']} | {item['reason']} [Kubernetes]"
                  for item in pods["affected"]]
    technical += [f"{item['namespace']}/{item['pod']}/{item['container']} : {item['count']} redémarrage(s) cumulés [Kubernetes]"
                  for item in restarting]
    technical += [f"{item['at']} | Warning {item['reason'] or 'sans motif'} | "
                  f"{item['namespace']}/{item['kind']}/{item['name']} | "
                  f"sonde={item['probe'] or 'non déterminée'} | occurrences={item['count']} | "
                  f"première observation={item['first_at']} | état actuel={item['current_health']} | "
                  f"gravité={item['severity']} | récurrence={item['recurrence']} | "
                  f"message={item['message'] or ''} [Kubernetes Events]"
                  for item in report["recent_warnings"]]
    technical += ["Limite : " + limit for limit in report["limits"]]
    technical.append(ai_note)
    lines += ["", technical_details(technical)]
    return "\n".join(lines)


def model_focus(report: dict, content: str) -> str | None:
    """Accept only a focus category backed by evidence; never publish model prose."""
    try:
        focus = json.loads(content)["focus"]
    except (ValueError, KeyError, TypeError):
        return None
    restarting_targets = {(item["namespace"], item["pod"]) for item in report["restarting"]}
    recent_restart_evidence = any(
        (event["namespace"], event["name"]) in restarting_targets
        and (event["reason"] or "").casefold() in RECENT_RESTART_REASONS
        for event in report["recent_warnings"]
    )
    available = {
        "nodes": bool(report["nodes"]["affected"]),
        "pods": bool(report["pods"]["affected"]),
        "restarts": recent_restart_evidence,
        "events": bool(report["recent_warnings"]),
        "availability": report["state_code"] == "indeterminate",
        "none": report["state_code"] == "healthy",
    }
    return focus if isinstance(focus, str) and available.get(focus, False) else None


def focus_note(focus: str, provider_name: str = "Mistral API") -> str:
    descriptions = {
        "nodes": "vérifier d'abord les nœuds NotReady",
        "pods": "examiner d'abord les pods en erreur",
        "restarts": "comparer d'abord les cumuls de redémarrages avec un nouveau relevé",
        "events": "examiner d'abord les avertissements Kubernetes récents",
        "availability": "rétablir d'abord les lectures Kubernetes manquantes",
        "none": "aucun axe supplémentaire n'est retenu par ce relevé",
    }
    return f"**Priorité de lecture sélectionnée avec {provider_name} :** " + descriptions[focus] + "."
