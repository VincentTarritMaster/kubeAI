"""Real loopback HTTP tests for A/B/C, with synthetic upstream fixtures only.

This test-only harness starts the production FastAPI application under Uvicorn
and replaces dependency transports in-process. It adds no simulation route and
must never be deployed as an application mode.
"""
import asyncio
from contextlib import contextmanager
import json
from pathlib import Path
import socket
import sys
from threading import Thread
import time

import httpx
import pytest
import uvicorn

# Reuse the existing synthetic fixtures and request builders.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import test_correlation as demo_c
import test_demo_cluster_health as demo_a
import test_incident_timeline as demo_b

TEST_API_KEY = "unit-api-key-0123456789abcdef0123456789"


@contextmanager
def local_server(app):
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("127.0.0.1", 0))
    listener.listen(128)
    port = listener.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port,
                                          log_level="error", access_log=False))
    thread = Thread(target=lambda: asyncio.run(server.serve(sockets=[listener])), daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started and thread.is_alive() and time.monotonic() < deadline:
        time.sleep(0.02)
    if not server.started:
        server.should_exit = True
        thread.join(timeout=5)
        listener.close()
        raise RuntimeError("Uvicorn failed to start on the loopback listener")
    try:
        with httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=10, trust_env=False,
                          headers={"Authorization": "Bearer " + TEST_API_KEY}) as client:
            yield client
    finally:
        server.should_exit = True
        thread.join(timeout=10)
        listener.close()
        if thread.is_alive():
            raise RuntimeError("Uvicorn did not stop cleanly")


def scenario(case):
    if case == "A":
        test_client, calls = demo_a.make_client(key="")
        request = lambda client, stream: demo_a.chat(client, stream=stream)
    elif case == "B":
        test_client, calls = demo_b.client(key="")
        request = lambda client, stream: demo_b.ask(client, question=demo_b.QUESTION, stream=stream)
    else:
        test_client, calls = demo_c.make_client(key="")
        request = lambda client, stream: demo_c.ask(client, question=demo_c.QUESTION, stream=stream)
    return test_client.app, calls, request


@pytest.mark.parametrize("case", ["A", "B", "C"])
@pytest.mark.parametrize("stream", [False, True], ids=["json", "sse"])
def test_demo_over_real_local_http(case, stream):
    app, calls, make_request = scenario(case)
    with local_server(app) as client:
        response = make_request(client, stream)
    assert response.status_code == 200
    if stream:
        assert "text/event-stream" in response.headers["content-type"]
        frames = [line[6:] for line in response.text.splitlines() if line.startswith("data: ")]
        assert frames[-1] == "[DONE]"
        chunks = [json.loads(frame) for frame in frames[:-1]]
        assert chunks and all(item["model"] == "kubeai" for item in chunks)
        text = "".join(item["choices"][0]["delta"].get("content", "") for item in chunks)
    else:
        assert response.headers["content-type"].startswith("application/json")
        body = response.json()
        assert body["model"] == "kubeai"
        text = body["choices"][0]["message"]["content"]
    assert text
    assert calls["kubernetes"]
    assert calls["llm"] == []  # No Mistral key and no provider request.
