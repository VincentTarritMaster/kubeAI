"""Bearer authentication for the OpenAI-compatible API surface."""
import hmac

from fastapi import HTTPException, Request


async def require_api_key(request: Request) -> None:
    settings = getattr(request.app.state, "settings", None)
    expected = settings.api_key.strip() if settings else ""
    if not expected:
        raise HTTPException(status_code=503, detail="Authentification API non configurée")

    authorization = request.headers.get("authorization", "")
    scheme, separator, supplied = authorization.partition(" ")
    if (not separator or scheme.lower() != "bearer"
            or not hmac.compare_digest(supplied.strip(), expected)):
        raise HTTPException(status_code=401, detail="Authentification requise",
                            headers={"WWW-Authenticate": "Bearer"})
