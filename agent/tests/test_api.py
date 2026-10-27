import copy
import tempfile
import time
import unittest
from pathlib import Path

from fastapi.testclient import TestClient
import httpx

from app.config import Settings
from app.main import create_app


def pod(name="web", uid="abc-123", *, phase="Running", ready=True):
    return {
        "metadata": {"name": name, "namespace": "kubeai", "uid": uid},
        "spec": {"nodeName": "minikube"},
        "status": {
            "phase": phase,
            "conditions": [{"type": "Ready", "status": str(ready)}],
            "containerStatuses": [{
                "name": "app", "containerID": "containerd://current-container",
                "ready": ready, "restartCount": 0, "state": {"running": {}},
            }],
        },
    }


def sample(value, *, uid="abc-123", container_id="current-container", container="app", pod_name="web"):
    return {"metric": {
        "namespace": "kubeai", "pod": pod_name, "container": container,
        "id": f"/kubepods/pod{uid}/{container_id}",
    }, "value": [time.time(), str(value)]}


class AgentAPITests(unittest.TestCase):
    def client(self, pods=None, *, kubernetes=None, prometheus=None, settings=None):
        def kube(request):
            self.assertEqual(request.method, "GET")
            self.assertEqual(request.url.path, "/api/v1/pods")
            return httpx.Response(200, json={"metadata": {}, "items": pods if pods is not None else [pod()]})

        def prom(request):
            self.assertEqual(request.method, "GET")
            self.assertEqual(request.url.path, "/prometheus/api/v1/query")
            self.assertEqual(request.url.params["timeout"], "5000ms")
            query = request.url.params["query"]
            self.assertIn('job="kubernetes-cadvisor"', query)
            self.assertIn("timestamp(", query)
            self.assertIn("< 60", query)
            value = 0.025 if "rate(" in query else 134217728
            return httpx.Response(200, json={
                "status": "success", "data": {"resultType": "vector", "result": [sample(value)]},
            })

        app = create_app(
            settings or Settings(ca_file="", token_file=""),
            kubernetes_transport=httpx.MockTransport(kubernetes or kube),
            prometheus_transport=httpx.MockTransport(prometheus or prom),
        )
        return TestClient(app)

    def test_real_response_contract_and_openapi(self):
        with self.client() as client:
            result = client.get("/cluster/status")
            self.assertEqual(result.status_code, 200)
            body = result.json()
            self.assertEqual(body["scope"], "cluster")
            self.assertEqual(body["namespace"], "*")
            self.assertEqual(body["health"], "healthy")
            self.assertEqual(body["data_status"], "complete")
            self.assertEqual(body["sources"]["mimir"]["status"], "ok")
            self.assertNotIn("prometheus", body["sources"])
            self.assertEqual(body["pods"][0]["metrics"]["cpu_cores"], 0.025)
            self.assertEqual(body["pods"][0]["metrics"]["memory_working_set_bytes"], 134217728)
            self.assertEqual(client.get("/health").status_code, 200)
            self.assertEqual(client.get("/ready").status_code, 200)
            schema = client.get("/openapi.json").json()
            self.assertEqual(schema["paths"]["/cluster/status"]["get"]["operationId"], "get_cluster_status")

    def test_mimir_failure_preserves_pods_and_readiness(self):
        with self.client(prometheus=lambda request: httpx.Response(503)) as client:
            body = client.get("/cluster/status").json()
            self.assertEqual(body["health"], "healthy")
            self.assertEqual(body["data_status"], "partial")
            self.assertEqual(body["sources"]["mimir"]["status"], "unavailable")
            self.assertEqual(body["sources"]["mimir"]["message"], "mimir returned HTTP 503")
            self.assertIsNone(body["pods"][0]["metrics"]["cpu_cores"])
            self.assertIsNone(body["pods"][0]["metrics"]["memory_working_set_bytes"])
            self.assertEqual(client.get("/ready").status_code, 200)

    def test_kubernetes_forbidden_is_explicit_and_never_empty_success(self):
        with self.client(kubernetes=lambda request: httpx.Response(403)) as client:
            for path in ("/cluster/status", "/ready"):
                result = client.get(path)
                self.assertEqual(result.status_code, 503)
                self.assertEqual(result.json()["detail"]["source"], "kubernetes")
            self.assertEqual(client.get("/health").status_code, 200)

    def test_missing_metrics_are_null_not_zero(self):
        def empty(request):
            return httpx.Response(200, json={"status": "success", "data": {"resultType": "vector", "result": []}})
        with self.client(prometheus=empty) as client:
            body = client.get("/cluster/status").json()
            self.assertEqual(body["data_status"], "partial")
            self.assertEqual(body["pods"][0]["metrics"]["status"], "unavailable")
            self.assertIsNone(body["pods"][0]["metrics"]["cpu_cores"])

    def test_metrics_do_not_include_previous_pod_or_restarted_container(self):
        def with_old_samples(request):
            return httpx.Response(200, json={"status": "success", "data": {
                "resultType": "vector", "result": [sample(1), sample(999, uid="old-uid"),
                                                        sample(999, container_id="old-container")],
            }})
        with self.client(prometheus=with_old_samples) as client:
            metrics = client.get("/cluster/status").json()["pods"][0]["metrics"]
            self.assertEqual(metrics["cpu_cores"], 1)
            self.assertEqual(metrics["memory_working_set_bytes"], 1)

    def test_incomplete_multicontainer_metrics_are_not_reported_as_total(self):
        item = pod()
        second = copy.deepcopy(item["status"]["containerStatuses"][0])
        second["name"] = "sidecar"
        item["status"]["containerStatuses"].append(second)
        with self.client([item]) as client:
            body = client.get("/cluster/status").json()
            self.assertEqual(body["data_status"], "partial")
            self.assertIsNone(body["pods"][0]["metrics"]["memory_working_set_bytes"])

    def test_crashloop_and_oom_evidence_are_exposed(self):
        item = pod(ready=False)
        container = item["status"]["containerStatuses"][0]
        container.update({
            "restartCount": 3, "state": {"waiting": {"reason": "CrashLoopBackOff"}},
            "lastState": {"terminated": {"reason": "OOMKilled", "exitCode": 137}},
        })
        with self.client([item]) as client:
            body = client.get("/cluster/status").json()
            self.assertEqual(body["health"], "degraded")
            self.assertEqual(body["summary"]["restarts"], 3)
            self.assertIn("app: CrashLoopBackOff", body["pods"][0]["issues"])
            self.assertEqual(body["pods"][0]["containers"][0]["last_termination_reason"], "OOMKilled")

    def test_completed_job_is_not_reported_as_unhealthy(self):
        item = pod(phase="Succeeded", ready=False)
        item["status"]["containerStatuses"][0]["state"] = {"terminated": {"reason": "Completed"}}
        with self.client([item]) as client:
            body = client.get("/cluster/status").json()
            self.assertEqual(body["health"], "healthy")
            self.assertEqual(body["summary"]["completed"], 1)
            self.assertEqual(body["summary"]["unhealthy"], 0)
            self.assertEqual(body["pods"][0]["metrics"]["status"], "not_applicable")

    def test_empty_namespace_is_explicit(self):
        with self.client([]) as client:
            body = client.get("/cluster/status").json()
            self.assertEqual(body["health"], "empty")
            self.assertEqual(body["summary"]["total"], 0)

    def test_pagination_does_not_drop_pods(self):
        def pages(request):
            second = request.url.params["continue"] == "next-page"
            return httpx.Response(200, json={
                "metadata": {"continue": "" if second else "next-page"},
                "items": [pod("second" if second else "first")],
            })
        with self.client(kubernetes=pages) as client:
            body = client.get("/cluster/status").json()
            self.assertEqual([item["name"] for item in body["pods"]], ["first", "second"])

    def test_projected_token_is_reread_after_rotation(self):
        seen = []
        def authorized(request):
            seen.append(request.headers.get("authorization"))
            return httpx.Response(200, json={"items": []})
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "token"
            path.write_text("first")
            with self.client(kubernetes=authorized, settings=Settings(ca_file="", token_file=str(path))) as client:
                client.get("/ready")
                path.write_text("second")
                client.get("/ready")
        self.assertEqual(seen, ["Bearer first", "Bearer second"])

    def test_mimir_warnings_and_invalid_samples_are_visible(self):
        def warned(request):
            return httpx.Response(200, json={"status": "success", "warnings": ["partial scrape"], "data": {
                "resultType": "vector", "result": [sample("NaN")],
            }})
        with self.client(prometheus=warned) as client:
            body = client.get("/cluster/status").json()
            self.assertEqual(body["sources"]["mimir"]["status"], "partial")
            self.assertIn("partial scrape", body["warnings"])
            self.assertIsNone(body["pods"][0]["metrics"]["cpu_cores"])


if __name__ == "__main__":
    unittest.main()
