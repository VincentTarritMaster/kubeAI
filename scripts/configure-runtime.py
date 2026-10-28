#!/usr/bin/env python3
"""Configurer le déploiement Mac depuis .env sans afficher les clés."""

import argparse
import json
from pathlib import Path
import re
import secrets
import subprocess
import tempfile
from urllib.parse import urlsplit, urlunsplit

ROOT = Path(__file__).resolve().parents[1]
PUBLIC_KEYS = (
    "KUBEAI_CHAT_MODE", "KUBEAI_TOOL_PROTOCOL", "KUBEAI_AGENT_MAX_STEPS", "KUBEAI_AGENT_TIMEOUT_SECONDS",
    "KUBEAI_AUDIT_ENABLED", "KUBEAI_RATE_LIMIT_PER_MINUTE", "KUBEAI_RATE_LIMIT_BURST",
    "KUBERNETES_NAMESPACE", "UPSTREAM_TIMEOUT_SECONDS", "METRICS_MAX_AGE_SECONDS",
    "KUBEAI_DISPLAY_TIMEZONE", "MISTRAL_BASE_URL", "MISTRAL_MODEL",
)


def read_env(path):
    values = {}
    if not path.is_file():
        raise ValueError("Fichier .env absent : copier .env.example vers .env et le configurer.")
    for number, line in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        match = re.fullmatch(r"([A-Z][A-Z0-9_]*)\s*=\s*(.*)", line)
        if not match:
            raise ValueError(f"Format NOM=valeur attendu dans .env, ligne {number}.")
        key, value = match.groups()
        if value.startswith(('"', "'")):
            if len(value) < 2 or value[-1] != value[0]:
                raise ValueError(f"Guillemets incomplets dans .env, ligne {number}.")
            value = value[1:-1]
        else:
            value = re.split(r"\s+#", value, maxsplit=1)[0].rstrip()
        values[key] = value
    return values


def read_key(value, root):
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = root / path
    if not path.is_file():
        raise ValueError(
            "Fichier de clé introuvable. Pour Apertus, lancer d'abord "
            "python3 scripts/local-llm.py setup puis python3 scripts/local-llm.py start."
        )
    key = path.read_text(encoding="utf-8").strip()
    if not key:
        raise ValueError("Le fichier de clé configuré est vide.")
    return key


def configuration(values, context, root=ROOT):
    provider = values.get("LLM_PROVIDER", "mistral-api")
    provider = {"apertus": "local", "mistral": "mistral-api"}.get(provider, provider)
    if provider not in ("local", "mistral-api"):
        raise ValueError("LLM_PROVIDER doit être local ou mistral-api.")
    config = {key: values[key] for key in PUBLIC_KEYS if key in values}
    config["LLM_PROVIDER"] = provider
    timeout = values.get("LLM_TIMEOUT_SECONDS", "180" if provider == "local" else "60")
    if float(timeout) <= 0:
        raise ValueError("LLM_TIMEOUT_SECONDS doit être positif.")
    config["LLM_TIMEOUT_SECONDS"] = timeout
    if provider == "local":
        url = urlsplit(values.get("LLM_BASE_URL", ""))
        model = values.get("LLM_MODEL", "")
        if url.scheme not in ("http", "https") or not url.hostname or not model:
            raise ValueError("Le profil local exige LLM_BASE_URL et LLM_MODEL dans .env.")
        if url.username is not None or url.password is not None or url.query or url.fragment:
            raise ValueError("LLM_BASE_URL ne doit contenir ni clé, ni paramètres, ni fragment.")
        if url.hostname in ("127.0.0.1", "localhost", "::1"):
            # Le lanceur Minikube utilise le driver Docker sur macOS. Son alias
            # host.minikube.internal n'est pas nécessairement résolu par les pods.
            host = {"minikube": "host.docker.internal", "docker-desktop": "host.docker.internal"}.get(context)
            if not host:
                raise ValueError("LLM_BASE_URL doit être accessible depuis les pods du contexte choisi.")
            authority = host + (f":{url.port}" if url.port else "")
            url = url._replace(netloc=authority)
        config.update(LLM_BASE_URL=urlunsplit(url), LLM_MODEL=model)
        filename = values.get("LLM_API_KEY_FILE", "")
        key = read_key(filename, root) if filename else ""
        config["LLM_API_KEY_FILE"] = "/var/run/secrets/kubeai-llm/api-key" if key else ""
        return config, "kubeai-llm", key
    key = values.get("MISTRAL_API_KEY", "")
    if not key and values.get("MISTRAL_API_KEY_FILE"):
        key = read_key(values["MISTRAL_API_KEY_FILE"], root)
    if not key or key.startswith("REMPLACER"):
        raise ValueError("Clé Mistral manquante dans .env.")
    config["MISTRAL_API_KEY_FILE"] = "/var/run/secrets/kubeai-mistral/api-key"
    return config, "kubeai-mistral", key


def configure(context, config, secret_name, key):
    command = ["kubectl", "--context", context, "--request-timeout=30s", "-n", "kubeai"]

    def apply_secret(name, value):
        manifest = {
            "apiVersion": "v1", "kind": "Secret",
            "metadata": {"name": name, "namespace": "kubeai"},
            "type": "Opaque", "stringData": {"api-key": value},
        }
        # Les clés passent uniquement sur stdin, jamais dans les arguments.
        subprocess.run(command + ["apply", "--server-side", "--field-manager=kubeai-runtime", "-f", "-"],
                       input=json.dumps(manifest), text=True, check=True)

    existing = subprocess.run(
        command + ["get", "secret", "kubeai-api-auth", "--ignore-not-found", "-o", "name"],
        capture_output=True, text=True, check=True,
    )
    if not existing.stdout.strip():
        apply_secret("kubeai-api-auth", secrets.token_urlsafe(32))
    if key:
        apply_secret(secret_name, key)
    payload = json.dumps({"data": config})
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".json", delete=False) as patch:
        patch.write(payload)
        patch_path = patch.name
    try:
        subprocess.run(command + ["patch", "configmap", "kubeai-agent-config",
                                  "--type=merge", "--patch-file", patch_path],
                       text=True, check=True)
    finally:
        Path(patch_path).unlink(missing_ok=True)
    print("Configuration .env appliquée : fournisseur " + config["LLM_PROVIDER"])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--context", default="minikube")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    try:
        config, secret_name, key = configuration(read_env(ROOT / ".env"), args.context)
        if not args.check:
            configure(args.context, config, secret_name, key)
    except (ValueError, OSError, subprocess.CalledProcessError) as error:
        # Ne pas inclure d'arguments ou de contenu de configuration dans l'erreur.
        if isinstance(error, ValueError):
            raise SystemExit(str(error)) from None
        raise SystemExit("Configuration impossible : vérifier les fichiers et l'accès Kubernetes.") from None


if __name__ == "__main__":
    main()
