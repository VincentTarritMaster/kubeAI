"""Fail closed if a unit test accidentally attempts an external HTTP request."""

import httpx
import pytest


@pytest.fixture(autouse=True)
def no_external_http(request, monkeypatch):
    if "integration" in request.node.path.parts:
        return

    def blocked(*_args, **_kwargs):
        raise AssertionError("External HTTP is forbidden in unit tests; use MockTransport")

    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", blocked)
    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", blocked)
