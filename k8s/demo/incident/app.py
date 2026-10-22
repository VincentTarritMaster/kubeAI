"""Small, dependency-free HTTP and Prometheus demo with a timed CPU/HTTP incident."""

from datetime import datetime, timezone
import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import socket
import threading
import time


BASELINE_SECONDS = 90
ANOMALY_SECONDS = 90
RECOVERY_SECONDS = 60
_DURATION_BUCKETS = (0.05, 0.1, 0.2, 0.5, 1.0, 2.0, 5.0)
_stop_load = threading.Event()
_stop_traffic = threading.Event()
_failure = threading.Event()
_http_lock = threading.Lock()
_http_status_counts = {}
_http_bucket_counts = {bound: 0 for bound in _DURATION_BUCKETS}
_http_duration_sum = 0.0
_http_duration_count = 0


def log_phase(phase: str) -> None:
    stamp = datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
    print(f"{stamp} KUBEAI_DEMO_INCIDENT phase={phase}", flush=True)


def record_http(status: int, duration: float) -> None:
    global _http_duration_sum, _http_duration_count
    with _http_lock:
        _http_status_counts[str(status)] = _http_status_counts.get(str(status), 0) + 1
        _http_duration_sum += duration
        _http_duration_count += 1
        for bound in _DURATION_BUCKETS:
            if duration <= bound:
                _http_bucket_counts[bound] += 1


def log_dependency_failure() -> None:
    """Emit a real connection-refused log against a just-closed local port."""
    detail = "connection refused"
    try:
        probe = socket.socket()
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
        probe.close()
        socket.create_connection(("127.0.0.1", port), timeout=0.2).close()
    except OSError as error:
        detail = f"connection refused: {error}"
    stamp = datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
    print(f"{stamp} KUBEAI_DEMO_DEPENDENCY {detail}", flush=True)


def cpu_load() -> None:
    while not _stop_load.is_set():
        # Keep one Python thread busy. The pod CPU limit bounds the workload.
        for _ in range(100_000):
            if _stop_load.is_set():
                return


def traffic() -> None:
    """Generate real requests so the HTTP counters are not always zero."""
    while not _stop_traffic.is_set():
        try:
            connection = http.client.HTTPConnection("127.0.0.1", 8080, timeout=2)
            connection.request("GET", "/work")
            response = connection.getresponse()
            response.read()
            connection.close()
        except OSError:
            pass
        _stop_traffic.wait(0.5)


def render_metrics() -> str:
    with _http_lock:
        statuses = dict(_http_status_counts)
        buckets = dict(_http_bucket_counts)
        total = _http_duration_count
        duration_sum = _http_duration_sum
    lines = [
        "# HELP http_requests_total Total HTTP requests by status.",
        "# TYPE http_requests_total counter",
    ]
    for status in ("200", "500"):
        lines.append(f'http_requests_total{{status="{status}"}} {statuses.get(status, 0)}')
    lines += [
        "# HELP http_request_duration_seconds HTTP request duration in seconds.",
        "# TYPE http_request_duration_seconds histogram",
    ]
    for bound in sorted(buckets):
        lines.append(f'http_request_duration_seconds_bucket{{le="{bound}"}} {buckets[bound]}')
    lines += [
        f'http_request_duration_seconds_bucket{{le="+Inf"}} {total}',
        f"http_request_duration_seconds_sum {duration_sum:.6f}",
        f"http_request_duration_seconds_count {total}",
        "# HELP process_cpu_seconds_total Total user and system CPU time spent in seconds.",
        "# TYPE process_cpu_seconds_total counter",
        f"process_cpu_seconds_total {time.process_time():.6f}",
    ]
    return "\n".join(lines) + "\n"


def run_scenario(server: ThreadingHTTPServer) -> None:
    log_phase("normal")
    time.sleep(BASELINE_SECONDS)
    log_phase("anomaly_start")
    _failure.set()
    log_dependency_failure()
    worker = threading.Thread(target=cpu_load, name="synthetic-cpu-load", daemon=True)
    worker.start()
    time.sleep(ANOMALY_SECONDS)
    _stop_load.set()
    worker.join(timeout=2)
    _failure.clear()
    log_phase("recovered")
    time.sleep(RECOVERY_SECONDS)
    log_phase("complete")
    _stop_traffic.set()
    server.shutdown()


class Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        if self.path == "/healthz":
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"ok\n")
            return
        if self.path == "/work":
            started = time.perf_counter()
            failed = _failure.is_set()
            time.sleep(0.12 if failed else 0.02)
            duration = time.perf_counter() - started
            status = 500 if failed else 200
            record_http(status, duration)
            body = b"dependency unavailable\n" if failed else b"ok\n"
            self.send_response(status)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if self.path != "/metrics":
            self.send_error(404)
            return
        body = render_metrics().encode("ascii")
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; version=0.0.4; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, _format: str, *_args: object) -> None:
        # Keep stdout limited to the synthetic, timestamped scenario markers.
        return


def main() -> None:
    server = ThreadingHTTPServer(("0.0.0.0", 8080), Handler)
    server.daemon_threads = True
    threading.Thread(target=run_scenario, args=(server,), name="demo-scenario", daemon=True).start()
    threading.Thread(target=traffic, name="synthetic-traffic", daemon=True).start()
    try:
        server.serve_forever(poll_interval=0.5)
    finally:
        _stop_load.set()
        _stop_traffic.set()
        server.server_close()


if __name__ == "__main__":
    main()
