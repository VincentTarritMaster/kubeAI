from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.responses import JSONResponse
import httpx

from app.api.routes import router
from app.api.chat import router as chat_router
from app.clients.base import UpstreamError
from app.clients.kubernetes_client import KubernetesClient
from app.clients.kubernetes_connection import build_kubernetes_http_options
from app.clients.prometheus_client import PrometheusClient
from app.clients.loki_client import LokiClient
from app.config import Settings
from app.services.cluster_status import ClusterStatusService
from app.services.diagnosis import DiagnosisService
from app.services.llm_service import LLMService


def create_app(settings: Settings | None = None, *, kubernetes_transport=None, prometheus_transport=None,
               loki_transport=None, llm_transport=None):
    @asynccontextmanager
    async def lifespan(application):
        config = settings or Settings.from_env()
        application.state.settings = config
        kubernetes_options = build_kubernetes_http_options(config)
        async with (
            httpx.AsyncClient(**kubernetes_options, timeout=config.request_timeout, trust_env=False,
                              transport=kubernetes_transport) as kubernetes_http,
            httpx.AsyncClient(base_url=config.prometheus_url, timeout=config.request_timeout,
                              trust_env=False, transport=prometheus_transport) as prometheus_http,
            httpx.AsyncClient(base_url=config.loki_url, timeout=config.request_timeout,
                              trust_env=False, transport=loki_transport) as loki_http,
            httpx.AsyncClient(base_url=config.llm_base_url.rstrip("/") + "/", timeout=config.llm_timeout,
                              trust_env=False, transport=llm_transport) as llm_http,
        ):
            application.state.cluster_status = ClusterStatusService(
                KubernetesClient(kubernetes_http, config), PrometheusClient(prometheus_http, config),
            )
            application.state.diagnosis = DiagnosisService(
                application.state.cluster_status, LokiClient(loki_http, config), LLMService(llm_http, config),
            )
            yield

    application = FastAPI(
        title="kubeAI Agent", version="0.3.0",
        description="Namespace metrics and bounded cluster-wide Kubernetes inventory. Read-only API.",
        lifespan=lifespan,
    )

    @application.exception_handler(UpstreamError)
    async def upstream_error(request: Request, error: UpstreamError):
        return JSONResponse(status_code=503, content={"detail": {
            "source": error.source, "message": error.message,
        }})

    @application.exception_handler(RequestValidationError)
    async def validation_error(request: Request, error: RequestValidationError):
        for item in error.errors():
            if item.get("type") == "current_question_too_long":
                return JSONResponse(status_code=413, content={"detail": item["msg"]})
        return await request_validation_exception_handler(request, error)

    application.include_router(router)
    application.include_router(chat_router)
    return application


app = create_app()
