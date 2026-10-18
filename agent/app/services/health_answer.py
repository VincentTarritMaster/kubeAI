"""Render current health facts without asking the model to reinterpret them."""
import re
import unicodedata

from app.models import ChatRequest


def health_question(chat: ChatRequest):
    questions = [message.content for message in chat.messages if message.role == "user"]
    question = questions[-1]
    normalized = unicodedata.normalize("NFKD", question).encode("ascii", "ignore").decode().lower()
    if re.match(r"\s*(et maintenant|et ce pod|and now|what about now|how about now|now\b)", normalized):
        previous = next((item for item in reversed(questions[:-1]) if not re.match(
            r"\s*(et maintenant|and now|what about now|how about now|now\b)", item, re.I)), "")
        question = previous + "\n" + question
        normalized = unicodedata.normalize("NFKD", question).encode("ascii", "ignore").decode().lower()
    if not re.search(
        r"\b(health|healthy|unhealthy|sante|sain|problemes?|problems?|failing)\b"
        r"|\b(state|status|etat)\b.*\b(namespace|kubeai|pod)\b"
        r"|\bpods?\b.*\b(ready|pret|prets|failed|running)\b", normalized,
    ):
        return None
    # Keep causal investigations with Apertus; plain state questions are facts.
    if re.search(r"\b(why|cause|causes|caused|pourquoi|explique|explain)\b", normalized):
        return None
    return question


def health_answer(chat: ChatRequest, observation: dict):
    question = health_question(chat)
    if question is None:
        return None
    summary = observation["summary"]
    answer = f"The current Kubernetes health of namespace `{observation['namespace']}` is **{observation['health']}** [K8S]."
    answer += f"\n\n{summary['total']} pods: {summary['ready']} Ready, {summary['unhealthy']} unhealthy; {summary['restarts']} restarts."
    # Resolve explicit pod references from questions, never from old diagnoses.
    references = re.findall(
        r"(?<!ce )(?<!this )(?<!that )\bpod\s+[`\"']?([a-z0-9]+(?:-[a-z0-9]+)+)", question, re.I,
    )
    names = {pod["name"] for pod in observation["pods"]}
    for name in dict.fromkeys(references):
        if name not in names and not observation["truncated"]:
            answer += f"\n\nPod `{name}` is no longer present in the current inventory [K8S]."
    failed = [pod for pod in observation["pods"] if pod["health"] == "unhealthy"]
    for pod in failed:
        issues = "; ".join(pod["issues"])
        answer += f"\n\n- `{pod['name']}`: **{pod['phase']}**, Ready=false"
        if issues:
            answer += "; " + issues
        answer += " [K8S]."
        logs = [entry["message"].strip() for entry in observation["logs"] if entry["pod"] == pod["name"]]
        if logs:
            answer += "\n  Recent log: " + repr(logs[0]) + " [LOGS]."
            if any(re.search(r"\b(simulated|simulation)\b", line, re.I) for line in logs):
                answer += "\n  The logs describe a simulation. The pod still counts as unhealthy in Kubernetes."
    if summary["unhealthy"] == 0:
        answer += "\n\nKubernetes currently reports no unhealthy pods in this namespace. This does not establish application or cluster-wide health."
    return answer
