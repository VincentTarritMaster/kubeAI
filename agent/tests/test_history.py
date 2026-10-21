"""Bound Open WebUI request context without modifying stored conversation history."""

from copy import deepcopy

from app.api.chat import diagnostic_route
from app.models import ChatRequest
from app.services.incident_timeline import parse_chat_window


def _texts(request):
    return [(item.role, item.content) for item in request.messages]


def test_short_and_under_limit_conversations_are_unchanged():
    for messages in (
        [{"role": "system", "content": "Client preference"},
         {"role": "user", "content": "Question A"},
         {"role": "assistant", "content": "Réponse A"},
         {"role": "user", "content": "Question B"}],
        [{"role": "user", "content": "ancien" * 1000},
         {"role": "assistant", "content": "réponse" * 1000},
         {"role": "user", "content": "courant" * 100}],
        [{"role": "user", "content": "Question en cours"},
         {"role": "assistant", "content": "Réponse déjà fournie"}],
        [{"role": "user", "content": "A" * 4000},
         {"role": "assistant", "content": "B" * 4000},
         {"role": "user", "content": "C" * 8000}],
    ):
        original = deepcopy(messages)
        request = ChatRequest.model_validate({"messages": messages})
        assert _texts(request) == [(item["role"], item["content"]) for item in original]
        assert messages == original


def test_over_limit_keeps_recent_complete_turn_and_current_question():
    messages = [
        {"role": "user", "content": "ancien" * 500},
        {"role": "assistant", "content": "vieille réponse" * 450},
        {"role": "user", "content": "question récente" * 150},
        {"role": "assistant", "content": "réponse récente" * 300},
        {"role": "user", "content": "Demande actuelle"},
    ]
    original = deepcopy(messages)
    request = ChatRequest.model_validate({"messages": messages})
    assert sum(len(item.content) for item in request.messages) <= 16000
    assert request.messages[-1].content == "Demande actuelle"
    assert ("assistant", messages[3]["content"]) in _texts(request)
    assert ("user", messages[2]["content"]) in _texts(request)
    assert ("assistant", messages[1]["content"]) not in _texts(request)
    assert messages == original


def test_long_incident_answer_does_not_erase_question_needed_by_correlation():
    incident = ("Analyse l'incident du pod kubeai-demo-incident dans le namespace kubeai, "
                "le 19 octobre 2026 entre 18:25 et 18:33 UTC.")
    health = "Quel est l'état de santé de mon cluster ?"
    correlation = "Pour ce même incident, corrèle les logs et le CPU."
    messages = [
        {"role": "user", "content": incident},
        {"role": "assistant", "content": "B" * 25000},
        {"role": "user", "content": health},
        {"role": "assistant", "content": "A" * 18000},
        {"role": "user", "content": correlation},
    ]
    before = deepcopy(messages)
    request = ChatRequest.model_validate({"messages": messages})
    assert _texts(request) == [("user", incident), ("user", health), ("user", correlation)]
    assert diagnostic_route(request) == "correlation"
    window, error = parse_chat_window(request)
    assert error is None
    assert window["namespace"] == "kubeai"
    assert window["pod"] == "kubeai-demo-incident"
    assert messages == before


def test_message_count_is_reduced_from_oldest_turns():
    messages = []
    for index in range(40):
        messages.extend(({"role": "user", "content": f"question {index}"},
                         {"role": "assistant", "content": f"answer {index}"}))
    messages.append({"role": "user", "content": "question finale"})
    request = ChatRequest.model_validate({"messages": messages})
    assert len(request.messages) <= 32
    assert request.messages[-1].content == "question finale"
    assert all(item.content not in {"question 0", "answer 0"} for item in request.messages)
