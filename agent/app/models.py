from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, PrivateAttr, model_validator
from pydantic_core import PydanticCustomError
from app.conversation import Dialogue, replay


class SourceStatus(BaseModel):
    status: Literal["ok", "partial", "unavailable"]
    message: str | None = None


class Capabilities(BaseModel):
    """Runtime report of configured sources and optional metric families."""

    observed_at: datetime
    service: str
    version: str
    scope: Literal["cluster"] = "cluster"
    namespace: str
    read_only: bool = True
    sources: dict[str, str]
    metrics: dict[str, str]
    llm: dict[str, str | bool | None]
    limits: dict[str, int]


class ContainerStatus(BaseModel):
    name: str
    container_id: str | None = None
    kind: Literal["app", "init"]
    ready: bool
    restarts: int
    state: Literal["running", "waiting", "terminated", "unknown"]
    reason: str | None = None
    last_termination_reason: str | None = None
    last_exit_code: int | None = None


class PodMetrics(BaseModel):
    status: Literal["available", "partial", "unavailable", "not_applicable"]
    cpu_cores: float | None = Field(default=None, description="Two-minute average in CPU cores")
    memory_working_set_bytes: int | None = None


class PodStatus(BaseModel):
    name: str
    namespace: str
    uid: str
    node: str | None
    phase: str
    ready: bool
    health: Literal["healthy", "unhealthy", "completed", "terminating", "unknown"]
    restarts: int
    containers: list[ContainerStatus]
    issues: list[str]
    metrics: PodMetrics


class Summary(BaseModel):
    total: int = 0
    ready: int = 0
    unhealthy: int = 0
    completed: int = 0
    terminating: int = 0
    restarts: int = 0


class ClusterStatus(BaseModel):
    observed_at: datetime
    scope: Literal["namespace"] = "namespace"
    namespace: str
    health: Literal["healthy", "degraded", "unknown", "empty"]
    data_status: Literal["complete", "partial"]
    summary: Summary
    sources: dict[str, SourceStatus]
    warnings: list[str]
    pods: list[PodStatus]


class NodeStatus(BaseModel):
    name: str
    ready: bool | None
    health: Literal["Ready", "NotReady", "Unknown"]
    reason: str | None = None
    message: str | None = None


class ClusterContainerStatus(ContainerStatus):
    ready: bool | None = None
    restarts: int | None = None


class ClusterPodStatus(BaseModel):
    name: str
    namespace: str
    uid: str | None = None
    node: str | None = None
    phase: str
    ready: bool | None = None
    health: Literal["healthy", "unhealthy", "completed", "terminating", "unknown"]
    restarts: int | None = None
    containers: list[ClusterContainerStatus]
    issues: list[str]


class KubernetesEvent(BaseModel):
    name: str
    namespace: str | None = None
    involved_kind: str | None = None
    involved_name: str | None = None
    type: str | None = None
    reason: str | None = None
    message: str | None = None
    count: int | None = None
    first_observed_at: datetime | None = None
    observed_at: datetime | None = None


class ClusterInventorySummary(BaseModel):
    nodes: int | None = None
    nodes_ready: int | None = None
    nodes_not_ready: int | None = None
    pods: int | None = None
    pods_unhealthy: int | None = None
    restarts: int | None = None
    events: int | None = None


class ClusterInventory(BaseModel):
    observed_at: datetime
    scope: Literal["cluster"] = "cluster"
    health: Literal["healthy", "degraded", "unknown"]
    data_status: Literal["complete", "partial", "unavailable"]
    summary: ClusterInventorySummary
    sources: dict[str, SourceStatus]
    warnings: list[str]
    nodes: list[NodeStatus] | None
    pods: list[ClusterPodStatus] | None
    events: list[KubernetesEvent] | None


class ChatMessage(BaseModel):
    role: Literal["system", "user", "assistant"]
    content: str = Field(max_length=12000)


CHAT_HISTORY_CHAR_LIMIT = 16000
CHAT_HISTORY_MESSAGE_LIMIT = 32
CHAT_MESSAGE_CHAR_LIMIT = 12000


def _message_parts(message):
    if isinstance(message, ChatMessage):
        return message.role, message.content
    if isinstance(message, dict):
        return message.get("role"), message.get("content")
    return None, None


