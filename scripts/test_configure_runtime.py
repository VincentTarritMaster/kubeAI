"""Vérifications du transfert .env vers la configuration Kubernetes."""

import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location("runtime", Path(__file__).with_name("configure-runtime.py"))
runtime = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runtime)


class RuntimeTests(unittest.TestCase):
    def test_local_key_and_loopback_are_translated_for_pods(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "api-key").write_text("private-key\n")
            values = {
                "LLM_PROVIDER": "local", "LLM_BASE_URL": "http://127.0.0.1:11435/v1",
                "LLM_MODEL": "apertus-8b", "LLM_API_KEY_FILE": "api-key",
            }
            config, name, key = runtime.configuration(values, "minikube", root)
            self.assertEqual(config["LLM_BASE_URL"], "http://host.docker.internal:11435/v1")
            self.assertEqual(config["LLM_API_KEY_FILE"], "/var/run/secrets/kubeai-llm/api-key")
            self.assertEqual((name, key), ("kubeai-llm", "private-key"))
            self.assertEqual(values["LLM_BASE_URL"], "http://127.0.0.1:11435/v1")
            self.assertNotIn("private-key", json.dumps(config))

    def test_remote_local_endpoint_without_key_is_preserved(self):
        config, _, key = runtime.configuration({
            "LLM_PROVIDER": "local", "LLM_BASE_URL": "http://llm.internal:8080/v1", "LLM_MODEL": "model",
        }, "other")
        self.assertEqual(config["LLM_BASE_URL"], "http://llm.internal:8080/v1")
        self.assertEqual(key, "")
        self.assertEqual(config["LLM_API_KEY_FILE"], "")

    def test_missing_local_key_has_actionable_error(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError, "local-llm.py setup"):
                runtime.configuration({
                    "LLM_PROVIDER": "local", "LLM_BASE_URL": "http://localhost:11435/v1",
                    "LLM_MODEL": "apertus-8b", "LLM_API_KEY_FILE": "absent",
                }, "minikube", Path(directory))

    def test_mistral_key_is_excluded_from_configmap(self):
        config, name, key = runtime.configuration({
            "LLM_PROVIDER": "mistral-api", "MISTRAL_API_KEY": "private-key",
        }, "minikube")
        self.assertEqual((name, key), ("kubeai-mistral", "private-key"))
        self.assertNotIn("private-key", json.dumps(config))

    def test_placeholder_mistral_key_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "Clé Mistral manquante"):
            runtime.configuration({"MISTRAL_API_KEY": "REMPLACER_PAR_SA_CLE"}, "minikube")

    def test_dotenv_values_are_read_without_shell_evaluation(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / ".env"
            path.write_text('# commentaire\nLLM_MODEL="model"\nMISTRAL_API_KEY=$(some-command)\nLLM_PROVIDER=local # commentaire\n')
            values = runtime.read_env(path)
            self.assertEqual(values["MISTRAL_API_KEY"], "$(some-command)")
            self.assertEqual(values["LLM_PROVIDER"], "local")
            self.assertEqual(values["LLM_MODEL"], "model")

    def test_existing_api_auth_is_preserved_and_key_only_passes_on_stdin(self):
        with patch.object(runtime.subprocess, "run") as run:
            run.return_value = subprocess.CompletedProcess([], 0, "secret/kubeai-api-auth\n")
            runtime.configure("minikube", {"LLM_PROVIDER": "local"}, "kubeai-llm", "private-key")
            self.assertEqual(run.call_count, 3)
            for call in run.call_args_list:
                self.assertNotIn("private-key", " ".join(call.args[0]))
            manifest = json.loads(run.call_args_list[1].kwargs["input"])
            self.assertEqual(manifest["metadata"]["name"], "kubeai-llm")
            self.assertEqual(manifest["stringData"]["api-key"], "private-key")

    def test_missing_auth_is_created_without_rotating_llm_key(self):
        with patch.object(runtime.subprocess, "run") as run:
            run.return_value = subprocess.CompletedProcess([], 0, "")
            runtime.configure("minikube", {"LLM_PROVIDER": "local"}, "kubeai-llm", "")
            manifest = json.loads(run.call_args_list[1].kwargs["input"])
            self.assertEqual(manifest["metadata"]["name"], "kubeai-api-auth")
            self.assertTrue(manifest["stringData"]["api-key"])
            self.assertEqual(run.call_count, 3)


if __name__ == "__main__":
    unittest.main()
