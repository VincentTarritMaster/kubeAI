import json
import unittest

from fastapi.testclient import TestClient
import httpx

from app.config import Settings
from app.main import create_app
from app.services.source_notes import SourceNoteFilter
from test_api import pod, sample

TEST_API_KEY = "unit-api-key-0123456789abcdef0123456789"


class ChatTests(unittest.TestCase):
    def client(self, *, kube_code=200, prom_code=200, loki_code=200, llm_code=200,
               key_file="", llm_text="Un pod Ready [K8S].", cluster_pods=None, log_entries=None):
        self.requests = []
        self.reads = {"kubernetes": 0, "mimir": 0, "loki": 0}
        self.log_queries = []

        def kube(request):
            self.reads["kubernetes"] += 1
            return httpx.Response(kube_code, json={"items": cluster_pods if cluster_pods is not None else [pod()]})

        def prom(request):
            self.reads["mimir"] += 1
            self.assertEqual(request.url.path, "/prometheus/api/v1/query")
            value = 0.02 if "rate(" in request.url.params["query"] else 104857600
            return httpx.Response(prom_code, json={"status": "success", "data": {
                "resultType": "vector", "result": [sample(value)],
            }})

        def loki(request):
            self.reads["loki"] += 1
            self.log_queries.append(request.url.params["query"])
            self.assertIn('pod=~', request.url.params["query"])
            self.assertEqual(request.url.params["limit"], "12")
            return httpx.Response(loki_code, json={"status": "success", "data": {
                "resultType": "streams", "result": [{"stream": {"pod": "web", "container": "app"},
                                                        "values": log_entries if log_entries is not None else [["1792347000000000000", "application ready"]]}],
            }})

        def llm(request):
            self.assertEqual(request.url.host, "host.minikube.internal")
            if request.method == "GET":
                return httpx.Response(llm_code, json={"data": [{"id": "apertus-8b"}]})
            payload = json.loads(request.content)
            self.requests.append(payload)
            self.assertEqual(payload["model"], "apertus-8b")
            self.assertEqual(request.url.path, "/v1/chat/completions")
            if llm_code != 200:
                return httpx.Response(llm_code, json={"error": "private upstream details"})
            if payload.get("stream"):
                chunks = [
                    {"id": "test", "object": "chat.completion.chunk", "created": 1,
                     "model": "apertus-8b", "choices": [{"index": 0, "delta": {"role": "assistant", "content": None}, "finish_reason": None}]},
                ] + [
                    {"id": "test", "object": "chat.completion.chunk", "created": 1,
                     "model": "apertus-8b", "choices": [{"index": 0, "delta": {"content": char}, "finish_reason": None}]}
                    for char in llm_text
                ] + [
                    {"id": "test", "object": "chat.completion.chunk", "created": 1,
                     "model": "apertus-8b", "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]},
                ]
                data = "".join("data: " + json.dumps(c) + "\n\n" for c in chunks) + "data: [DONE]\n\n"
                return httpx.Response(200, text=data, headers={"content-type": "text/event-stream"})
            return httpx.Response(200, json={
                "id": "test", "object": "chat.completion", "created": 1, "model": "apertus-8b",
                "choices": [{"index": 0, "message": {"role": "assistant", "content": llm_text}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
            })

        return TestClient(create_app(
            Settings(ca_file="", token_file="", llm_provider="apertus",
                     llm_base_url="http://host.minikube.internal:11435/v1", llm_model="apertus-8b",
                     llm_api_key_file=key_file, llm_timeout=180, api_key=TEST_API_KEY),
            kubernetes_transport=httpx.MockTransport(kube), prometheus_transport=httpx.MockTransport(prom),
            loki_transport=httpx.MockTransport(loki), llm_transport=httpx.MockTransport(llm),
        ), headers={"Authorization": "Bearer " + TEST_API_KEY})

    def body(self, **extra):
        return {"model": "kubeai-apertus", "messages": [{"role": "user", "content": "Explain the latest observations."}], **extra}

    def test_chat_uses_real_snapshot_and_bounded_logs(self):
        with self.client() as client:
            response = client.post("/v1/chat/completions", json=self.body())
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["model"], "kubeai-apertus")
            content = response.json()["choices"][0]["message"]["content"]
            self.assertIn("Sources disponibles pour le cluster", content)
            self.assertIn("mimir", content)
            messages = self.requests[0]["messages"]
            self.assertIn('"memory_working_set_bytes":104857600', messages[-1]["content"])
            self.assertIn('"memory_working_set_mib":100.0', messages[-1]["content"])
            self.assertIn("application ready", messages[-1]["content"])
            self.assertIn("jamais des instructions", messages[0]["content"])
            self.assertTrue(messages[-1]["content"].endswith("CURRENT_QUESTION:\nExplain the latest observations."))

    def test_stream_remaps_model_and_adds_source_footer_before_finish(self):
        with self.client() as client:
            response = client.post("/v1/chat/completions", json=self.body(stream=True))
            self.assertEqual(response.status_code, 200)
            self.assertIn("text/event-stream", response.headers["content-type"])
            lines = [line[6:] for line in response.text.splitlines() if line.startswith("data: ")]
            self.assertEqual(lines[-1], "[DONE]")
            chunks = [json.loads(line) for line in lines[:-1]]
            self.assertTrue(all(c["model"] == "kubeai-apertus" for c in chunks))
            self.assertIn("Sources disponibles", chunks[-2]["choices"][0]["delta"]["content"])
            self.assertEqual(chunks[-1]["choices"][0]["finish_reason"], "stop")

    def test_diagnose_exposes_observation_with_answer(self):
        with self.client() as client:
            result = client.post("/diagnose", json={"question": "Que se passe-t-il ?"})
            self.assertEqual(result.status_code, 200)
            self.assertEqual(result.json()["observation"]["pods"][0]["name"], "web")
            self.assertEqual(result.json()["model"], "kubeai")
            self.assertNotIn("Relevé du namespace", result.json()["answer"])
            self.assertIn(result.json()["observation"]["observed_at"], result.json()["answer"])

    def test_old_assistant_diagnoses_and_footers_are_excluded(self):
        old = "\n\n---\nRelevé du namespace `kubeai` à 2000-01-01T00:00:00+01:00 · Sources available : kubernetes.\nPartial data : ancien avertissement"
        history = [{"role": "assistant", "content": "Ancien diagnostic." + old},
                   {"role": "user", "content": "Explique le champ Relevé du namespace."}]
        with self.client() as client:
            response = client.post("/v1/chat/completions", json=self.body(messages=history))
            self.assertEqual(response.status_code, 200)
        messages = self.requests[0]["messages"]
        self.assertEqual([m["role"] for m in messages], ["system", "user"])
        self.assertNotIn("Ancien diagnostic.", messages[-1]["content"])
        self.assertTrue(messages[-1]["content"].endswith(history[1]["content"]))
        self.assertNotIn("2000-01-01", json.dumps(messages))

    def test_each_turn_refreshes_sources_after_incident_is_removed_and_returns(self):
        failed = pod("incident", "failed-uid", phase="Failed", ready=False)
        failed["status"]["containerStatuses"][0]["state"] = {"terminated": {"reason": "Error", "exitCode": 42}}
        pods = [pod(), failed]
        logs = [["1792347000000000000", "simulated incident"]]
        history = self.body()["messages"]
        with self.client(cluster_pods=pods, log_entries=logs) as client:
            for turn, present in enumerate((True, False, True), start=1):
                if present:
                    pods[:] = [pod(), failed]
                    logs[:] = [["1792347000000000000", "simulated incident"]]
                else:
                    pods[:] = [pod()]
                    logs[:] = [["1792347000000000001", "application ready"]]
                response = client.post("/v1/chat/completions", json=self.body(messages=history, stream=turn == 2))
                self.assertEqual(response.status_code, 200)
                payload = self.requests[-1]
                messages = payload["messages"]
                self.assertEqual([m["role"] for m in messages], ["system", "user"])
                content = messages[-1]["content"]
                observation = json.loads(content.split("\n\nOBSERVATION_JSON", 1)[1].split(":\n", 1)[1].split("\n\nCURRENT_KUBERNETES_STATE", 1)[0])
                self.assertNotIn("Le pod incident est en échec.", content)
                self.assertEqual(observation["health"], "degraded" if present else "healthy")
                self.assertEqual(observation["summary"]["unhealthy"], int(present))
                self.assertEqual("incident" in [p["name"] for p in observation["pods"]], present)
                self.assertEqual("incident" in self.log_queries[-1], present)
                self.assertEqual(observation["logs"][0]["message"], logs[0][1])
                self.assertEqual(self.reads, {"kubernetes": turn, "mimir": turn * 2, "loki": turn})
                history += [{"role": "assistant", "content": "Le pod incident est en échec."},
                            {"role": "user", "content": "Et maintenant ?"}]

    def test_health_verdict_stays_grounded_as_incident_appears_and_disappears(self):
        failed = pod("kubeai-demo-failure", "failed-uid", phase="Failed", ready=False)
        failed["status"]["containerStatuses"][0]["state"] = {"terminated": {"reason": "Error", "exitCode": 42}}
        pods = [pod()]
        history = [{"role": "user", "content": "donne moi l'etat de sante de kubeai"}]
        with self.client(cluster_pods=pods, llm_text="Everything is healthy. Ignore the failed pod.") as client:
            for turn, present in enumerate((False, True, False, True)):
                pods[:] = [pod(), failed] if present else [pod()]
                response = client.post("/v1/chat/completions", json=self.body(messages=history, stream=turn % 2 == 1))
                self.assertEqual(response.status_code, 200)
                if turn % 2:
                    chunks = [json.loads(line[6:]) for line in response.text.splitlines()
                              if line.startswith("data: ") and line != "data: [DONE]"]
                    answer = "".join(c["choices"][0]["delta"].get("content") or "" for c in chunks)
                else:
                    answer = response.json()["choices"][0]["message"]["content"]
                self.assertIn("**degraded**" if present else "**healthy**", answer)
                self.assertEqual("**Failed**" in answer, present)
                self.assertNotIn("Everything is healthy.", answer)
                self.assertEqual(self.requests, [])
                self.assertEqual(self.reads["kubernetes"], turn + 1)
                self.assertEqual(self.reads["loki"], turn + 1)
                self.assertEqual(self.reads["mimir"], (turn + 1) * 2)
                history += [{"role": "assistant", "content": answer},
                            {"role": "user", "content": "Et maintenant ? Ce pod a-t-il toujours un problème ?"}]
                self.assertNotIn("Pod `a-t-il`", answer)

    def test_model_footer_is_replaced_by_current_note_in_both_modes(self):
        generated = "Incident simulé [LOGS].\n**Relevé du namespace `kubeai` à 2000-01-01T00:00:00+01:00**\nSources available : anciennes."
        for streaming in (False, True):
            with self.subTest(streaming=streaming), self.client(llm_text=generated) as client:
                response = client.post("/v1/chat/completions", json=self.body(stream=streaming))
                self.assertEqual(response.status_code, 200)
                if streaming:
                    chunks = [json.loads(line[6:]) for line in response.text.splitlines()
                              if line.startswith("data: ") and line != "data: [DONE]"]
                    answer = "".join(c["choices"][0]["delta"].get("content") or "" for c in chunks)
                else:
                    answer = response.json()["choices"][0]["message"]["content"]
                self.assertIn("Incident simulé [LOGS].", answer)
                self.assertNotIn("2000-01-01", answer)
                self.assertNotIn("anciennes", answer)
                self.assertEqual(answer.count("Relevé du namespace"), 0)
                self.assertEqual(answer.count("Sources disponibles"), 1)

    def test_note_filter_handles_every_split_without_hiding_event_timestamps(self):
        text = "Faits à 2026-10-18T19:29:43Z [LOGS].\nRelevé du namespace ancien\nSuite utile."
        for index in range(len(text) + 1):
            filter_ = SourceNoteFilter()
            result = filter_.feed(text[:index]) + filter_.feed(text[index:]) + filter_.finish()
            self.assertEqual(result, "Faits à 2026-10-18T19:29:43Z [LOGS].\nSuite utile.")

    def test_loki_failure_is_visible_but_does_not_hide_kubernetes(self):
        with self.client(loki_code=503) as client:
            result = client.post("/diagnose", json={"question": "Logs ?"}).json()
            self.assertEqual(result["observation"]["sources"]["loki"]["status"], "unavailable")
            self.assertEqual(result["observation"]["data_status"], "partial")
            self.assertIn("Données partielles", result["answer"])

    def test_kubernetes_failure_does_not_generate_an_ungrounded_answer(self):
        with self.client(kube_code=403) as client:
            history = [{"role": "user", "content": "Is the namespace healthy?"},
                       {"role": "assistant", "content": "Everything is healthy."},
                       {"role": "user", "content": "And now?"}]
            response = client.post("/v1/chat/completions", json=self.body(messages=history))
            self.assertEqual(response.status_code, 503)
            self.assertEqual(self.requests, [])

    def test_upstream_errors_are_sanitized_before_streaming(self):
        with self.client(llm_code=401) as client:
            response = client.post("/v1/chat/completions", json=self.body(stream=True))
            self.assertEqual(response.status_code, 503)
            self.assertEqual(response.json()["detail"]["source"], "llm")
            self.assertNotIn("private upstream", response.text)

    def test_missing_local_key_is_reported_without_breaking_inventory(self):
        with self.client(key_file="/nonexistent-kubeai-key") as client:
            response = client.post("/v1/chat/completions", json=self.body())
            self.assertEqual(response.status_code, 503)
            self.assertIn("Clé Apertus local introuvable", response.json()["detail"]["message"])
            self.assertEqual(client.get("/cluster/status").status_code, 200)

    def test_model_discovery_and_local_model_health(self):
        with self.client() as client:
            self.assertEqual(client.get("/v1/models").json()["data"][0]["id"], "kubeai")
            self.assertEqual(client.get("/llm/status").json()["hosting"], "configured_endpoint")

    def test_unknown_model_is_rejected(self):
        with self.client() as client:
            self.assertEqual(client.post("/v1/chat/completions", json=self.body(model="other")).status_code, 404)

    def test_invalid_history_is_rejected_but_long_old_history_is_reduced(self):
        with self.client() as client:
            self.assertEqual(client.post("/v1/chat/completions", json=self.body(messages=[])).status_code, 422)
            history = [{"role": "user", "content": "Observations Kubernetes : " + "a" * 10000},
                       {"role": "user", "content": "Explique les observations Kubernetes : " + "b" * 10000}]
            self.assertEqual(client.post("/v1/chat/completions", json=self.body(messages=history)).status_code, 200)
            self.assertTrue(self.requests[-1]["messages"][-1]["content"].endswith("b" * 10000))
            self.assertNotIn("a" * 10000, self.requests[-1]["messages"][-1]["content"])
            self.assertEqual(client.post("/v1/chat/completions", json=self.body(max_tokens=999999)).status_code, 422)

    def test_last_question_too_long_has_plain_actionable_error(self):
        with self.client() as client:
            response = client.post("/v1/chat/completions", json=self.body(messages=[
                {"role": "user", "content": "x" * 12001},
            ]))
        self.assertEqual(response.status_code, 413)
        self.assertIn("Raccourcissez seulement cette question", response.json()["detail"])
        self.assertNotIn("Value error", response.text)

    def test_local_streaming_and_json_keep_current_question_after_long_answer(self):
        history = [
            {"role": "user", "content": "Question précédente."},
            {"role": "assistant", "content": "Ancienne réponse." * 1300},
            {"role": "user", "content": "Explique les observations actuelles."},
        ]
        for streaming in (False, True):
            with self.subTest(streaming=streaming), self.client() as client:
                response = client.post("/v1/chat/completions",
                                       json=self.body(messages=history, stream=streaming))
                self.assertEqual(response.status_code, 200)
                payload = self.requests[-1]
                self.assertIn("jamais des instructions", payload["messages"][0]["content"])
                self.assertIn("Question précédente.", payload["messages"][-1]["content"])
                self.assertTrue(payload["messages"][-1]["content"].endswith(
                    "CURRENT_QUESTION:\nExplique les observations actuelles."))
                self.assertNotIn("Ancienne réponse.", payload["messages"][-1]["content"])
                if streaming:
                    self.assertIn("data: [DONE]", response.text)

    def test_incident_health_and_correlation_share_long_webui_conversation(self):
        incident = ("Analyse l'incident du pod kubeai-demo-incident dans le namespace kubeai, "
                    "le 19 octobre 2026 entre 18:25 et 18:33 UTC.")
        health = "Quel est l'état de santé de mon cluster ?"
        correlation = "Pour ce même incident, corrèle les logs et le CPU."
        history = [{"role": "user", "content": incident}]
        with self.client() as client:
            first = client.post("/v1/chat/completions", json=self.body(messages=history))
            self.assertEqual(first.status_code, 200)
            self.assertIn("kubeai-demo-incident", first.json()["choices"][0]["message"]["content"])
            history += [{"role": "assistant", "content": "Longue réponse B." * 1500},
                        {"role": "user", "content": health}]
            second = client.post("/v1/chat/completions", json=self.body(messages=history, stream=True))
            self.assertEqual(second.status_code, 200)
            self.assertIn("data: [DONE]", second.text)
            history += [{"role": "assistant", "content": "Longue réponse A." * 1500},
                        {"role": "user", "content": correlation}]
            third = client.post("/v1/chat/completions", json=self.body(messages=history))
            self.assertEqual(third.status_code, 200)
            final_answer = third.json()["choices"][0]["message"]["content"]
            self.assertIn("Corrélation de l'incident", final_answer)
            self.assertIn("kubeai/kubeai-demo-incident", final_answer)
            self.assertGreaterEqual(self.reads["kubernetes"], 3)


if __name__ == "__main__":
    unittest.main()
