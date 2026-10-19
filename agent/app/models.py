from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, model_validator


class SourceStatus(BaseModel):
    status: Literal["ok", "partial", "unavailable"]
    message: str | None = None


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


class ChatRequest(BaseModel):
    model: str = "kubeai"
    messages: list[ChatMessage] = Field(min_length=1, max_length=32)
    stream: bool = False
    temperature: float = Field(default=0.2, ge=0, le=2)
    max_tokens: int | None = Field(default=None, ge=1, le=1024)
    max_completion_tokens: int | None = Field(default=None, ge=1, le=1024)

    @model_validator(mode="after")
    def validate_history(self):
        if not any(m.role == "user" and m.content.strip() for m in self.messages):
            raise ValueError("A non-empty user message is required")
        if sum(len(m.content) for m in self.messages) > 16000:
            raise ValueError("History exceeds 16,000 characters; start a new conversation")
        return self


class DiagnosisRequest(BaseModel):
    question: str = Field(min_length=1, max_length=4000)
