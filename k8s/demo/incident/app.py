"""Small, dependency-free HTTP and Prometheus demo with a timed CPU incident."""

from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import threading
import time


BASELINE_SECONDS = 90
ANOMALY_SECONDS = 90
RECOVERY_SECONDS = 60
_stop_load = threading.Event()


def log_phase(phase: str) -> None:
    stamp = datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
    print(f"{stamp} KUBEAI_DEMO_INCIDENT phase={phase}", flush=True)


def cpu_load() -> None:
    while not _stop_load.is_set():
        # Keep one Python thread busy. The pod CPU limit bounds the workload.
        for _ in range(100_000):
            if _stop_load.is_set():
                return


def render_metrics() -> str:
    return (
        "# HELP process_cpu_seconds_total Total user and system CPU time spent in seconds.\n"
        "# TYPE process_cpu_seconds_total counter\n"
        f"process_cpu_seconds_total {time.process_time():.6f}\n"
    )


def run_scenario(server: ThreadingHTTPServer) -> None:
    log_phase("normal")
    time.sleep(BASELINE_SECONDS)
    log_phase("anomaly_start")
    worker = threading.Thread(target=cpu_load, name="synthetic-cpu-load", daemon=True)
    worker.start()
    time.sleep(ANOMALY_SECONDS)
    _stop_load.set()
    worker.join(timeout=2)
    log_phase("recovered")
    time.sleep(RECOVERY_SECONDS)
    log_phase("complete")
    server.shutdown()


class Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        if self.path == "/healthz":
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"ok\n")
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
    try:
        server.serve_forever(poll_interval=0.5)
    finally:
        _stop_load.set()
        server.server_close()


if __name__ == "__main__":
    main()
