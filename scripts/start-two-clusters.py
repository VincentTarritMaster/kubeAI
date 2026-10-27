#!/usr/bin/env python3
"""Démarrer deux Minikube : applications observées et supervision kubeAI."""
import argparse
import copy
import importlib.util
import ipaddress
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import sys
import time
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / ".cache/two-clusters"
NETWORK = "kubeai-private"
SUPERVISION = "kubeai-supervision"
OBSERVED = "kubeai-observed"
APPS_NAMESPACE = "applications"
COLLECTOR_NAMESPACE = "collecte"
STORAGE_NAMESPACE = "supervision"


def run(command, *, payload=None, capture=False):
    return subprocess.run(command, input=payload, text=True, check=True, capture_output=capture).stdout


def kubectl(context, *args, payload=None, capture=False):
    return run(["kubectl", "--context", context, "--request-timeout=30s", *args], payload=payload, capture=capture)


def apply(context, objects):
    kubectl(context, "apply", "--validate=false", "-f", "-", payload=json.dumps({"apiVersion": "v1", "kind": "List", "items": objects}))


def wait_api(context, timeout=180):
    """Attendre l'API après un redémarrage de conteneur Minikube."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = subprocess.run(
            ["kubectl", "--context", context, "--request-timeout=5s", "get", "--raw=/readyz"],
            capture_output=True, text=True,
        )
        if result.returncode == 0:
            return
        time.sleep(2)
    raise ValueError("L'API Kubernetes de " + context + " ne répond pas après " + str(timeout) + " s.")


def runtime_module():
    spec = importlib.util.spec_from_file_location("runtime", ROOT / "scripts/configure-runtime.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def render_base(context):
    rendered = run(["kubectl", "kustomize", str(ROOT / "k8s")], capture=True)
    # kubectl fournit le parseur YAML ; aucun paquet Python supplémentaire.
    output = kubectl(context, "create", "--dry-run=client", "--validate=false", "-f", "-", "-o", "json",
                     payload=rendered, capture=True)
    return decode_manifests(output)


def decode_manifests(output):
    # Selon sa version, kubectl émet un List ou plusieurs objets JSON successifs.
    decoder = json.JSONDecoder()
    objects = []
    remaining = output.strip()
    while remaining:
        obj, end = decoder.raw_decode(remaining)
        objects.extend(obj["items"] if obj.get("kind") == "List" else [obj])
        remaining = remaining[end:].lstrip()
    return objects


def split_manifests(base, supervision, observed, supervision_ip, namespace=APPS_NAMESPACE):
    """Réutiliser les manifests communs, en plaçant chaque composant dans son rôle."""
    stacks = {"supervision": [], "observed": []}
    collectors = {"alloy", "kube-state-metrics", "alloy-data", "kubeai-monitoring-kube-state-metrics",
                  "kubeai-alloy-cadvisor", "kubeai-alloy-pod-logs"}
    for source in base:
        obj = copy.deepcopy(source)
        name, kind = obj["metadata"]["name"], obj["kind"]
        if kind == "Namespace":
            local_namespace = copy.deepcopy(obj)
            if name == "monitoring":
                local_namespace["metadata"]["name"] = STORAGE_NAMESPACE
                obj["metadata"]["name"] = COLLECTOR_NAMESPACE
            stacks["supervision"].append(local_namespace)
            if name == "kubeai":
                obj["metadata"]["name"] = namespace
            stacks["observed"].append(obj)
            continue
        collector = name in collectors or name.startswith("alloy-config-")
        remote_rbac = name == "kubeai-agent" and kind in {"ClusterRole", "ClusterRoleBinding"}
        role = "observed" if collector or remote_rbac else "supervision"
        if name == "kubeai-agent" and kind == "ServiceAccount":
            reader = copy.deepcopy(obj)
            reader["metadata"]["namespace"] = namespace
            stacks["observed"].append(reader)
        if role == "observed":
            if obj["metadata"].get("namespace") == "kubeai":
                obj["metadata"]["namespace"] = namespace
            if obj["metadata"].get("namespace") == "monitoring":
                obj["metadata"]["namespace"] = COLLECTOR_NAMESPACE
            for subject in obj.get("subjects", []):
                if subject.get("namespace") == "kubeai":
                    subject["namespace"] = namespace
                if subject.get("namespace") == "monitoring":
                    subject["namespace"] = COLLECTOR_NAMESPACE
        elif obj["metadata"].get("namespace") == "monitoring":
            obj["metadata"]["namespace"] = STORAGE_NAMESPACE
        if kind == "ConfigMap" and name.startswith("alloy-config-"):
            alloy = obj["data"]["config.alloy"]
            alloy = alloy.replace("\r\n", "\n")
            alloy = alloy.replace('"kubeai"', json.dumps(namespace))
            alloy = alloy.replace('loki.write "local" {', 'loki.write "local" {\n  external_labels = {"cluster" = sys.env("OBSERVED_CLUSTER")}')
            alloy = alloy.replace('prometheus.remote_write "local" {', 'prometheus.remote_write "local" {\n  external_labels = {"cluster" = sys.env("OBSERVED_CLUSTER")}')
            alloy = alloy.replace('"http://loki.monitoring.svc.cluster.local:3100/loki/api/v1/push"', 'sys.env("LOKI_WRITE_URL")')
            alloy = alloy.replace('"http://mimir.monitoring.svc:9009/api/v1/push"', 'sys.env("MIMIR_WRITE_URL")')
            alloy = alloy.replace('    {"__address__" = "mimir.monitoring.svc:9009", "component" = "mimir"},\n', '')
            alloy = alloy.replace('    {"__address__" = "loki.monitoring.svc.cluster.local:3100", "component" = "loki"},\n', '')
            alloy = alloy.replace('kube-state-metrics.monitoring.svc', 'kube-state-metrics.' + COLLECTOR_NAMESPACE + '.svc')
            obj["data"]["config.alloy"] = alloy
        if kind == "Deployment" and name == "alloy":
            obj["spec"]["template"]["spec"]["containers"][0]["envFrom"] = [
                {"configMapRef": {"name": "kubeai-collector-targets"}}]
        if kind == "Deployment" and name == "kubeai-agent":
            pod = obj["spec"]["template"]["spec"]
            pod["automountServiceAccountToken"] = False
            pod["volumes"].append({"name": "observed-auth", "secret": {
                "secretName": "kubeai-observed-auth", "defaultMode": 0o444}})
            pod["containers"][0]["volumeMounts"].append({"name": "observed-auth",
                "mountPath": "/var/run/secrets/kubeai-observed", "readOnly": True})
        if kind == "ConfigMap" and name == "kubeai-agent-config":
            obj["data"].update({"KUBERNETES_ACCESS_MODE": "kubeconfig", "KUBERNETES_CONTEXT": observed,
                "KUBERNETES_KUBECONFIG_FILE": "/var/run/secrets/kubeai-observed/kubeconfig", "OBSERVED_CLUSTER": observed})
            obj["data"]["KUBERNETES_NAMESPACE"] = namespace
            obj["data"]["MIMIR_URL"] = f"http://mimir.{STORAGE_NAMESPACE}.svc:9009/prometheus"
            obj["data"]["LOKI_URL"] = f"http://loki.{STORAGE_NAMESPACE}.svc:3100"
        stacks[role].append(obj)
    stacks["observed"].append({"apiVersion": "v1", "kind": "ConfigMap", "metadata": {
        "name": "kubeai-collector-targets", "namespace": COLLECTOR_NAMESPACE}, "data": {
            "OBSERVED_CLUSTER": observed, "MIMIR_WRITE_URL": f"http://{supervision_ip}:30009/api/v1/push",
            "LOKI_WRITE_URL": f"http://{supervision_ip}:30310/loki/api/v1/push"}})
    for name, port, node_port in (("mimir", 9009, 30009), ("loki", 3100, 30310)):
        stacks["supervision"].append({"apiVersion": "v1", "kind": "Service", "metadata": {
            "name": name + "-ingest", "namespace": STORAGE_NAMESPACE}, "spec": {
                "type": "NodePort", "selector": {"app": name}, "ports": [
                    {"name": "http", "port": port, "targetPort": "http", "nodePort": node_port}]}})
    return stacks


def remote_kubeconfig(cluster, token, endpoint, context, namespace="kubeai"):
    if cluster.get("insecure-skip-tls-verify") or not cluster.get("certificate-authority-data"):
        raise ValueError("Le cluster observé doit fournir une CA et vérifier TLS.")
    return {"apiVersion": "v1", "kind": "Config", "current-context": context,
            "clusters": [{"name": context, "cluster": {
                "server": endpoint, "certificate-authority-data": cluster["certificate-authority-data"]}}],
            "contexts": [{"name": context, "context": {
                "cluster": context, "user": "kubeai-reader", "namespace": namespace}}],
            "users": [{"name": "kubeai-reader", "user": {"token": token}}]}


def install_reader(supervision, observed, observed_ip, namespace="kubeai"):
    source = json.loads(kubectl(observed, "config", "view", "--minify", "--flatten", "--raw", "-o", "json", capture=True))
    token = kubectl(observed, "-n", namespace, "create", "token", "kubeai-agent", "--duration=24h", capture=True).strip()
    config = remote_kubeconfig(source["clusters"][0]["cluster"], token, f"https://{observed_ip}:8443", observed, namespace)
    secret = {"apiVersion": "v1", "kind": "Secret", "metadata": {
        "name": "kubeai-observed-auth", "namespace": "kubeai"}, "type": "Opaque",
        "stringData": {"kubeconfig": json.dumps(config)}}
    kubectl(supervision, "apply", "--server-side", "--field-manager=kubeai-topology", "-f", "-", payload=json.dumps(secret))


def ensure_cluster(profile, memory, service_cidr, private_ip):
    status = subprocess.run(["minikube", "--profile", profile, "status"], capture_output=True)
    if status.returncode:
        print(f"Démarrage du cluster {profile}...", flush=True)
        run(["minikube", "--profile", profile, "start", "--driver=docker",
             "--apiserver-ips=" + private_ip, "--memory=" + str(memory), "--cpus=2",
             "--keep-context", "--service-cluster-ip-range=" + service_cidr])


def detach_private_network(profile):
    """Les commandes Minikube supposent un seul réseau par conteneur."""
    result = subprocess.run(["docker", "inspect", profile, "--format", "{{json .NetworkSettings.Networks}}"],
                            capture_output=True, text=True)
    if result.returncode:
        return
    networks = json.loads(result.stdout)
    if NETWORK in networks and len(networks) > 1:
        run(["docker", "network", "disconnect", NETWORK, profile])


def attach_private_network(profile, private_ip):
    networks = json.loads(run(["docker", "inspect", profile, "--format", "{{json .NetworkSettings.Networks}}"], capture=True))
    if NETWORK not in networks:
        # Une seconde interface relie uniquement les deux nœuds ; Minikube
        # conserve son propre réseau et son API publiée pour kubectl sur le Mac.
        run(["docker", "network", "connect", "--ip", private_ip, NETWORK, profile])
        networks = json.loads(run(["docker", "inspect", profile, "--format", "{{json .NetworkSettings.Networks}}"], capture=True))
    return str(ipaddress.ip_address(networks[NETWORK]["IPAddress"]))


def stop_legacy_cluster(supervision, observed):
    if "minikube" in (supervision, observed):
        return
    status = subprocess.run(["minikube", "--profile", "minikube", "status"], capture_output=True)
    if status.returncode:
        return
    check = subprocess.run(["kubectl", "--context", "minikube", "--request-timeout=10s", "-n", "kubeai",
                            "get", "deployment", "kubeai-agent", "--ignore-not-found", "-o", "name"],
                           capture_output=True, text=True)
    if check.returncode == 0 and check.stdout.strip():
        print("Arrêt de l'ancien cluster kubeAI unique pour libérer ses ressources...", flush=True)
        run(["minikube", "--profile", "minikube", "stop"])


def stop_previous_topology(supervision, observed):
    state = ROOT / ".cache/topology.json"
    if not state.exists():
        return
    previous = json.loads(state.read_text())
    old_profiles = {previous.get("supervision"), previous.get("observed")} - {supervision, observed, None}
    for profile in sorted(old_profiles):
        if not isinstance(profile, str) or not re.fullmatch(r"[a-z][a-z0-9-]{0,61}[a-z0-9]|[a-z]", profile):
            raise ValueError("Ancien nom de profil invalide dans .cache/topology.json.")
        detach_private_network(profile)
        status = subprocess.run(["minikube", "--profile", profile, "status"], capture_output=True)
        if not status.returncode:
            print("Arrêt de l'ancien profil kubeAI " + profile, flush=True)
            run(["minikube", "--profile", profile, "stop"])


def check_agent(context):
    code = '''import json, os, time, urllib.request, urllib.error
for path in ("/ready", "/llm/status", "/cluster/inventory"):
    request = urllib.request.Request("http://127.0.0.1:8000" + path,
        headers={"Authorization": "Bearer " + os.environ["KUBEAI_API_KEY"]})
    for attempt in range(10):
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                data = json.load(response)
            break
        except (OSError, urllib.error.HTTPError):
            if attempt == 9:
                raise SystemExit("Le contrôle " + path + " reste indisponible ; vérifier ses sources.")
            time.sleep(2)
    print(path, data.get("status", "accessible"))
'''
    kubectl(context, "--request-timeout=0", "-n", "kubeai", "exec", "-i", "deployment/kubeai-agent", "--", "python", "-", payload=code)


def start_forward(context):
    CACHE.mkdir(parents=True, exist_ok=True)
    state_path = CACHE / "webui-forward.json"
    if state_path.exists():
        previous = json.loads(state_path.read_text())
        try:
            os.kill(previous["pid"], 0)
            if previous["context"] == context:
                with urllib.request.urlopen("http://127.0.0.1:8080/health", timeout=3) as response:
                    if response.status == 200:
                        return
        except (OSError, ValueError):
            pass
    with socket.socket() as probe:
        if probe.connect_ex(("127.0.0.1", 8080)) == 0:
            raise ValueError("Le port 8080 est occupé. Arrêter l'ancien port-forward Open WebUI, puis relancer start.")
    with (CACHE / "webui-forward.log").open("a") as log:
        process = subprocess.Popen(["kubectl", "--context", context, "-n", "kubeai", "port-forward",
                                    "--address=127.0.0.1", "service/open-webui", "8080:80"],
                                   stdout=log, stderr=log, start_new_session=(os.name != "nt"))
    state_path.write_text(json.dumps({"pid": process.pid, "context": context}))
    for _ in range(40):
        if process.poll() is not None:
            raise ValueError("Le tunnel WebUI s'est arrêté ; consulter .cache/two-clusters/webui-forward.log.")
        try:
            with urllib.request.urlopen("http://127.0.0.1:8080/health", timeout=2):
                return
        except OSError:
            time.sleep(0.5)
    raise ValueError("Le tunnel WebUI ne répond pas sur le port 8080.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--supervision")
    parser.add_argument("--observed")
    parser.add_argument("--no-forward", action="store_true")
    args = parser.parse_args()
    runtime = runtime_module()
    values = runtime.read_env(ROOT / ".env")
    args.supervision = args.supervision or values.get("KUBEAI_SUPERVISION_CLUSTER", SUPERVISION)
    args.observed = args.observed or values.get("KUBEAI_OBSERVED_CLUSTER", OBSERVED)
    namespace = values.get("KUBERNETES_NAMESPACE", APPS_NAMESPACE)
    for name in (args.supervision, args.observed, namespace):
        if not re.fullmatch(r"[a-z][a-z0-9-]{0,61}[a-z0-9]|[a-z]", name):
            raise ValueError("Les noms de clusters et le namespace doivent être des noms DNS valides (63 caractères maximum).")
    if args.supervision == args.observed:
        raise ValueError("Les deux clusters doivent avoir des noms différents.")
    if namespace in ("kubeai", COLLECTOR_NAMESPACE, STORAGE_NAMESPACE, "kube-system", "default"):
        raise ValueError("Choisir un namespace applicatif distinct des namespaces techniques, par exemple applications.")
    config, secret_name, key = runtime.configuration(values, "minikube")
    run(["docker", "info"], capture=True)
    stop_legacy_cluster(args.supervision, args.observed)
    stop_previous_topology(args.supervision, args.observed)
    network = run(["docker", "network", "ls", "--filter", "name=^" + NETWORK + "$", "--format", "{{.Name}}"], capture=True)
    if not network.strip():
        run(["docker", "network", "create", "--subnet=192.168.79.0/24", "--label=com.kubeai=two-clusters", NETWORK])
    detach_private_network(args.supervision)
    detach_private_network(args.observed)
    ensure_cluster(args.supervision, 4096, "10.96.0.0/16", "192.168.79.2")
    ensure_cluster(args.observed, 2048, "10.120.0.0/16", "192.168.79.3")
    wait_api(args.supervision)
    wait_api(args.observed)
    run(["minikube", "--profile", args.supervision, "image", "build", "--tag", "kubeai-agent:0.1.0", str(ROOT / "agent")])
    supervision_ip = attach_private_network(args.supervision, "192.168.79.2")
    observed_ip = attach_private_network(args.observed, "192.168.79.3")
    stacks = split_manifests(render_base(args.supervision), args.supervision, args.observed, supervision_ip, namespace)
    print("Déploiement des collecteurs sur " + args.observed, flush=True)
    apply(args.observed, stacks["observed"])
    print("Déploiement de la supervision sur " + args.supervision, flush=True)
    apply(args.supervision, [obj for obj in stacks["supervision"] if obj["kind"] == "Namespace"])
    install_reader(args.supervision, args.observed, observed_ip, namespace)
    apply(args.supervision, stacks["supervision"])
    runtime.configure(args.supervision, config, secret_name, key)
    kubectl(args.observed, "-n", COLLECTOR_NAMESPACE, "rollout", "restart", "deployment/alloy")
    for context, resource_namespace, deployments in ((args.supervision, STORAGE_NAMESPACE, ("mimir", "loki")),
                                                     (args.observed, COLLECTOR_NAMESPACE, ("alloy", "kube-state-metrics"))):
        for name in deployments:
            kubectl(context, "-n", resource_namespace, "rollout", "status", "deployment/" + name, "--timeout=5m")
    kubectl(args.supervision, "-n", "kubeai", "rollout", "restart", "deployment/kubeai-agent")
    for name in ("kubeai-agent", "open-webui"):
        kubectl(args.supervision, "-n", "kubeai", "rollout", "status", "deployment/" + name, "--timeout=5m")
    check_agent(args.supervision)
    CACHE.mkdir(parents=True, exist_ok=True)
    (ROOT / ".cache/topology.json").write_text(json.dumps({"supervision": args.supervision, "observed": args.observed,
                                                       "namespace": namespace}))
    if not args.no_forward:
        start_forward(args.supervision)
    print(f"\nKubeAI est prêt. Cluster observé : {args.observed} ; supervision : {args.supervision}.")
    print("Interface : http://127.0.0.1:8080")
    print("Démo : bash start-failure.sh ; nettoyage : bash stop-failure.sh")


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError, subprocess.CalledProcessError) as error:
        print("ÉCHEC : " + str(error) if isinstance(error, ValueError)
              else "ÉCHEC : démarrage interrompu ; consulter l'erreur de la commande précédente.", file=sys.stderr)
        sys.exit(1)
