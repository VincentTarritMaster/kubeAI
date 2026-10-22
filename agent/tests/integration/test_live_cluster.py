"""Tests explicites sur un vrai cluster ; aucun lancement sans contexte fourni."""

import json
import os
import subprocess
import time
import unittest
import uuid

CONTEXT = os.getenv("KUBEAI_LIVE_TEST_CONTEXT")


@unittest.skipUnless(CONTEXT, "Définir KUBEAI_LIVE_TEST_CONTEXT pour tester un vrai cluster")
class LiveClusterTests(unittest.TestCase):
    def kubectl(self, *args, payload=None):
        result = subprocess.run(
            ["kubectl", "--context", CONTEXT, "--request-timeout=15s", *args],
            input=json.dumps(payload) if payload else None,
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode:
            raise RuntimeError(result.stderr.strip())
        return result.stdout

    def api(self, path):
        return json.loads(self.kubectl("get", "--raw", (
            "/api/v1/namespaces/kubeai/services/http:kubeai-agent:8000/proxy/" + path
        )))

    def test_01_live_pods_and_metrics(self):
        self.assertEqual(self.api("health")["status"], "ok")
        self.assertEqual(self.api("ready")["status"], "ready")
        deadline = time.monotonic() + 120
        while True:
            result = self.api("cluster/status")
            if result["data_status"] == "complete" or time.monotonic() >= deadline:
                break
            time.sleep(5)
        actual = json.loads(self.kubectl("get", "pods", "-n", "kubeai", "-o", "json"))["items"]
        self.assertEqual({p["uid"] for p in result["pods"]}, {p["metadata"]["uid"] for p in actual})
        # La pile par défaut ne scrape pas cAdvisor : data_status peut être
        # partial et les métriques conteneur absentes. On l'accepte, mais on
        # vérifie les valeurs dès qu'elles sont annoncées disponibles.
        self.assertIn(result["data_status"], {"complete", "partial"}, result["warnings"])
        self.assertIn(result["sources"]["mimir"]["status"], {"ok", "partial", "unavailable"})
        for item in result["pods"]:
            if item["health"] != "healthy":
                continue
            metrics = item["metrics"]
            if metrics["status"] == "available":
                self.assertGreaterEqual(metrics["cpu_cores"], 0)
                self.assertGreater(metrics["memory_working_set_bytes"], 0)
            else:
                self.assertIsNone(metrics["cpu_cores"])
                self.assertIsNone(metrics["memory_working_set_bytes"])
        print(json.dumps({"health": result["health"], "summary": result["summary"],
                          "pods": [{"name": p["name"], "metrics": p["metrics"]} for p in result["pods"]]}, indent=2))

    def test_02_failed_pod_is_detected(self):
        name = "agent-failure-check-" + uuid.uuid4().hex[:8]
        manifest = {
            "apiVersion": "v1", "kind": "Pod",
            "metadata": {"name": name, "namespace": "kubeai", "labels": {"app": "kubeai-agent-test"}},
            "spec": {
                "restartPolicy": "Never", "automountServiceAccountToken": False,
                "securityContext": {"runAsNonRoot": True, "runAsUser": 65534,
                                    "seccompProfile": {"type": "RuntimeDefault"}},
                "containers": [{
                    "name": "failure", "image": "busybox:1.37.0",
                    "command": ["sh", "-c", "echo simulated-agent-test-failure >&2; exit 1"],
                    "resources": {"requests": {"cpu": "5m", "memory": "8Mi"},
                                  "limits": {"cpu": "50m", "memory": "32Mi"}},
                    "securityContext": {"allowPrivilegeEscalation": False, "readOnlyRootFilesystem": True,
                                        "capabilities": {"drop": ["ALL"]}},
                }],
            },
        }
        self.kubectl("create", "-f", "-", payload=manifest)
        self.addCleanup(self.kubectl, "delete", "pod", name, "-n", "kubeai", "--wait=false")
        deadline = time.monotonic() + 60
        while True:
            result = self.api("cluster/status")
            observed = next((p for p in result["pods"] if p["name"] == name), None)
            if observed and observed["phase"] == "Failed":
                break
            if time.monotonic() >= deadline:
                self.fail("Le pod de test n'a pas atteint Failed")
            time.sleep(2)
        self.assertEqual(result["health"], "degraded")
        self.assertEqual(observed["health"], "unhealthy")
        self.assertIn("failure: Error", observed["issues"])
        self.assertIsNone(observed["metrics"]["cpu_cores"])
        print("Pod Failed détecté ; nettoyage : " + name)


if __name__ == "__main__":
    unittest.main(verbosity=2)
