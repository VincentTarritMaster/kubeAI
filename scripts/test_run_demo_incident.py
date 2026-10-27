"""Exécuter le lanceur Bash avec kubectl simulé, sans cluster ni attente réelle."""

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

SCRIPT = Path(__file__).with_name("run-demo-incident.sh").resolve()
STUB = '''
import json, os, sys
args = sys.argv[1:]
with open(os.environ['DEMO_CALLS'], 'a') as stream:
    stream.write(json.dumps(args) + '\\n')
if os.path.basename(sys.argv[0]) == 'pwsh':
    sys.exit(int(os.environ.get('DEMO_PWSH_EXIT', '0')))
if 'get' in args:
    if os.environ.get('DEMO_GET_ERROR'):
        print('connection refused', file=sys.stderr)
        sys.exit(1)
    print(os.environ.get('DEMO_EXISTING', ''), end='')
elif 'logs' in args:
    print('2026-10-27T10:00:00Z KUBEAI_DEMO_INCIDENT phase=normal')
    if not os.environ.get('DEMO_INCOMPLETE'):
        print('2026-10-27T10:04:00Z KUBEAI_DEMO_INCIDENT phase=complete')
    if os.environ.get('DEMO_LOG_ERROR'):
        sys.exit(1)
'''


class DemoLauncherTests(unittest.TestCase):
    def run_launcher(self, shell="bash", context="minikube", script=SCRIPT, arguments=None,
                     fake_pwsh=False, **options):
        if os.name == "nt":
            shell = (r"C:\Program Files\Git\bin\bash.exe" if shell == "bash"
                     else r"C:\Program Files\Git\usr\bin\sh.exe")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            stub = root / "kubectl"
            stub.write_text(f"#!{sys.executable}\n" + STUB)
            stub.chmod(0o700)
            if fake_pwsh:
                pwsh = root / "pwsh"
                pwsh.write_text(f"#!{sys.executable}\n" + STUB)
                pwsh.chmod(0o700)
            calls_path = root / "calls.jsonl"
            env = dict(os.environ, PATH=str(root) + os.pathsep + os.environ["PATH"],
                       DEMO_CALLS=str(calls_path), LC_ALL="C.UTF-8", LANG="C.UTF-8", **options)
            launch_args = [context] if arguments is None else arguments
            result = subprocess.run([shell, str(script), *launch_args], cwd=directory, env=env,
                                    capture_output=True, text=True,
                                    encoding="utf-8" if os.name == "nt" else "utf-8",
                                    errors="replace", timeout=10)
            calls = [json.loads(line) for line in calls_path.read_text().splitlines()] if calls_path.exists() else []
            return result, calls

    def test_sh_launches_same_scenario_from_another_directory(self):
        result, calls = self.run_launcher(shell="sh", context="custom-context")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Scénario terminé", result.stdout)
        self.assertTrue(all(call[:2] == ["--context", "custom-context"] for call in calls))
        apply = next(call for call in calls if "apply" in call)
        self.assertEqual(apply[-1], str(SCRIPT.parents[1] / "k8s/demo/incident"))
        self.assertFalse(any("delete" in call for call in calls))
        logs = next(call for call in calls if "logs" in call)
        self.assertEqual(next(arg for arg in reversed(logs) if arg.startswith("--request-timeout=")),
                         "--request-timeout=0")

    def test_start_failure_delegates_to_incident_with_selected_context(self):
        for shell, arguments in (("sh", ["custom-context"]),
                                 ("bash", ["--shell", "sh", "--context", "custom-context"])):
            with self.subTest(shell=shell, arguments=arguments):
                result, calls = self.run_launcher(shell=shell, script=SCRIPT.parents[1] / "start-failure.sh",
                                                  arguments=arguments)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertTrue(all(call[:2] == ["--context", "custom-context"] for call in calls))
                self.assertIn("Scénario terminé", result.stdout)

    def test_start_failure_rejects_invalid_options_before_cluster_access(self):
        for arguments in (["--shell"], ["--shell", "invalid"], ["--context"],
                          ["--unknown"], ["first-context", "second-context"]):
            with self.subTest(arguments=arguments):
                result, calls = self.run_launcher(script=SCRIPT.parents[1] / "start-failure.sh",
                                                  arguments=arguments)
                self.assertEqual(result.returncode, 2)
                self.assertFalse(calls)

    def test_start_failure_defaults_to_observed_cluster(self):
        result, calls = self.run_launcher(script=SCRIPT.parents[1] / "start-failure.sh", arguments=[])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(all(call[:2] == ["--context", "kubeai-observed"] for call in calls))

    def test_stop_failure_only_cleans_demo_pods_in_observed_cluster(self):
        result, calls = self.run_launcher(script=SCRIPT.parents[1] / "stop-failure.sh", arguments=[])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(calls), 4)
        for call in calls[:3]:
            self.assertEqual(call[:2], ["--context", "kubeai-observed"])
            self.assertIn("delete", call)
            self.assertIn("pods", call)
            self.assertTrue(any(arg.startswith("--field-selector=metadata.name=kubeai-demo-") for arg in call))
            self.assertTrue(any(arg.startswith("--selector=kubeai-demo=") for arg in call))
            self.assertNotIn("pvc", call)
        self.assertIn("configmaps", calls[-1])
        self.assertIn("--field-selector=metadata.name=kubeai-demo-incident-app", calls[-1])
        self.assertIn("--selector=kubeai-demo=incident", calls[-1])

    def test_start_failure_forwards_powershell_context_and_exit_code(self):
        for exit_code in (0, 7):
            with self.subTest(exit_code=exit_code):
                result, calls = self.run_launcher(
                    script=SCRIPT.parents[1] / "start-failure.sh", fake_pwsh=True,
                    arguments=["--shell", "ps1", "--context", "custom context"],
                    DEMO_PWSH_EXIT=str(exit_code),
                )
                self.assertEqual(result.returncode, exit_code, result.stderr)
                self.assertEqual(calls, [["-NoProfile", "-File", str(SCRIPT.with_suffix(".ps1")),
                                          "-Context", "custom context"]])

    def test_only_finished_demo_is_replaced(self):
        for phase in ("Succeeded", "Failed"):
            with self.subTest(phase=phase):
                result, calls = self.run_launcher(DEMO_EXISTING=phase + "|incident")
                self.assertEqual(result.returncode, 0, result.stderr)
                delete = next(call for call in calls if "delete" in call)
                self.assertIn("--field-selector=metadata.name=kubeai-demo-incident", delete)
                self.assertIn("--selector=kubeai-demo=incident", delete)
                self.assertNotIn("pvc", delete)

    def test_active_or_unexpected_demo_is_preserved(self):
        for phase in ("Running", "Pending", "Unknown"):
            with self.subTest(phase=phase):
                result, calls = self.run_launcher(DEMO_EXISTING=phase + "|incident")
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(len(calls), 1)

    def test_unrelated_pod_is_preserved(self):
        result, calls = self.run_launcher(DEMO_EXISTING="Succeeded|other")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(len(calls), 1)

    def test_cluster_error_is_not_treated_as_absent_pod(self):
        result, calls = self.run_launcher(DEMO_GET_ERROR="1")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(len(calls), 1)

    def test_incomplete_or_failed_log_stream_does_not_report_success(self):
        for options in ({"DEMO_INCOMPLETE": "1"}, {"DEMO_LOG_ERROR": "1"}):
            with self.subTest(options=options):
                result, _ = self.run_launcher(**options)
                self.assertNotEqual(result.returncode, 0)
                self.assertNotIn("Scénario terminé", result.stdout)


if __name__ == "__main__":
    unittest.main()
