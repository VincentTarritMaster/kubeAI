"""Request-local dialogue state derived from user turns, before history reduction.

No assistant diagnostic is accepted as evidence. Clarification choices are only
resource candidates and must be checked against read-only sources again.
"""
from dataclasses import dataclass
import re
import unicodedata


def plain(text):
    return unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower()


def explicit_intent(text):
    text = plain(text)
    # A current cluster inventory may explicitly request restart counters. Those
    # counters do not turn a health request into a historical investigation.
    health = bool((re.search(r"\b(cluster|grappe|kubernetes)\b", text) and re.search(
        r"(?:etat|sante|health|status|bilan|vue d.ensemble|inventaire|comment va|va bien|fais le point|problemes?|verifie|inspecte)", text))
        or re.search(r"\balertes?\b.*\b(actives?|recentes?|actuelles?)\b", text))
    historical = bool(re.search(r"(?<![\w-])(?:incident|chronologie|investigation|investigue|enquete)(?![\w-])", text))
    causal = bool(re.search(r"\b(correl\w*|hypothes\w*|caus\w*|pourquoi|latence|lenteur)\b", text))
    if health and not historical and not causal:
        return "cluster"
    if causal:
        return "correlation"
    if historical or re.search(r"(?:que s.est.il passe|a.t.il redemarre|a redemarre|crash|panne|debut=)", text):
        return "incident"
    return None


def is_reference(text):
    return bool(re.search(r"(?:cet incident|ce meme incident|meme incident|incident precedent|"
                          r"incident passe|ce pod|ses logs|ses metriques|repren\w*|reprends|"
                          r"tout ca|cause probable|same incident|previous incident)", plain(text)))


def _resource(text):
    match = re.search(r"\bpod\s*(?:=|:)?\s+([a-z0-9][a-z0-9-]*)", plain(text))
    if not match:
        match = re.search(r"\bpod=([a-z0-9][a-z0-9-]*)", plain(text))
    if not match:
        match = re.search(r"\bincident\s+([a-z0-9]+-[a-z0-9-]+)", plain(text))
    return match.group(1) if match and match.group(1) not in {"precedent", "concerne", "a-t-il", "est", "suivant"} else None


def _fragment(text):
    normalized = plain(text).strip()
    return len(text) <= 400 and bool(re.search(
        r"^(?:(?:c.est|dans|du|le|en|a|oui|non|plutot|namespace|pod|debut|fin)\b|\d{1,4}[-/]|"
        r"hier\b|aujourd.hui\b|ce matin\b)", normalized))


def _ordinal(text):
    normalized = plain(text).strip(" .?!")
    choices = {"le premier": 0, "la premiere": 0, "le 1er": 0, "1": 0,
               "le second": 1, "le deuxieme": 1, "la deuxieme": 1, "2": 1,
               "le troisieme": 2, "3": 2}
    return choices.get(normalized)


OUT_OF_SCOPE = ("Je suis KubeAI, spécialisé dans le monitoring Kubernetes : santé du cluster, "
                "logs, métriques et investigation d'incidents. Cette question sort de mon périmètre. "
                "Posez-moi une question sur votre cluster ou sur un incident à examiner.")


def in_scope(text, *, contextual=False):
    normalized = plain(text)
    hardware = bool(re.search(r"\b(noeuds?|nodes?|serveurs?|materiel|hardware|cpu|processeur)\b", normalized))
    unrelated = bool(re.search(r"\b(meteo|weather|football|soccer|recette|cuisine|horoscope|"
                               r"vacances|poeme|chanson|politique)\b|quel temps|temperature (?:exterieure|d.aujourd)", normalized))
    if unrelated and not (hardware and re.search(r"temperature", normalized)):
        return False
    if re.search(r"\b(kubernetes|k8s|kubeai|cluster|grappe|pods?|noeuds?|nodes?|namespace|"
                 r"cpu|memoire|memory|logs?|metriques?|metrics|monitoring|observations?|"
                 r"prometheus|mimir|loki|conteneurs?|containers?|deploiement|deployment|"
                 r"oomkilled|crashloopbackoff|latence|restarts?|health|application|nestjs|alertes?|alerts?|incident|chronologie)\b|\b(?:debut|fin)=", normalized):
        return True
    if explicit_intent(text) == "incident":
        return True
    if re.search(r"(?:que se passe.t.il|what is happening|investig\w*.*incident|analyse.*incident|"
                 r"incident.*[a-z0-9]+-[a-z0-9-]+)", normalized):
        return True
    return contextual


