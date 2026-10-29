"""Evidence-based global cluster health assessment for demonstration A."""

from datetime import timedelta
import json
import re
import unicodedata

from pydantic import BaseModel, ConfigDict

from app.models import ChatRequest, ClusterInventory
from app.clients.prometheus_client import ClusterUsage
from app.services.privacy import redact_data
from app.services.presentation import (DEFAULT_ZONE, day_heading, full_time, local_time, offset_label, short_time,
                                       technical_details)

WARNING_WINDOW = timedelta(minutes=15)
RECENT_RESTART_REASONS = {"backoff", "crashloopbackoff"}


class ClusterHealthReport(BaseModel):
    """Validated, machine-readable result of the deterministic health rules."""

    model_config = ConfigDict(extra="allow")
    report_version: int = 1
    snapshot_id: str = ""
    mode: str = "LIVE"
    cluster: str | None = None
    monitored_namespaces: list[str] = ["*"]
    namespace: str | None = None
    observed_at: str
    window_seconds: int = 60
    health: str
    state_code: str
    nodes: dict
    pods: dict
    restarts: dict
    metrics: dict
    events: dict
    causes: list[dict] = []
    unavailable: list[str] = []
    data_status: str


def _probe_kind(message: str | None) -> str | None:
    match = re.search(r"\b(readiness|liveness|startup)\s+probe\b", message or "", re.I)
    return match.group(1).lower() if match else None


def cluster_health_question(chat: ChatRequest) -> bool:
    questions = [message.content for message in chat.messages if message.role == "user"]
    current = questions[-1]
    if re.match(r"\s*(et maintenant|and now|what about now)\b", current, re.I) and len(questions) > 1:
        current = questions[-2] + " " + current
    normalized = unicodedata.normalize("NFKD", current).encode("ascii", "ignore").decode().lower()
    health = re.search(
        r"\b(sante|etat|status|state|health|healthy|problemes?|probleme|fonctionne\w*|"
        r"fonctionnement|bilan|point|critique|degrade\w*|sain|works?|working|correctly|"
        r"va bien|comment va)\b",
        normalized,
    )
    scope = re.search(
        r"\b(cluster|grappe|kubernetes|k8s|namespace|namespaces|espace de noms|"
        r"applications|kube-system|supervision|tout|all|everything|systeme|system|"
        r"plateforme|environnement)\b",
        normalized,
    )
    return bool(health and scope)


