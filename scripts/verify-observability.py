#!/usr/bin/env python3
"""Vérifier la collecte réelle dans Mimir et Loki, avec kubectl et Python 3."""

import argparse
import json
import math
import subprocess
import sys
import time
import uuid
from urllib.parse import urlencode


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--context", default="minikube")
    parser.add_argument("--namespace", default="kubeai")
    parser.add_argument("--pod", help="Pod existant ; sinon un pod temporaire est créé puis supprimé")
    parser.add_argument("--timeout", type=int, default=180, help="Attente de la collecte en secondes")
    args = parser.parse_args()
    if args.timeout <= 0:
        parser.error("--timeout doit être positif")

    def kubectl(*command, payload=None):
        result = subprocess.run(
            ["kubectl", "--context", args.context, "--request-timeout=15s", *command],
            input=json.dumps(payload) if payload is not None else None,
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode:
            raise RuntimeError(result.stderr.strip() or result.stdout.strip())
        return result.stdout

    def api(service, port, path, params):
        route = (
            f"/api/v1/namespaces/monitoring/services/http:{service}:{port}"
            f"/proxy/{path}?{urlencode(params)}"
        )
        response = json.loads(kubectl("get", "--raw", route))
        if response.get("status") != "success":
            raise RuntimeError(f"{service} : {response}")
        return response["data"]["result"]

    def metric(query):
        results = api("mimir", 9009, "prometheus/api/v1/query", {"query": query})
        if not results:
            raise RuntimeError(f"Métrique encore absente : {query}")
        value = float(results[0]["value"][1])
        if not math.isfinite(value):
            raise RuntimeError(f"Métrique non exploitable : {query}")
        return value

    created = False
    pod_name = args.pod or f"observability-check-{uuid.uuid4().hex[:8]}"
    marker = f"kubeai-collection-{uuid.uuid4().hex}" if not args.pod else None
    started = time.time()
    try:
        if not args.pod:
            pod = {
                "apiVersion": "v1", "kind": "Pod",
                "metadata": {
                    "name": pod_name, "namespace": args.namespace,
                    "labels": {"app": "kubeai-observability-check"},
                },
                "spec": {
                    "restartPolicy": "Never",
                    "automountServiceAccountToken": False,
                    "securityContext": {
                        "runAsNonRoot": True, "runAsUser": 65534,
                        "seccompProfile": {"type": "RuntimeDefault"},
                    },
                    "containers": [{
                        "name": "check", "image": "busybox:1.37.0",
                        "command": ["sh", "-c", f"while true; do echo {marker}; sleep 5; done"],
                        "resources": {
                            "requests": {"cpu": "5m", "memory": "8Mi"},
                            "limits": {"cpu": "50m", "memory": "32Mi"},
                        },
                        "securityContext": {
                            "allowPrivilegeEscalation": False,
                            "readOnlyRootFilesystem": True,
                            "capabilities": {"drop": ["ALL"]},
                        },
                    }],
                },
            }
            kubectl("create", "-f", "-", payload=pod)
            created = True
            print(f"Pod de test créé : {args.namespace}/{pod_name}", file=sys.stderr)
        else:
            kubectl("get", "pod", pod_name, "-n", args.namespace, "-o", "name")

        labels = f"namespace={json.dumps(args.namespace)},pod={json.dumps(pod_name)}"
        containers = labels + ',container!="",container!="POD"'
        deadline = time.monotonic() + args.timeout
        last_error = ""
        while True:
            try:
                # Ne pas accepter un ancien échantillon si le collecteur s'est arrêté.
                for job in ("kubernetes-cadvisor", "kube-state-metrics"):
                    selector = f'up{{job="{job}"}}'
                    if metric(f"min({selector})") != 1:
                        raise RuntimeError(f"Collecte en échec : {job}")
                    if metric(f"time() - min(timestamp({selector}))") > 60:
                        raise RuntimeError(f"Collecte périmée : {job}")
                memory = metric(f"sum(container_memory_working_set_bytes{{{containers}}})")
                cpu = metric(f"sum(rate(container_cpu_usage_seconds_total{{{containers}}}[2m]))")
                restarts = metric(f"sum(kube_pod_container_status_restarts_total{{{labels}}})")
                ready = metric(f'sum(kube_pod_status_ready{{{labels},condition="true"}})')
                if ready < 1:
                    raise RuntimeError("Le pod n'est pas encore Ready")
                log_query = "{" + labels + "}"
                if marker:
                    log_query += " |= " + json.dumps(marker)
                streams = api("loki", 3100, "loki/api/v1/query_range", {
                    "query": log_query,
                    "start": str(int((started if marker else time.time() - 3600) * 1e9)),
                    "end": str(time.time_ns()), "limit": "5", "direction": "backward",
                })
                entries = [entry for stream in streams for entry in stream.get("values", [])]
                if not entries:
                    raise RuntimeError("Loki n'a pas encore reçu les logs du pod")
                report = {
                    "status": "success", "context": args.context,
                    "namespace": args.namespace, "pod": pod_name,
                    "cpu_cores": round(cpu, 6),
                    "memory_working_set_MiB": round(memory / 1048576, 2),
                    "restarts": int(restarts), "ready": True,
                    "log_sample": entries[0][1],
                    "log_timestamp_ns": entries[0][0],
                    "sources": ["Mimir via Alloy/cAdvisor/kube-state-metrics", "Loki via Alloy"],
                    "temporary_pod": created,
                }
                print(json.dumps(report, indent=2, ensure_ascii=False))
                break
            except (RuntimeError, subprocess.TimeoutExpired) as error:
                if time.monotonic() >= deadline:
                    raise RuntimeError(f"Collecte non validée après {args.timeout}s : {error}") from error
                if str(error) != last_error:
                    print(f"Attente : {error}", file=sys.stderr)
                    last_error = str(error)
                time.sleep(min(5, max(0, deadline - time.monotonic())))
    finally:
        if created:
            try:
                kubectl("delete", "pod", pod_name, "-n", args.namespace, "--wait=false")
                print(f"Pod temporaire supprimé : {pod_name}", file=sys.stderr)
            except (RuntimeError, subprocess.TimeoutExpired) as error:
                print(f"Nettoyage à terminer pour {pod_name} : {error}", file=sys.stderr)


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, subprocess.TimeoutExpired, ValueError, KeyError) as error:
        print(f"ÉCHEC : {error}", file=sys.stderr)
        sys.exit(1)
