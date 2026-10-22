"""Tester le vrai moteur Apertus par l'API de l'agent, sans accès au navigateur."""
import json
import os
import subprocess
import time
import unittest
import uuid
from pathlib import Path

CONTEXT = os.getenv("KUBEAI_LIVE_TEST_CONTEXT")


@unittest.skipUnless(CONTEXT, "Définir KUBEAI_LIVE_TEST_CONTEXT pour utiliser le modèle réel")
class LiveChatTests(unittest.TestCase):
    def kubectl(self, *args, manifest=None):
        result = subprocess.run(["kubectl", "--context", CONTEXT, "--request-timeout=15s", *args],
                                input=json.dumps(manifest) if manifest else None,
                                capture_output=True, text=True, timeout=45)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout

    def remote(self, source):
        result = subprocess.run([
            "kubectl", "--context", CONTEXT, "exec", "-n", "kubeai", "deployment/kubeai-agent",
            "--", "python", "-c", source,
        ], capture_output=True, text=True, timeout=240)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def test_01_diagnosis_with_real_observations(self):
        result = self.remote('''
import httpx,json
import os
with httpx.Client(base_url="http://127.0.0.1:8000",timeout=180) as client:
    ready=client.get("/llm/status",headers={"Authorization":"Bearer "+os.environ["KUBEAI_API_KEY"]}); ready.raise_for_status()
    response=client.post("/diagnose",headers={"Authorization":"Bearer "+os.environ["KUBEAI_API_KEY"]},json={"question":"Pour chaque pod, cite exactement son nom et la valeur memory_working_set_mib fournie en Mio, sans conversion. Une ligne par pod."})
    response.raise_for_status()
    print(json.dumps(response.json()))
''')
        self.assertIn(result["model"], {"kubeai", "kubeai-apertus"})
        self.assertEqual(result["observation"]["namespace"], "kubeai")
        self.assertIn("Sources disponibles", result["answer"])
        for pod in result["observation"]["pods"]:
            self.assertIn(pod["name"], result["answer"])
        self.assertGreater(result["usage"]["completion_tokens"], 0)
        print(result["answer"])

    def test_02_streaming_from_real_apertus(self):
        result = self.remote('''
import httpx,json
import os
body={"model":"kubeai-apertus","messages":[{"role":"user","content":"Les pods sont-ils prêts ? Réponds en deux phrases."}],"max_tokens":160,"stream":True}
with httpx.Client(timeout=180) as client:
    with client.stream("POST","http://127.0.0.1:8000/v1/chat/completions",headers={"Authorization":"Bearer "+os.environ["KUBEAI_API_KEY"]},json=body) as response:
        response.raise_for_status()
        events=[line[6:] for line in response.iter_lines() if line.startswith("data: ")]
        chunks=[json.loads(line) for line in events if line!="[DONE]"]
        text="".join((c.get("choices",[{}])[0].get("delta",{}).get("content") or "") for c in chunks if c.get("choices"))
        print(json.dumps({"done":events[-1]=="[DONE]","models":list({c.get("model") for c in chunks}),"errors":[c["error"] for c in chunks if "error" in c],"text":text,"chunks":len(chunks)}))
''')
        self.assertTrue(result["done"])
        self.assertEqual(len(result["models"]), 1)
        self.assertIn(result["models"][0], {"kubeai", "kubeai-apertus"})
        self.assertEqual(result["errors"], [])
        self.assertGreater(result["chunks"], 2)
        self.assertIn("Sources disponibles", result["text"])
        print("Streaming réel validé : %s événements" % result["chunks"])

    def test_03_same_conversation_refreshes_after_failed_pod_is_deleted(self):
        path = Path(__file__).resolve().parents[3] / "k8s/demo/failure-pod.yaml"
        manifest = json.loads(self.kubectl("create", "--dry-run=client", "-f", str(path), "-o", "json"))
        name = "conversation-failure-" + uuid.uuid4().hex[:8]
        manifest["metadata"]["name"] = name
        manifest["metadata"]["labels"] = {"app": "kubeai-conversation-test"}
        self.kubectl("create", "-f", "-", manifest=manifest)
        self.addCleanup(self.kubectl, "delete", "pod", name, "-n", "kubeai", "--ignore-not-found", "--wait=false")
        self.kubectl("wait", "pod/" + name, "-n", "kubeai", "--for=jsonpath={.status.phase}=Failed", "--timeout=30s")
        question = f"Check the current state of pod {name}. Is it failing? Answer in three sentences using the latest data."
        first = self.remote(f'''
import httpx,json
import os
r=httpx.post("http://127.0.0.1:8000/diagnose",headers={{"Authorization":"Bearer "+os.environ["KUBEAI_API_KEY"]}},json={{"question":{question!r}}},timeout=180)
r.raise_for_status(); print(json.dumps(r.json()))
''')
        observed = next(p for p in first["observation"]["pods"] if p["name"] == name)
        self.assertEqual(observed["phase"], "Failed")
        self.assertIn(name, first["answer"])
        self.kubectl("delete", "pod", name, "-n", "kubeai", "--wait=true", "--timeout=30s")
        current = json.loads(self.kubectl("get", "--raw", "/api/v1/namespaces/kubeai/services/http:kubeai-agent:8000/proxy/cluster/status"))
        self.assertNotIn(name, [p["name"] for p in current["pods"]])
        history = [{"role": "user", "content": question},
                   {"role": "assistant", "content": first["answer"]},
                   {"role": "user", "content": "Et maintenant ? Ce pod a-t-il toujours un problème ? Vérifie les données actuelles. Réponds en deux phrases."}]
        answers = []
        for streaming in (False, True):
            body = {"model": "kubeai-apertus", "messages": history, "stream": streaming, "temperature": 0, "max_tokens": 256}
            result = self.remote(f'''
import httpx,json
import os
body=json.loads({json.dumps(body)!r})
r=httpx.post("http://127.0.0.1:8000/v1/chat/completions",headers={{"Authorization":"Bearer "+os.environ["KUBEAI_API_KEY"]}},json=body,timeout=180); r.raise_for_status()
if body["stream"]:
    events=[json.loads(line[6:]) for line in r.text.splitlines() if line.startswith("data: ") and line!="data: [DONE]"]
    assert not any("error" in event for event in events),events
    assert "data: [DONE]" in r.text
    text="".join((e["choices"][0].get("delta",{{}}).get("content") or "") for e in events if e.get("choices"))
else:
    text=r.json()["choices"][0]["message"]["content"]
print(json.dumps({{"stream":body["stream"],"answer":text}}))
''')
            text = result["answer"].lower()
            self.assertTrue(any(term in text for term in ("no longer", "not present", "absent", "removed", "not found", "not in", "does not appear")), result)
            self.assertIn("Sources disponibles", result["answer"])
            self.assertNotIn("Sources available", result["answer"])
            self.assertNotIn("Pod `a-t-il`", result["answer"])
            answers.append(result)
        print(json.dumps({"pod": name, "before_deletion": first["answer"], "after_deletion": answers,
                          "current_health": current["health"]}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    unittest.main(verbosity=2)
