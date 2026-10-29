"""Stable, localized user-facing errors with a diagnostic correlation ID."""
import re
import uuid

from app.clients.base import UpstreamError


FRENCH_HINT = re.compile(
    r"[àâçéèêëîïôùûüÿœ]|"
    r"\b(quel|quelle|quels|quelles|état|santé|dernier|dernière|"
    r"journaux|métriques|erreur|réponse|pourquoi|comment|"
    r"nœud|noeud|espace|tout|tous|toutes|fonctionne|fonctionnent|correctement|"
    r"bilan|point|supprime|supprimer|redémarre|redemarrer|redémarrages?|"
    r"debut|début|fin|de|du|des|le|la|les|un|une)\b",
    re.IGNORECASE,
)
ENGLISH_HINT = re.compile(
    r"\b(the|and|is|are|what|why|how|check|health|healthy|status|show|please|"
    r"restarts?|restarted|nodes?|pods?|cluster|alerts?|delete|scale|apply|"
    r"explain|counts?|does|did|has|have|my|of|to|in|with|from|for|not|just)\b",
    re.IGNORECASE,
)


def language_of(question: str) -> str:
    text = question or ""
    lowered = text.lower()
    if re.search(r"\b(answer|reply|réponds|reponds|respond)\b.{0,20}\b(english|anglais)\b", lowered):
        return "en"
    if re.search(r"\b(answer|reply|réponds|reponds|respond)\b.{0,20}\b(french|français|francais)\b", lowered):
        return "fr"
    fr = len(FRENCH_HINT.findall(text))
    en = len(ENGLISH_HINT.findall(text))
    if fr > en:
        return "fr"
    if en > fr:
        return "en"
    # Short technical questions without discriminative words are English by
    # default; the demo questions always contain at least one clear cue.
    return "en"


def new_diagnostic_id() -> str:
    return "kubeai-" + uuid.uuid4().hex[:12]


def classify(error: Exception) -> tuple[str, str, str, bool]:
    """Return (code, component, source_message, retryable)."""
    if isinstance(error, UpstreamError):
        source = (error.source or "agent").lower()
        message = (error.message or "").lower()
        if source == "llm":
            if "401" in message or "403" in message or "auth" in message or "clé" in message:
                return "LLM_AUTH", "mistral", error.message, False
            if "429" in message or "quota" in message or "limite de requêtes" in message:
                return "LLM_QUOTA", "mistral", error.message, True
            if "500" in message or "503" in message or "indisponible" in message or "unavailable" in message:
                return "LLM_UNAVAILABLE", "mistral", error.message, True
            if "timeout" in message or "timed out" in message or "délai" in message:
                return "LLM_TIMEOUT", "mistral", error.message, True
            if "connect" in message or "connexion" in message:
                return "LLM_CONNECTION", "mistral", error.message, True
            if "json" in message or "structured" in message or "tool" in message:
                return "LLM_INVALID_TOOL_CALL", "mistral", error.message, True
            return "LLM_UNAVAILABLE", "mistral", error.message, True
        if source == "kubernetes":
            if "401" in message or "403" in message or "forbidden" in message or "unauthorized" in message:
                return "K8S_FORBIDDEN", "kubernetes", error.message, False
            return "K8S_UNAVAILABLE", "kubernetes", error.message, True
        if source == "mimir":
            return "MIMIR_UNAVAILABLE", "mimir", error.message, True
        if source == "loki":
            return "LOKI_UNAVAILABLE", "loki", error.message, True
        if "timeout" in message or "timed out" in message or "délai" in message:
            return "UPSTREAM_TIMEOUT", source, error.message, True
        return "UPSTREAM_UNAVAILABLE", source, error.message, True
    return "INTERNAL_ERROR", "agent", str(error), False


