"""Render current namespace health facts without asking a model to reinterpret them."""
import re

from app.models import ChatRequest
from app.conversation import plain


def health_question(chat: ChatRequest):
    questions = [message.content for message in chat.messages if message.role == "user"]
    question = questions[-1]
    normalized = plain(question)
    if re.match(r"\s*(et maintenant|et ce pod|and now|what about now|how about now|now\b)", normalized):
        previous = next((item for item in reversed(questions[:-1]) if not re.match(
            r"\s*(et maintenant|and now|what about now|how about now|now\b)", item, re.I)), "")
        question = previous + "\n" + question
        normalized = plain(question)
    if not re.search(
        r"\b(health|healthy|unhealthy|sante|sain|problemes?|problems?|failing)\b"
        r"|\b(state|status|etat)\b.*\b(namespace|kubeai|pod)\b"
        r"|\bpods?\b.*\b(ready|pret|prets|failed|running)\b", normalized,
    ):
        return None
    if re.search(r"\b(why|cause|causes|caused|pourquoi|explique|explain)\b", normalized):
        return None
    return question


def health_answer(chat: ChatRequest, observation: dict):
    question = health_question(chat)
    if question is None:
        return None
    summary = observation["summary"]
    lines = [
        "### 1. État observé",
        f"L'état Kubernetes actuel du namespace `{observation['namespace']}` est **{observation['health']}** [K8S].",
        "",
        "### 2. Preuves et sources",
        f"Relevé à {observation['observed_at']} [K8S] : {summary['total']} pods, "
        f"{summary['ready']} Ready, {summary['unhealthy']} en défaut, {summary['restarts']} redémarrages cumulés.",
    ]
    references = re.findall(
        r"(?<!ce )(?<!this )(?<!that )\bpod\s+[`\"']?([a-z0-9]+(?:-[a-z0-9]+)+)", question, re.I,
    )
    names = {pod["name"] for pod in observation["pods"]}
    for name in dict.fromkeys(references):
        if name not in names and not observation["truncated"]:
            lines.append(f"Le pod `{name}` ne figure plus dans l'inventaire actuel [K8S].")
    failed = [pod for pod in observation["pods"] if pod["health"] == "unhealthy"]
    simulation = False
    for pod in failed:
        issues = "; ".join(pod["issues"])
        lines.append(f"- `{pod['name']}` : **{pod['phase']}**, Ready=false" +
                     (f" ; {issues}" if issues else "") + " [K8S].")
        logs = [entry for entry in observation["logs"] if entry["pod"] == pod["name"]]
        if logs:
            entry = logs[0]
            lines.append(f"  Log à {entry.get('timestamp', entry['timestamp_ns'])} [LOGS] : {entry['message'].strip()!r}.")
            simulation |= any(re.search(r"\b(simulated|simulation)\b", item["message"], re.I) for item in logs)
    if summary["unhealthy"] == 0:
        lines.append("Aucun pod en défaut n'est signalé dans ce namespace [K8S]. Cela ne démontre pas la santé du cluster entier ni de l'application.")
    lines += ["", "### 3. Hypothèses de cause"]
    lines.append("Les seules données présentes ne permettent pas d'établir une cause.")
    if simulation:
        lines.append("Un log décrit une simulation ; il ne prouve pas qu'une configuration est réellement absente. L'état Failed reste observé [K8S].")
    lines += ["", "### 4. Informations manquantes"]
    missing = []
    if observation["data_status"] == "partial":
        missing.append("certaines sources d'observation sont indisponibles ou partielles")
    if not observation["logs"]:
        missing.append("aucun log récent disponible")
    if observation["truncated"]:
        missing.append("l'inventaire est limité aux 20 premiers pods prioritaires")
    lines.append(" ; ".join(missing).capitalize() + "." if missing else
                 "Aucune autre preuve de cause n'est fournie par ce relevé.")
    lines += ["", "### 5. Vérifications recommandées",
              "Vérifier les événements et la configuration du pod concerné si un diagnostic de cause est nécessaire ; comparer avec un nouveau relevé horodaté."]
    return "\n".join(lines)
