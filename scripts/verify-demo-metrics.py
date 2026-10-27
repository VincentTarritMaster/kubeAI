#!/usr/bin/env python3
"""Vérifier les métriques du pod de démonstration kubeAI dans Mimir et Loki.

Le script interroge Mimir et Loki via le proxy des Services Kubernetes. Il
vérifie que le pod annoté est scrapé, que les compteurs HTTP et le CPU du
processus existent, et que les marqueurs du scénario sont présents.

À lancer après `scripts/run-demo-incident.ps1` :

    python scripts/verify-demo-metrics.py --context docker-desktop
"""

import argparse
import json
import importlib.util
import math
import re
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import urlencode


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--context", help="Ancien mode : un seul contexte pour les pods et le stockage")
    parser.add_argument("--supervision-context")
    parser.add_argument("--observed-context")
    parser.add_argument("--monitoring-namespace")
    parser.add_argument("--namespace")
    parser.add_argument("--pod", default="kubeai-demo-incident")
    parser.add_argument("--timeout", type=int, default=60, help="Attente des scrapes en secondes")
    args = parser.parse_args()
    spec = importlib.util.spec_from_file_location("contexts", Path(__file__).with_name("cluster-context.py"))
    contexts = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(contexts)
    args.supervision_context = args.supervision_context or args.context or contexts.context("supervision")
    args.observed_context = args.observed_context or args.context or contexts.context("observed")
    args.monitoring_namespace = args.monitoring_namespace or ("monitoring" if args.context else "supervision")
    args.namespace = args.namespace or ("kubeai" if args.context else contexts.context("namespace"))
    cluster_selector = "" if args.context else ",cluster=" + json.dumps(args.observed_context)
    if args.timeout <= 0:
        parser.error("--timeout doit être positif")

    def kubectl(*command, context=None):
        result = subprocess.run(
            ["kubectl", "--context", context or args.supervision_context, "--request-timeout=15s", *command],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode:
            raise RuntimeError(result.stderr.strip() or result.stdout.strip())
        return result.stdout

    def raw(service, port, path, params):
        route = (
            f"/api/v1/namespaces/{args.monitoring_namespace}/services/http:{service}:{port}"
            f"/proxy/{path}?{urlencode(params)}"
        )
        response = json.loads(kubectl("get", "--raw", route))
        if response.get("status") != "success":
            raise RuntimeError(f"{service} : {response}")
        return response

    def scalar(query):
        body = raw("mimir", 9009, "prometheus/api/v1/query", {"query": query})
        results = body["data"]["result"]
        if not results:
            return None
        value = float(results[0]["value"][1])
        return value if math.isfinite(value) else None

    def markers():
        now = time.time_ns()
        body = raw("loki", 3100, "loki/api/v1/query_range", {
            "query": ('{namespace="' + args.namespace + '",pod="' + args.pod
                      + '"' + cluster_selector + '} |= "KUBEAI_DEMO_INCIDENT"'),
            "start": str(now - 3600 * 10**9), "end": str(now),
            "limit": "50", "direction": "backward",
        })
        found = []
        for stream in body["data"]["result"]:
            for _, message in stream.get("values", []):
                match = re.search(
                    r"\bKUBEAI_DEMO_INCIDENT\s+phase=(normal|anomaly_start|recovered|complete)\b",
                    message,
                )
                if match:
                    found.append(match.group(1))
        return found

    try:
        pod = json.loads(kubectl("-n", args.namespace, "get", "pod", args.pod, "-o", "json", context=args.observed_context))
    except (RuntimeError, json.JSONDecodeError) as error:
        print(f"ÉCHEC : pod {args.namespace}/{args.pod} introuvable ({error}). "
              "Lancez d'abord scripts/run-demo-incident.ps1.", file=sys.stderr)
        return 1

    phase = pod.get("status", {}).get("phase", "Unknown")
    if phase not in {"Running", "Succeeded"}:
        print(f"ÉCHEC : le pod est en phase {phase}, pas Running/Succeeded.", file=sys.stderr)
        return 1

    labels = f'namespace="{args.namespace}",pod="{args.pod}"' + cluster_selector
    deadline = time.monotonic() + args.timeout
    last_error = ""
    while True:
        try:
            # Le pod de démo se termine après le scénario : une requête
            # instantanée sur `up` serait vide. On lit la dernière heure pour
            # vérifier que le scrape a bien eu lieu pendant l'exécution.
            up = scalar(f'max_over_time(up{{job="kubernetes-pods-annotated",{labels}}}[1h])')
            http_total = scalar(f"sum(last_over_time(http_requests_total{{{labels}}}[1h]))")
            http_500 = scalar(f'sum(last_over_time(http_requests_total{{{labels},status=~"5.."}}[1h]))')
            duration_count = scalar(f"sum(last_over_time(http_request_duration_seconds_count{{{labels}}}[1h]))")
            process_cpu = scalar(f"sum(last_over_time(process_cpu_seconds_total{{{labels}}}[1h]))")
            phases = markers()
            if up != 1:
                raise RuntimeError("le pod annoté n'a pas été scrapé dans la dernière heure (up != 1)")
            if not http_total or http_total <= 0:
                raise RuntimeError("http_requests_total absent ou nul")
            if not duration_count or duration_count <= 0:
                raise RuntimeError("http_request_duration_seconds_count absent ou nul")
            if not process_cpu or process_cpu <= 0:
                raise RuntimeError("process_cpu_seconds_total absent ou nul")
            if phase == "Succeeded" and (not http_500 or http_500 <= 0):
                raise RuntimeError("aucun 5xx historiquement observé pour un scénario terminé")
            if not phases:
                raise RuntimeError("aucun marqueur KUBEAI_DEMO_INCIDENT dans Loki")
            if phase == "Succeeded" and not {"normal", "anomaly_start", "recovered", "complete"}.issubset(phases):
                raise RuntimeError("les quatre marqueurs du scénario terminé ne sont pas encore présents dans Loki")
            report = {
                "status": "success",
                "observed_context": args.observed_context,
                "supervision_context": args.supervision_context,
                "namespace": args.namespace,
                "pod": args.pod,
                "phase": phase,
                "window": "1h",
                "markers": phases,
                "metrics": {
                    "up": up,
                    "http_requests_total": int(http_total),
                    "http_5xx_total": int(http_500 or 0),
                    "http_duration_count": int(duration_count),
                    "process_cpu_seconds_total": round(process_cpu, 6),
                },
                "sources": ["Mimir via Alloy/pod annoté", "Loki via Alloy"],
            }
            if not http_500:
                report["warning"] = (
                    "Aucun 5xx observé : le scénario n'a peut-être pas encore atteint "
                    "anomaly_start, ou le pod a été recréé."
                )
            print(json.dumps(report, indent=2, ensure_ascii=False))
            return 0
        except (RuntimeError, ValueError, KeyError, TypeError, json.JSONDecodeError) as error:
            if time.monotonic() >= deadline:
                print(f"ÉCHEC après {args.timeout}s : {error}", file=sys.stderr)
                return 1
            if str(error) != last_error:
                print(f"Attente : {error}", file=sys.stderr)
                last_error = str(error)
            time.sleep(5)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (RuntimeError, subprocess.TimeoutExpired) as error:
        print(f"ÉCHEC : {error}", file=sys.stderr)
        sys.exit(1)