@dataclass(frozen=True)
class Dialogue:
    route: str = "chat"
    incident_turns: tuple[str, ...] = ()
    choices: tuple[tuple[str, str], ...] = ()
    selection: int | None = None


def replay(messages):
    """Fold the complete submitted history into a bounded incident thread."""
    incident = []
    route = "chat"
    incident_route = "incident"
    last_diagnostic = "chat"
    pending = None
    candidates = ()
    selection = None
    active_scope = False
    for message in messages:
        role = message.get("role") if isinstance(message, dict) else getattr(message, "role", None)
        text = message.get("content") if isinstance(message, dict) else getattr(message, "content", None)
        if not isinstance(text, str):
            continue
        if role == "assistant":
            # Only read the bounded clarification preface, never old facts.
            preface = text[:1600]
            normalized = plain(preface)
            if normalized.startswith("plusieurs pods correspondent"):
                candidates = tuple(dict.fromkeys(re.findall(
                    r"\b([a-z0-9][a-z0-9-]{0,62})/([a-z0-9][a-z0-9-]{0,252})\b", preface)))[:6]
                pending = "resource"
            elif "quel namespace" in normalized or "quel espace de noms" in normalized:
                pending = "namespace"
            elif normalized.startswith(("indiquez au moins une date", "quelle date", "a quelle date")):
                pending = "date"
            elif normalized.startswith("quel pod"):
                pending = "pod"
            continue
        if role != "user" or len(text) > 12000:
            continue
        intent = explicit_intent(text)
        normalized = plain(text).strip(" .?!")
        short = normalized in {"pourquoi", "quelles preuves", "quelle preuve", "et maintenant",
                               "et les logs", "les preuves", "explique", "continue", "et ensuite",
                               "and now", "what about now", "how about now"}
        ordinal = _ordinal(text)
        update = text
        if pending == "namespace" and re.fullmatch(r"[a-z0-9][a-z0-9-]{0,62}", normalized):
            update = "namespace=" + normalized
        if pending == "resource" and normalized in {namespace for namespace, _ in candidates}:
            update = "namespace=" + normalized
        if pending == "pod" and re.fullmatch(r"[a-z0-9][a-z0-9-]{0,252}", normalized):
            update = "pod=" + normalized
        contextual = bool((incident and (short or is_reference(text) or _fragment(text) or ordinal is not None or
                                         (pending and update != text))) or
                          (active_scope and short))
        if not in_scope(text, contextual=contextual):
            route = "out_of_scope"
            continue
        active_scope = True
        if normalized == "pourquoi" and last_diagnostic == "cluster":
            route = "cluster"
            continue
        if intent == "cluster":
            route = last_diagnostic = "cluster"
            pending = None
            selection = None
            continue
        if intent in {"incident", "correlation"}:
            old_resource = next((_resource(item) for item in reversed(incident) if _resource(item)), None)
            new_resource = _resource(text)
            new_target = new_resource and old_resource and new_resource != old_resource
            inherit = incident and not new_target and (
                is_reference(text) or (intent == "correlation" and not new_resource) or
                (pending and _fragment(text)))
            if not inherit:
                incident = [text]
                candidates = ()
            elif not short:
                incident.append(text)
            route = last_diagnostic = incident_route = intent
            pending = None
            selection = None
        elif incident and (ordinal is not None or (pending and (update != text or _fragment(text))) or
                           (_fragment(text) and re.search(r"\b(namespace|pod|octobre|janvier|fevrier|mars|avril|mai|juin|juillet|aout|septembre|novembre|decembre)\b", plain(text)))):
            if ordinal is None:
                incident.append(update)
            selection = ordinal
            route = last_diagnostic = incident_route
            pending = None
        elif short or is_reference(text):
            route = last_diagnostic
            if route == "incident" and normalized == "pourquoi":
                route = "correlation"
            if route in {"incident", "correlation"}:
                incident_route = route
            selection = None
        else:
            route = "chat"
            selection = None
        # Keep the original incident request and its latest clarifications. A/B/C
        # routing and source parameters survive 32-message/16k context trimming.
        if len(incident) > 5:
            incident = [incident[0], *incident[-4:]]
    return Dialogue(route, tuple(incident), candidates, selection)


def dialogue(chat):
    cached = getattr(chat, "_dialogue", None)
    return cached if isinstance(cached, Dialogue) else replay(chat.messages)