class ChatRequest(BaseModel):
    _dialogue: Dialogue = PrivateAttr(default_factory=Dialogue)
    model: str = "kubeai"
    messages: list[ChatMessage] = Field(min_length=1, max_length=CHAT_HISTORY_MESSAGE_LIMIT)
    stream: bool = False
    temperature: float = Field(default=0.2, ge=0, le=2)
    max_tokens: int | None = Field(default=None, ge=1, le=1024)
    max_completion_tokens: int | None = Field(default=None, ge=1, le=1024)

    @model_validator(mode="wrap")
    @classmethod
    def remember_dialogue(cls, data, handler):
        # Derive a compact request-only state before reducing old answers. It is
        # not an input field and cannot be supplied as trusted state by a client.
        snapshot = replay(data.get("messages", [])) if isinstance(data, dict) and isinstance(data.get("messages"), list) else None
        request = handler(data)
        if snapshot is not None:
            request._dialogue = snapshot
        return request

    @model_validator(mode="before")
    @classmethod
    def reduce_history(cls, data):
        """Bound only this request's copy; Open WebUI keeps its complete history."""
        if not isinstance(data, dict) or not isinstance(data.get("messages"), list):
            return data
        incoming = data["messages"]
        current = next((i for i in range(len(incoming) - 1, -1, -1)
                        if _message_parts(incoming[i])[0] == "user"), None)
        if current is None:
            return data  # The normal validator reports a missing user message.
        _, question = _message_parts(incoming[current])
        if not isinstance(question, str):
            return data  # Let ChatMessage report malformed content.
        if len(question) > CHAT_MESSAGE_CHAR_LIMIT:
            raise PydanticCustomError(
                "current_question_too_long",
                "Votre dernière question dépasse 12 000 caractères. Raccourcissez seulement cette question.",
            )

        # Leave a valid, already bounded request untouched, including any
        # trailing assistant message supplied by another compatible client.
        if len(incoming) <= CHAT_HISTORY_MESSAGE_LIMIT:
            parts = [_message_parts(message) for message in incoming]
            if (all(role in {"system", "user", "assistant"} and isinstance(content, str)
                    and len(content) <= CHAT_MESSAGE_CHAR_LIMIT for role, content in parts)
                    and sum(len(content) for _, content in parts) <= CHAT_HISTORY_CHAR_LIMIT):
                return data

        selected = [current]
        remaining = CHAT_HISTORY_CHAR_LIMIT - len(question)
        slots = CHAT_HISTORY_MESSAGE_LIMIT - 1
        cursor = current
        while cursor > 0 and slots > 0:
            previous = next((i for i in range(cursor - 1, -1, -1)
                             if _message_parts(incoming[i])[0] == "user"), None)
            if previous is None:
                # A short system preface can stay in the validated request;
                # only the trusted server prompt is sent to the LLM.
                prefix = list(range(cursor))
                parts = [_message_parts(incoming[i]) for i in prefix]
                if (len(prefix) <= slots and
                        all(role in {"system", "assistant"} and isinstance(content, str)
                            and len(content) <= CHAT_MESSAGE_CHAR_LIMIT for role, content in parts) and
                        sum(len(content) for _, content in parts) <= remaining):
                    selected.extend(prefix)
                break

            turn = list(range(previous, cursor))
            parts = [_message_parts(incoming[i]) for i in turn]
            user_role, user_content = parts[0]
            if user_role != "user" or not isinstance(user_content, str) or len(user_content) > CHAT_MESSAGE_CHAR_LIMIT:
                break
            if len(turn) <= slots and all(
                    role in {"user", "assistant", "system"} and isinstance(content, str)
                    and len(content) <= CHAT_MESSAGE_CHAR_LIMIT for role, content in parts
            ) and sum(len(content) for _, content in parts) <= remaining:
                chosen = turn
                cost = sum(len(content) for _, content in parts)
            elif len(user_content) <= remaining:
                # A long old answer is expendable; its user question may carry
                # the explicit incident window needed by a follow-up C.
                chosen = [previous]
                cost = len(user_content)
            else:
                break
            selected.extend(chosen)
            remaining -= cost
            slots -= len(chosen)
            cursor = previous

        reduced = dict(data)
        reduced["messages"] = [incoming[i] for i in sorted(selected)]
        return reduced

    @model_validator(mode="after")
    def validate_history(self):
        latest = next((m for m in reversed(self.messages) if m.role == "user"), None)
        if latest is None or not latest.content.strip():
            raise ValueError("A non-empty user message is required")
        if sum(len(m.content) for m in self.messages) > CHAT_HISTORY_CHAR_LIMIT:
            raise ValueError("L'historique dépasse la limite de sécurité après réduction")
        return self


class DiagnosisRequest(BaseModel):
    question: str = Field(min_length=1, max_length=4000)


class AgentChatRequest(BaseModel):
    """Bound the transport without classifying or routing the user's words."""
    model: str = "kubeai"
    messages: list[ChatMessage] = Field(min_length=1, max_length=CHAT_HISTORY_MESSAGE_LIMIT)
    stream: bool = False
    temperature: float = Field(default=0.2, ge=0, le=2)
    max_tokens: int | None = Field(default=None, ge=1, le=1024)
    max_completion_tokens: int | None = Field(default=None, ge=1, le=1024)

    @model_validator(mode="before")
    @classmethod
    def bound_history(cls, data):
        return ChatRequest.reduce_history(data)

    @model_validator(mode="after")
    def require_question(self):
        latest = next((item for item in reversed(self.messages) if item.role == "user"), None)
        if latest is None or not latest.content.strip():
            raise ValueError("A non-empty user message is required")
        return self
