#!/usr/bin/env python3
"""Préserver les données d'un ancien pod Open WebUI avant passage au PVC."""
import argparse
import json
from pathlib import Path
import subprocess
import time
import uuid

root = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--context", default="minikube")
args = parser.parse_args()
command = ["kubectl", "--context", args.context, "--request-timeout=30s"]


def kubectl(*items, **kwargs):
    return subprocess.run(command + list(items), check=True, **kwargs)


current = kubectl("get", "deployment", "open-webui", "-n", "kubeai", "--ignore-not-found", "-o", "json",
                  capture_output=True, text=True).stdout
if not current.strip():
    print("Pas d'ancien Open WebUI à migrer")
    raise SystemExit(0)
deployment = json.loads(current)
if any(v.get("persistentVolumeClaim", {}).get("claimName") == "open-webui-data"
       for v in deployment["spec"]["template"]["spec"].get("volumes", [])):
    print("Open WebUI utilise déjà son stockage persistant")
    raise SystemExit(0)

cache = root / ".cache/webui-backups"
cache.mkdir(parents=True, exist_ok=True)
backup = cache / ("before-pvc-%s.tar" % int(time.time()))
remote = '''
from pathlib import Path
import sqlite3, sys, tarfile
data = Path('/app/backend/data')
db = data / 'webui.db'
copy = Path('/tmp/kubeai-webui-backup.db')
if db.exists():
    with sqlite3.connect(db) as source, sqlite3.connect(copy) as target:
        source.backup(target)
def keep(member):
    return None if Path(member.name).name in {'webui.db', 'webui.db-wal', 'webui.db-shm'} else member
with tarfile.open(fileobj=sys.stdout.buffer, mode='w|') as tar:
    tar.add(data, arcname='.', filter=keep)
    if copy.exists():
        tar.add(copy, arcname='./webui.db')
'''
with backup.open("wb") as stream:
    kubectl("exec", "-n", "kubeai", "deployment/open-webui", "--", "python", "-c", remote, stdout=stream)
backup.chmod(0o600)
print("Sauvegarde locale créée : " + str(backup))
kubectl("apply", "-f", str(root / "k8s/open-webui-storage.yaml"))
name = "webui-storage-migration-" + uuid.uuid4().hex[:8]
helper = {
    "apiVersion": "v1", "kind": "Pod", "metadata": {"name": name, "namespace": "kubeai"},
    "spec": {
        "restartPolicy": "Never", "automountServiceAccountToken": False,
        "securityContext": {"runAsUser": 1000, "runAsGroup": 1000, "fsGroup": 1000},
        "containers": [{"name": "copy", "image": "busybox:1.37.0", "command": ["sleep", "600"],
                        "volumeMounts": [{"name": "data", "mountPath": "/data"}]}],
        "volumes": [{"name": "data", "persistentVolumeClaim": {"claimName": "open-webui-data"}}],
    },
}
kubectl("create", "-f", "-", input=json.dumps(helper), text=True)
try:
    kubectl("wait", "--for=condition=Ready", "pod/" + name, "-n", "kubeai", "--timeout=90s")
    with backup.open("rb") as stream:
        kubectl("exec", "-i", "-n", "kubeai", name, "--", "tar", "-xf", "-", "-C", "/data", stdin=stream)
    print("Données Open WebUI restaurées sur le PVC")
finally:
    kubectl("delete", "pod", name, "-n", "kubeai", "--wait=false")