def assess(inventory: ClusterInventory, usage: ClusterUsage | None, mimir_error: str | None = None,
           namespace: str | None = None) -> dict:
    all_nodes, all_pods, all_events = inventory.nodes, inventory.pods, inventory.events
    pods = ([pod for pod in all_pods if pod.namespace == namespace]
            if namespace and all_pods is not None else all_pods)
    events = ([event for event in all_events if event.namespace == namespace]
              if namespace and all_events is not None else all_events)
    nodes = all_nodes
    observed_at = inventory.observed_at
    bad_nodes = [node.name for node in nodes or [] if node.ready is False]
    bad_pods = [{"namespace": pod.namespace, "name": pod.name, "phase": pod.phase,
                 "reason": ", ".join(pod.issues[:2]) or pod.health,
                 "age_seconds": pod.age_seconds}
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
    recent_keys = {(item["at"], item["kind"], item["name"], item["reason"])
                   for item in recent_warnings}
    historical_warnings = []
    for event in events or []:
        if event.type != "Warning" or event.observed_at is None:
            continue
        key = (event.observed_at.isoformat(), event.involved_kind,
               event.involved_name, event.reason)
        if key in recent_keys:
            continue
        historical_warnings.append({
            "at": event.observed_at.isoformat(), "namespace": event.namespace,
            "kind": event.involved_kind, "name": event.involved_name,
            "reason": event.reason, "count": event.count,
        })
    historical_warnings.sort(key=lambda item: item["at"], reverse=True)

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

    health_label = {
        "critical": "CRITICAL", "degraded": "DEGRADED",
        "healthy": "HEALTHY", "operational": "DEGRADED",
        "indeterminate": "UNKNOWN",
    }[state_code]
    pod_phases = {
        "pending": sum(pod.phase == "Pending" for pod in pods or []),
        "failed": sum(pod.phase == "Failed" for pod in pods or []),
        "succeeded": sum(pod.phase == "Succeeded" for pod in pods or []),
        "unknown": sum(pod.phase not in {"Running", "Pending", "Failed", "Succeeded"}
                       for pod in pods or []),
    }
    pending_ages = [pod.age_seconds for pod in pods or []
                    if pod.phase == "Pending" and pod.age_seconds is not None]
    pod_phases["oldest_pending_seconds"] = max(pending_ages) if pending_ages else None
    causes = [
        {"kind": "node", "resource": name, "evidence": "Kubernetes Ready=False"}
        for name in bad_nodes
    ] + [
        {"kind": "pod", "resource": f"{item['namespace']}/{item['name']}",
         "evidence": f"phase={item['phase']}; {item['reason']}"}
        for item in bad_pods
    ]

    report = {
        "report_version": 1,
        "mode": "LIVE",
        "scope": "namespace" if namespace else "cluster",
        "namespace": namespace,
        "observed_at": observed_at.isoformat(),
        "window_seconds": 60, "monitored_namespaces": [namespace or "*"],
        "state": state, "state_code": state_code, "health": health_label,
        "nodes": {"total": len(nodes) if nodes is not None else None,
                  "ready": sum(node.ready is True for node in nodes) if nodes is not None else None,
                  "not_ready": len(bad_nodes) if nodes is not None else None,
                  "affected": bad_nodes[:5]},
        "pods": {"total": len(pods) if pods is not None else None,
                 "running": sum(pod.phase == "Running" for pod in pods) if pods is not None else None,
                 "unhealthy": len(bad_pods) if pods is not None else None,
                 "completed": pod_phases["succeeded"],
                 **pod_phases,
                 "affected": bad_pods[:8]},
        "restarting": restarting[:5], "restarting_total": len(restarting),
        "restarts": {"observed_nonzero": len(restarting),
                     "counters_are_cumulative": True},
        "restart_context": "compteur cumulé sans horodatage ; conservé comme contexte historique",
        "recent_warnings": recent_warnings[:5], "recent_warnings_total": len(recent_warnings),
        "historical_warnings": historical_warnings[:5],
        "historical_warnings_total": len(historical_warnings),
        "events": {"active_warnings": len(recent_warnings),
                   "historical_events": len(historical_warnings)},
        "metrics": {"cpu_cores": usage.cpu_cores if usage else None,
                    "memory_working_set_bytes": usage.memory_working_set_bytes if usage else None,
                    "cpu_query": usage.cpu_query if usage else None,
                    "memory_query": usage.memory_query if usage else None,
                    "period": "instant",
                    "scope": "cluster",
                    "cpu_unit": "cores", "memory_unit": "bytes"},
        "causes": causes,
        "unavailable": limits,
        "data_status": ("partial" if limits or inventory.data_status != "complete" else "complete"),
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


def render(report: dict, *, ai_note: str, zone: str = DEFAULT_ZONE, language: str = "fr") -> str:
    if language == "en":
        return render_english(report, ai_note=ai_note, zone=zone)
    nodes, pods, metrics = report["nodes"], report["pods"], report["metrics"]
    local_observed = local_time(report["observed_at"], zone)
    event_moments = [local_time(item["at"], zone) for item in report["recent_warnings"]]
    day_offsets = {}
    for moment in [local_observed, *event_moments]:
        day_offsets.setdefault(moment.date(), set()).add(offset_label(moment))
    lines = ["## Santé du cluster Kubernetes", "",
             *day_heading(local_observed, zone, mixed_offset=len(day_offsets[local_observed.date()]) > 1),
             f"**État global : {report['state']}**", ""]
    if report.get("scope") == "namespace":
        lines.insert(3, f"**Périmètre : namespace `{report.get('namespace')}` uniquement.**\n")
    lines.append("- Nœuds : données indisponibles." if nodes["total"] is None else
                 f"- Nœuds : {nodes['ready']}/{nodes['total']} Ready ({nodes['not_ready']} NotReady).")
    lines.append("- Pods : données indisponibles." if pods["total"] is None else
                 "- Pods : " + f"{pods['running']}/{pods['total']} Running "
                 f"({pods['unhealthy']} en erreur)" +
                 (f", {pods.get('completed', 0)} terminé(s)" if pods.get("completed") else "") +
                 (f", namespace `{report.get('namespace')}` uniquement." if report.get("scope") == "namespace"
                  else ", tous namespaces confondus."))
    restarting = report["restarting"]
    lines.append(f"- Redémarrages cumulés : {report['restarting_total']} conteneur(s) avec un compteur non nul."
                 if restarting else "- Redémarrages : aucun cumul non nul observé dans les données disponibles.")
    if report["sources"].get("pods") != "ok":
        lines.append("- Inventaire des pods partiel : les champs Kubernetes indisponibles ne sont pas assimilés à un état sain.")
    if report["sources"].get("alerts") == "not_configured":
        lines.append("- Alertes : aucun système d'alerting dédié n'est intégré. Les « avertissements récents » "
                     "sont des événements Kubernetes, pas des notifications d'alerting.")
    if metrics["cpu_cores"] is None and metrics["memory_working_set_bytes"] is None:
        lines.append("- Métriques : CPU et mémoire indisponibles dans Mimir.")
    else:
        parts = []
        if metrics["cpu_cores"] is not None:
            parts.append(f"CPU des conteneurs observés {metrics['cpu_cores']:.3f} cœur(s)")
        if metrics["memory_working_set_bytes"] is not None:
            parts.append(f"mémoire working set {metrics['memory_working_set_bytes'] / 1048576:.1f} Mio")
        lines.append("- Métriques Mimir" + (" (portée cluster)" if report.get("scope") == "namespace" else "") + " : "
                     + ", ".join(parts) + ".")
    lines.append(f"- Avertissements récents : {report['recent_warnings_total']}."
                 if report["sources"]["events"] != "unavailable" else
                 "- Avertissements récents : source Kubernetes indisponible.")
    if report.get("historical_warnings_total"):
        lines.append(f"- Avertissements historiques : {report['historical_warnings_total']} "
                     "(hors fenêtre récente, non actifs pour le statut actuel).")
        for item in report.get("historical_warnings", [])[:2]:
            lines.append(f"  - {short_time(item['at'], zone)} — `{item['reason'] or 'Warning'}` "
                         f"sur `{item.get('namespace') or ''}/{item.get('kind') or ''}/{item.get('name') or ''}` "
                         "(historique)." )
    if nodes["affected"] or pods["affected"] or report["recent_warnings"]:
        lines += ["", "**Anomalies détectées**", ""]
    for name in nodes["affected"]:
        lines.append(f"- Nœud `{name}` : NotReady [Kubernetes].")
    for item in pods["affected"][:5]:
        age = (f", âge {item['age_seconds']} s" if item.get("age_seconds") is not None else "")
        lines.append(f"- Pod `{item['namespace']}/{item['name']}` : {item['phase']}, {item['reason']}{age} [Kubernetes].")
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
        if metrics["cpu_cores"] is None and metrics["memory_working_set_bytes"] is None:
            lines.append("- Vérifier le scrape cAdvisor (`job=\"kubernetes-cadvisor\"`) puis la collecte Mimir.")
        else:
            lines.append("- Vérifier la collecte CPU et mémoire dans Mimir.")
    if report["state_code"] == "indeterminate":
        lines.append("- Réessayer la lecture des nœuds et des pods pour compléter le relevé.")
    if lines[-1] == "":
        lines.append("- Continuer la surveillance des états Kubernetes et des métriques disponibles.")
    if report.get("changes"):
        lines += ["", "**Changement depuis l'observation précédente**", ""]
        for change in report["changes"]:
            lines.append(f"- {change['label']} : {change['before']} → {change['after']}.")
    if ai_note.startswith("**Priorité"):
        lines += ["", ai_note]
    technical = [f"Snapshot : {report.get('snapshot_id') or 'non identifié'} | "
                 f"mode={report.get('mode', 'LIVE')} | fenêtre={report.get('window_seconds')} s",
                 f"Relevé UTC : {report['observed_at']}",
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


def _english_limit(limit):
    if "pods : partial" in limit:
        return "pod inventory: partial (some Kubernetes fields unavailable)"
    if "redémarrages incomplets" in limit:
        return "restart counters incomplete for some pods"
    if "aucune source d'alertes" in limit:
        return "no dedicated alerting system; Kubernetes events are not alerts"
    if "Mimir" in limit:
        return limit.replace("indisponible", "unavailable").replace("absent", "missing")
    return limit


def render_english(report: dict, *, ai_note: str, zone: str = DEFAULT_ZONE) -> str:
    """English rendering of the same deterministic report; no model facts."""
    nodes, pods, metrics = report["nodes"], report["pods"], report["metrics"]
    state = {"critical": "CRITICAL", "degraded": "DEGRADED",
             "operational": "DEGRADED", "indeterminate": "UNKNOWN",
             "healthy": "HEALTHY"}[report["state_code"]]
    lines = ["## Kubernetes cluster health", "",
             f"**{full_time(report['observed_at'], zone, language='en')}**", "",
             f"**Overall health: {state}**", ""]
    if report.get("scope") == "namespace":
        lines.insert(3, f"**Scope: namespace `{report.get('namespace')}` only.**\n")
    lines.append("- Nodes: data unavailable." if nodes["total"] is None else
                 f"- Nodes: {nodes['ready']}/{nodes['total']} Ready ({nodes['not_ready']} NotReady).")
    lines.append("- Pods: data unavailable." if pods["total"] is None else
                 f"- Pods: {pods['running']}/{pods['total']} Running ({pods['unhealthy']} unhealthy), "
                 f"{pods.get('pending', 0)} Pending, {pods.get('failed', 0)} Failed, "
                 f"{pods.get('succeeded', 0)} Succeeded" +
                 (f", namespace `{report.get('namespace')}` only." if report.get("scope") == "namespace"
                  else "."))
    if metrics["cpu_cores"] is None and metrics["memory_working_set_bytes"] is None:
        lines.append("- Mimir metrics: CPU and memory unavailable.")
    else:
        parts = []
        if metrics["cpu_cores"] is not None:
            parts.append(f"container CPU {metrics['cpu_cores']:.3f} cores")
        if metrics["memory_working_set_bytes"] is not None:
            parts.append(f"working set {metrics['memory_working_set_bytes'] / 1048576:.1f} MiB")
        lines.append("- Mimir metrics" + (" (cluster-wide)" if report.get("scope") == "namespace" else "") + ": "
                     + ", ".join(parts) + ".")
    lines.append(f"- Recent warnings: {report.get('recent_warnings_total', 0)}.")
    if report.get("historical_warnings_total"):
        lines.append(f"- Historical warnings: {report['historical_warnings_total']} "
                     "(outside the recent window, not active for current status).")
        for item in report.get("historical_warnings", [])[:2]:
            lines.append(f"  - {short_time(item['at'], zone)} — `{item['reason'] or 'Warning'}` "
                         f"on `{item.get('namespace') or ''}/{item.get('kind') or ''}/{item.get('name') or ''}` "
                         "(historical).")
    lines.append(f"- Cumulative restarts: {report.get('restarting_total', 0)} container counter(s) non-zero.")
    if report["sources"].get("pods") != "ok":
        lines.append("- Pod inventory: partial; unavailable Kubernetes fields are not treated as healthy.")
    if report["sources"].get("alerts") == "not_configured":
        lines.append("- Alerts: no dedicated alerting system is integrated. "
                     "The 'recent warnings' are Kubernetes events, not alert notifications.")
    if nodes["affected"] or pods["affected"]:
        lines += ["", "**Detected anomalies**", ""]
    for name in nodes["affected"]:
        lines.append(f"- Node `{name}`: NotReady [Kubernetes].")
    for item in pods["affected"][:8]:
        age = (f", age {item['age_seconds']} s" if item.get("age_seconds") is not None else "")
        lines.append(f"- Pod `{item['namespace']}/{item['name']}`: {item['phase']}, {item['reason']}{age} [Kubernetes].")
    lines += ["", "**Diagnostic**", "", {
        "critical": "No observed node is Ready; immediate verification is required.",
        "degraded": "At least one node or pod is currently unhealthy. This is a current-state diagnosis, not an inferred cause.",
        "indeterminate": "Node or pod state is insufficient for a reliable overall conclusion.",
        "operational": "Observed nodes and pods are available; warnings or missing supervision require review.",
        "healthy": "No current or recent anomaly is visible in the available controls.",
    }[report["state_code"]]]
    if report.get("changes"):
        lines += ["", "**Change since the previous observation**", ""]
        for change in report["changes"]:
            lines.append(f"- {change['label']}: {change['before']} -> {change['after']}.")
    lines += ["", "**Read-only checks**", ""]
    if nodes["affected"] or pods["affected"]:
        lines.append("- Inspect the affected resources and their current Kubernetes events.")
    if report["sources"].get("mimir") != "ok":
        lines.append("- Check the Mimir CPU/memory scrape and query windows.")
    if report["state_code"] == "indeterminate":
        lines.append("- Retry the node and pod reads to complete the snapshot.")
    if not nodes["affected"] and not pods["affected"]:
        lines.append("- Continue monitoring current Kubernetes state and available metrics.")
    lines += ["", technical_details(title="Technical details / Evidence", lines=[
        f"Snapshot: {report.get('snapshot_id') or 'unidentified'} | "
        f"mode={report.get('mode', 'LIVE')} | window={report.get('window_seconds')} s",
        f"Observed UTC: {report['observed_at']}",
        "Sources: " + ", ".join(f"{name}={state}" for name, state in report["sources"].items()),
        f"Mimir CPU: {metrics['cpu_cores']} cores | Mimir memory: "
        f"{metrics['memory_working_set_bytes']} bytes",
        "Limits: " + "; ".join(_english_limit(item) for item in report.get("limits", [])),
        ai_note,
    ])]
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
