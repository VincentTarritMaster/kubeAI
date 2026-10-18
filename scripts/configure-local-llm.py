#!/usr/bin/env python3
"""Transférer la clé du serveur local vers un Secret Kubernetes sans l'afficher."""
import argparse
import json
from pathlib import Path
import subprocess

root = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--context", default="minikube")
args = parser.parse_args()
key = (root / ".cache/apertus/api-key").read_text().strip()
manifest = {
    "apiVersion": "v1", "kind": "Secret",
    "metadata": {"name": "kubeai-llm", "namespace": "kubeai"},
    "type": "Opaque", "stringData": {"api-key": key},
}
subprocess.run(["kubectl", "--context", args.context, "apply", "-f", str(root / "k8s/namespace.yaml")], check=True)
subprocess.run(["kubectl", "--context", args.context, "apply", "--server-side", "--field-manager=kubeai-local-llm", "-f", "-"],
               input=json.dumps(manifest), text=True, check=True)
