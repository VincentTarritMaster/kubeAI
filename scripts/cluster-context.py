#!/usr/bin/env python3
"""Retrouver les contextes de la dernière topologie, sans charger de secrets."""
import json
import importlib.util
from pathlib import Path
import sys


def context(role, root=None):
    root = root or Path(__file__).resolve().parents[1]
    default = {"supervision": "kubeai-supervision", "observed": "kubeai-observed", "namespace": "applications"}
    state = root / ".cache/topology.json"
    values = dict(default)
    env = root / ".env"
    if env.exists():
        spec = importlib.util.spec_from_file_location("runtime", Path(__file__).with_name("configure-runtime.py"))
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        config = module.read_env(env)
        for key, name in (("supervision", "KUBEAI_SUPERVISION_CLUSTER"),
                          ("observed", "KUBEAI_OBSERVED_CLUSTER"), ("namespace", "KUBERNETES_NAMESPACE")):
            values[key] = config.get(name, values[key])
    if state.exists():
        values.update(json.loads(state.read_text()))
    value = values[role]
    if not isinstance(value, str) or not value or value.startswith("-"):
        raise ValueError("Contexte Kubernetes invalide")
    return value


if __name__ == "__main__":
    print(context(sys.argv[1]))