def user_error(error: Exception, question: str, diagnostic_id: str | None = None) -> dict:
    code, component, source_message, retryable = classify(error)
    diagnostic_id = diagnostic_id or new_diagnostic_id()
    lang = language_of(question)
    messages = {
        "LLM_INVALID_TOOL_CALL": {
            "fr": "Le modèle IA n'a pas pu interpréter la demande : la réponse JSON était invalide ou incomplète. Les données Kubernetes n'ont pas été modifiées. Réessayez dans une nouvelle conversation.",
            "en": "The AI model could not interpret the request: its JSON response was invalid or incomplete. No Kubernetes data was changed. Please retry in a new conversation.",
        },
        "LLM_AUTH": {
            "fr": "Authentification au modèle IA impossible. Clé Mistral manquante ou refusée. Le diagnostic ne peut pas être produit.",
            "en": "AI model authentication failed. The Mistral key is missing or rejected. The diagnosis cannot be produced.",
        },
        "LLM_QUOTA": {
            "fr": "Quota ou limite de requêtes du modèle IA atteint. Le diagnostic ne peut pas être produit pour le moment.",
            "en": "The AI model request quota or rate limit was reached. The diagnosis cannot be produced right now.",
        },
        "LLM_UNAVAILABLE": {
            "fr": "Le service du modèle IA est indisponible. Le diagnostic ne peut pas être produit.",
            "en": "The AI model service is unavailable. The diagnosis cannot be produced.",
        },
        "LLM_TIMEOUT": {
            "fr": "Délai dépassé en attendant le modèle IA. Le diagnostic est incomplet ; réessayez.",
            "en": "Timed out while waiting for the AI model. The diagnosis is incomplete; please retry.",
        },
        "LLM_CONNECTION": {
            "fr": "Connexion au modèle IA interrompue. Le diagnostic est incomplet ; réessayez.",
            "en": "The connection to the AI model was interrupted. The diagnosis is incomplete; please retry.",
        },
        "UPSTREAM_TIMEOUT": {
            "fr": "Délai dépassé : le service de supervision ne répond pas dans le délai prévu. Le diagnostic est incomplet ; réessayez.",
            "en": "Timeout: the observability service did not respond in time. The diagnosis is incomplete; please retry.",
        },
        "K8S_FORBIDDEN": {
            "fr": "L'agent n'est pas autorisé à consulter cette ressource Kubernetes. Vérifiez les permissions du compte de service.",
            "en": "The agent is not authorized to read this Kubernetes resource. Check the service account permissions.",
        },
        "K8S_UNAVAILABLE": {
            "fr": "L'API Kubernetes est indisponible. Le diagnostic est impossible pour le moment.",
            "en": "The Kubernetes API is unavailable. The diagnosis is not possible right now.",
        },
        "MIMIR_UNAVAILABLE": {
            "fr": "Impossible de récupérer les métriques Kubernetes depuis Mimir. Le diagnostic est incomplet ; les journaux Loki restent éventuellement disponibles.",
            "en": "Kubernetes metrics could not be retrieved from Mimir. The diagnosis is incomplete; Loki logs may still be available.",
        },
        "LOKI_UNAVAILABLE": {
            "fr": "La consultation des journaux a échoué. Je ne peux pas confirmer la cause de l'incident à partir des logs.",
            "en": "Reading the logs failed. I cannot confirm the incident cause from the logs.",
        },
        "INTERNAL_ERROR": {
            "fr": "Une erreur interne a interrompu le diagnostic. Aucune ressource Kubernetes n'a été modifiée.",
            "en": "An internal error interrupted the diagnosis. No Kubernetes resource was changed.",
        },
        "UPSTREAM_UNAVAILABLE": {
            "fr": "Une source du diagnostic est indisponible. Le diagnostic est partiel.",
            "en": "A diagnosis source is unavailable. The diagnosis is partial.",
        },
    }
    message = messages.get(code, messages["INTERNAL_ERROR"])[lang]
    if lang == "fr" and source_message and code in {
        "LLM_AUTH", "LLM_QUOTA", "LLM_UNAVAILABLE", "LLM_TIMEOUT", "LLM_CONNECTION",
        "UPSTREAM_TIMEOUT",
    }:
        message += " Précision : " + source_message.rstrip(".") + "."
    message += f" Diagnostic : {diagnostic_id}."
    return {
        "code": code,
        "component": component,
        "source": error.source if isinstance(error, UpstreamError) else "agent",
        "message": message,
        "diagnostic_id": diagnostic_id,
        "complete": False,
        "retryable": retryable,
        "source_message": source_message,
    }
