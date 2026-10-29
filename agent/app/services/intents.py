"""Small, conservative intent guards for the read-only demo assistant."""
import re

# An imperative at the start of a question is normally a consultation
# ("Check restarts", "Why did it restart?", "How to restart safely").
READ_PREFIX = (
    "check", "vérifie", "verifie", "show", "affiche", "display", "list", "analyse",
    "why", "pourquoi", "how", "comment", "explain", "explique", "what", "quel",
    "quelle", "combien", "is", "are", "est", "sont", "do", "does", "did", "has",
    "have", "can you explain", "could you explain", "peux-tu expliquer",
    "pouvez-vous expliquer", "what happened", "que s'est-il passé",
)
POLITE_PREFIX = (
    "please ", "merci de ", "peux-tu ", "pouvez-vous ", "can you ", "could you ",
    "i want you to ", "je veux que tu ", "je veux que vous ",
)
# These are actions, but not necessarily destructive; the target/immediate
# checks below decide whether the user is asking for a real mutation.
ACTION = re.compile(
    r"\b(restart|restarting|redémarrer|redemarrer|delete|remove|scale|apply|patch|edit|create|kill|terminate|drain|"
    r"cordon|uncordon|redémarre|redemarre|supprime|supprimer|applique|modifie|"
    r"efface|déploie|deploie)\b",
    re.IGNORECASE,
)
NEGATIVE = re.compile(
    r"\b(do not|don't|never|sans|ne pas|n'|aucune?|without)\b", re.IGNORECASE
)
TARGET = re.compile(
    r"\b(pod|pods|deployment|deployments|namespace|namespaces|service|services|"
    r"node|nodes|daemonset|statefulset|job|cronjob|resource|manifest|application|"
    r"applications|container|containers|conteneur|conteneurs|cluster)\b",
    re.IGNORECASE,
)
IMMEDIATE = re.compile(
    r"\b(now|immediately|right now|maintenant|tout de suite|immédiatement)\b",
    re.IGNORECASE,
)
OBSERVATION = re.compile(
    r"\b(restart counts?|counts?|times?|history|fréquence|frequence|"
    r"redémarrages?|redemarrages?)\b",
    re.IGNORECASE,
)


def is_write_request(text: str) -> bool:
    """True only for an imperative mutation request; false for observations.

    This is a UI guard, not the security boundary. Kubernetes RBAC remains the
    authority and the agent has no write tool at all.
    """
    raw = (text or "").strip()
    if not raw:
        return False
    lowered = raw.lower()
    if NEGATIVE.search(lowered[:80]):
        return False

    # Remove one polite prefix, then inspect the leading verb. "Please restart"
    # remains a mutation; "Please check restarts" remains a consultation.
    core = lowered
    for prefix in POLITE_PREFIX:
        if core.startswith(prefix):
            core = core[len(prefix):].lstrip()
            break

    if re.match(r"^(?:" + "|".join(re.escape(word) for word in READ_PREFIX) + r")\b", core):
        return False

    action = ACTION.search(core)
    if not action:
        return False
    # "Show restart counts" / "check restart history" are observations even
    # if they contain an action-looking token.
    if OBSERVATION.search(core) and not IMMEDIATE.search(core):
        return False
    # "has been deleted" / "was restarted" are past participles, not commands.
    after = core[action.end():action.end() + 20]
    if re.match(r"\s*(?:ed|é|ée|és|ées)\b", after):
        return False
    target = TARGET.search(core)
    if not target:
        return False
    # A resource-targeted action after the consultation/negative guards is a
    # mutation request. This covers "Can you restart the pod?" as well as
    # "Restart it now" while keeping "Check restarts" read-only.
    return bool(target)


def refusal_text(question: str) -> str:
    from app.services.user_errors import language_of

    if language_of(question) == "en":
        return ("I cannot delete, restart, scale, patch or modify a Kubernetes resource: "
                "KubeAI is strictly read-only. No action was executed. "
                "You can ask for a diagnosis, available evidence, or read-only checks.")
    return ("Je ne peux pas supprimer, redémarrer, corriger ou modifier une ressource Kubernetes : "
            "KubeAI est strictement en lecture seule. Aucune action n'a été exécutée. "
            "Vous pouvez demander un diagnostic, les preuves disponibles ou des vérifications en lecture seule.")
