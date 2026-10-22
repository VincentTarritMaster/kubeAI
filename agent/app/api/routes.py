from fastapi import APIRouter, Request

from app.models import Capabilities, ClusterInventory, ClusterStatus

router = APIRouter()


@router.get("/health", tags=["health"], operation_id="get_agent_health")
async def health():
    """Check that the process responds without querying its dependencies."""
    return {"status": "ok"}


@router.get("/ready", tags=["health"], operation_id="get_agent_readiness", responses={503: {"description": "Kubernetes API unavailable"}})
async def ready(request: Request):
    """Check pod access in the configured namespace. Mimir is optional."""
    await request.app.state.cluster_status.kubernetes.ready()
    return {"status": "ready"}


@router.get("/capabilities", response_model=Capabilities, tags=["health"], operation_id="get_agent_capabilities")
async def capabilities(request: Request):
    """Report configured sources and optional metric families without calling the LLM.

    This route never returns a key or an arbitrary upstream URL. It is intended
    for operators who need to know why a metric is null before asking a question.
    """
    return await request.app.state.cluster_status.capabilities()


@router.get(
    "/cluster/status", response_model=ClusterStatus, tags=["cluster"],
    operation_id="get_cluster_status", responses={503: {"description": "Kubernetes API unavailable"}},
)
async def cluster_status(request: Request):
    """Read pods and recent CPU/memory metrics for the configured namespace.

    This operation is read-only. health describes pod health within the namespace;
    data_status describes data availability. A null metric is missing or does not
    apply; it does not represent zero usage.
    """
    return await request.app.state.cluster_status.status()


@router.get(
    "/cluster/inventory", response_model=ClusterInventory, tags=["cluster"],
    operation_id="get_cluster_inventory",
)
async def cluster_inventory(request: Request):
    """Read bounded node, pod and event inventories across the cluster.

    Each source reports its own availability. Missing data is never presented as
    an empty, healthy inventory.
    """
    return await request.app.state.cluster_status.inventory()
