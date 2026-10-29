from app.services.intents import is_write_request, refusal_text
from app.services.presentation import technical_details
from app.services.user_errors import language_of


def test_restart_observations_are_not_write_requests():
    questions = [
        "Check pod restarts.",
        "How many times did the pod restart?",
        "Show restart counts.",
        "Check nodes, pods, restarts and recent alerts.",
        "Analyse les redémarrages des conteneurs.",
        "Pourquoi le pod a-t-il redémarré ?",
        "Check whether any pod has been deleted.",
        "Explain how to restart a pod safely.",
        "Do not restart anything; just show restart counts.",
        "What is the current health status of my Kubernetes cluster? Check nodes, pods, restarts and recent alerts.",
    ]
    for question in questions:
        assert is_write_request(question) is False, question


def test_destructive_requests_are_refused():
    questions = [
        "Restart pod coredns now.",
        "Delete the failing pod.",
        "Scale the deployment to 3 replicas.",
        "Apply this Kubernetes manifest.",
        "Redémarre le pod maintenant.",
        "Supprime le namespace applications.",
        "Can you restart the kubeai-agent pod?",
    ]
    for question in questions:
        assert is_write_request(question) is True, question


def test_language_detection_is_automatic_and_explicit_override_wins():
    assert language_of("Quel est l'état de santé de mon cluster ?") == "fr"
    assert language_of("What is the health status of my cluster?") == "en"
    assert language_of("Check nodes, pods, restarts and alerts.") == "en"
    assert language_of("Why did my pod restart?") == "en"
    assert language_of("Pourquoi le pod a-t-il redémarré ?") == "fr"
    assert language_of("Réponds en anglais : quel est l'état du cluster ?") == "en"
    assert language_of("Answer in French: what is the cluster health?") == "fr"


def test_refusal_follows_the_question_language():
    assert refusal_text("Delete the pod.").startswith("I cannot")
    assert refusal_text("Supprime le pod.").startswith("Je ne peux pas")


def test_technical_evidence_is_safe_markdown_not_raw_html():
    rendered = technical_details(["CPU: 0.1", "Pod: applications/demo"])
    assert "KUBEAI_TECHNICAL_DETAILS|Détails techniques / Preuves" in rendered
    assert "<details>" not in rendered
    assert "<pre>" not in rendered
    assert "CPU: 0.1" in rendered
    assert "Pod: applications/demo" in rendered
