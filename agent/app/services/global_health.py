"""Evidence-based global cluster health assessment for demonstration A."""

from datetime import timedelta
import json
import re
import unicodedata

from app.models import ChatRequest, ClusterInventory
from app.clients.prometheus_client import ClusterUsage
from app.services.privacy import redact_data

WARNING_WINDOW = timedelta(minutes=15)
RESTART_THRESHOLD = 3  # Project rule for cumulative restarts, not a recent rate.


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
    recent_warnings = []
    for event in events or []:
        if event.type != "Warning" or event.observed_at is None:
            continue
        age = observed_at - event.observed_at
        if -timedelta(minutes=5) <= age <= WARNING_WINDOW:
            recent_warnings.append({
                "at": event.observed_at.isoformat(), "namespace": event.namespace,
                "kind": event.involved_kind, "name": event.involved_name,
                "reason": event.reason,
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
    degraded = bool(bad_nodes or bad_pods or any(item["count"] >= RESTART_THRESHOLD for item in restarting)
                    or recent_warnings)
    essential_missing = (nodes is None or pods is None or not nodes
                         or inventory.sources["nodes"].status != "ok"
                         or inventory.sources["pods"].status != "ok"
                         or any(node.ready is None for node in nodes or [])
                         or any(pod.health == "unknown" for pod in pods or []))
    if critical:
        state = "critique"
    elif degraded:
        state = "dégradé"
    elif essential_missing:
        state = "indéterminé"
    else:
        state = "sain selon les contrôles effectués"

    report = {
        "scope": "cluster", "observed_at": observed_at.isoformat(), "state": state,
        "nodes": {"total": len(nodes) if nodes is not None else None,
                  "ready": sum(node.ready is True for node in nodes) if nodes is not None else None,
                  "not_ready": len(bad_nodes) if nodes is not None else None,
                  "affected": bad_nodes[:5]},
        "pods": {"total": len(pods) if pods is not None else None,
                 "running": sum(pod.phase == "Running" for pod in pods) if pods is not None else None,
                 "unhealthy": len(bad_pods) if pods is not None else None,
                 "affected": bad_pods[:8]},
        "restarting": restarting[:5], "restarting_total": len(restarting),
        "recent_warnings": recent_warnings[:5], "recent_warnings_total": len(recent_warnings),
        "metrics": {"cpu_cores": usage.cpu_cores if usage else None,
                    "memory_working_set_bytes": usage.memory_working_set_bytes if usage else None},
        "sources": {name: source.status for name, source in inventory.sources.items()}
                   | {"mimir": "unavailable" if mimir_error else
                      "partial" if usage is None or usage.cpu_cores is None
                      or usage.memory_working_set_bytes is None or usage.warnings else "ok",
                      "alerts": "not_configured"},
        "limits": limits,
        "truncated": any(source.status == "partial" for source in inventory.sources.values()),
    }
    return redact_data(report)


def render(report: dict, *, ai_note: str) -> str:
    nodes, pods, metrics = report["nodes"], report["pods"], report["metrics"]
    lines = [f"**État global : {report['state']}**", ""]
    lines.append("- Nœuds : données indisponibles." if nodes["total"] is None else
                 f"- Nœuds : {nodes['ready']} Ready, {nodes['not_ready']} NotReady sur {nodes['total']} observés.")
    lines.append("- Pods : données indisponibles." if pods["total"] is None else
                 f"- Pods : {pods['running']} Running, {pods['unhealthy']} en erreur sur {pods['total']} observés, tous namespaces confondus.")
    restarting = report["restarting"]
    lines.append(f"- Redémarrages : {report['restarting_total']} conteneur(s) avec un cumul non nul."
                 if restarting else "- Redémarrages : aucun cumul non nul observé dans les données disponibles.")
    if metrics["cpu_cores"] is None and metrics["memory_working_set_bytes"] is None:
        lines.append("- Métriques : CPU et mémoire indisponibles dans Mimir.")
    else:
        parts = []
        if metrics["cpu_cores"] is not None:
            parts.append(f"CPU des conteneurs observés {metrics['cpu_cores']:.3f} cœur(s)")
        if metrics["memory_working_set_bytes"] is not None:
            parts.append(f"mémoire working set {metrics['memory_working_set_bytes'] / 1048576:.1f} Mio")
        lines.append("- Métriques Mimir : " + ", ".join(parts) + ". Aucune conclusion de saturation sans capacité ni seuil validé.")
    lines.append(f"- Événements : {report['recent_warnings_total']} avertissement(s) daté(s) dans les 15 dernières minutes."
                 if report["sources"]["events"] != "unavailable" else "- Événements : source indisponible.")
    lines += ["", "**Diagnostic :** " + {
        "critique": "aucun nœud observé n'est Ready ; la disponibilité du cluster exige une vérification immédiate.",
        "dégradé": "des anomalies observées affectent au moins un nœud, pod, conteneur ou événement récent.",
        "indéterminé": "les données essentielles ne permettent pas de conclure sur la santé globale.",
        "sain selon les contrôles effectués": "aucune anomalie n'est visible dans les contrôles disponibles ; cela ne prouve pas la santé de chaque application.",
    }[report["state"]], "", "**Preuves :**"]
    lines.append(f"- Relevé à {report['observed_at']} ; sources : " +
                 ", ".join(f"{name}={state}" for name, state in report["sources"].items()) + ".")
    for name in nodes["affected"]:
        lines.append(f"- Nœud `{name}` : NotReady [Kubernetes].")
    for item in pods["affected"]:
        lines.append(f"- Pod `{item['namespace']}/{item['name']}` : {item['phase']}, {item['reason']} [Kubernetes].")
    for item in restarting:
        lines.append(f"- `{item['namespace']}/{item['pod']}/{item['container']}` : {item['count']} redémarrage(s) cumulés [Kubernetes].")
    for item in report["recent_warnings"]:
        target = "/".join(part for part in (item["namespace"], item["kind"], item["name"]) if part)
        lines.append(f"- {item['at']} : Warning {item['reason'] or 'sans motif'}, {target or 'objet non précisé'} [Kubernetes Events].")
    if not (nodes["affected"] or pods["affected"] or restarting or report["recent_warnings"]):
        lines.append("- Aucun signal d'anomalie dans les ressources accessibles à ce relevé.")
    lines += ["", "**Limites :** " + "; ".join(report["limits"]) + ".",
              "", "**Vérifications proposées :** comparer ce relevé avec `kubectl get nodes`, "
              "`kubectl get pods -A` et `kubectl get events -A` sur un cluster autorisé, "
              "puis consulter les métriques Mimir sans modifier les ressources.",
              "", ai_note]
    return "\n".join(lines)


def model_focus(report: dict, content: str) -> str | None:
    """Accept only a focus category backed by evidence; never publish model prose."""
    try:
        focus = json.loads(content)["focus"]
    except (ValueError, KeyError, TypeError):
        return None
    available = {
        "nodes": bool(report["nodes"]["affected"]),
        "pods": bool(report["pods"]["affected"]),
        "restarts": any(item["count"] >= RESTART_THRESHOLD for item in report["restarting"]),
        "events": bool(report["recent_warnings"]),
        "availability": report["state"] == "indéterminé",
        "none": report["state"] == "sain selon les contrôles effectués",
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
