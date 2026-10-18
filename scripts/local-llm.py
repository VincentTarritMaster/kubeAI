#!/usr/bin/env python3
"""Installer et gérer Apertus localement sur macOS Apple Silicon, sans service cloud."""

import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
from pathlib import Path
import platform
import secrets
import shutil
import signal
import subprocess
import sys
import tarfile
import threading
import time
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
CONFIG = json.loads((ROOT / "llm/local-apertus.json").read_text())
CACHE = ROOT / ".cache/apertus"
TOKEN = CACHE / "api-key"
PID = CACHE / "server.pid"
MODEL = CACHE / CONFIG["model"]["name"]
RUNTIME = CACHE / ("llama-" + CONFIG["runtime"]["version"])


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def download(spec, target):
    if target.exists():
        if sha256(target) != spec["sha256"]:
            raise RuntimeError("Empreinte incorrecte pour " + str(target))
        print("Déjà vérifié : " + target.name, flush=True)
        return
    part = target.with_suffix(target.suffix + ".part")
    print("Téléchargement : " + target.name, flush=True)
    ranges = target.with_suffix(target.suffix + ".ranges.json")
    if spec.get("size", 0) > 1024**3:
        download_ranges(spec, part, ranges)
    else:
        subprocess.run([
            "curl", "--fail", "--location", "--retry", "3", "--continue-at", "-",
            "--silent", "--show-error", "--output", str(part), spec["url"],
        ], check=True)
    if sha256(part) != spec["sha256"]:
        raise RuntimeError("Empreinte SHA-256 incorrecte : " + str(part))
    part.replace(target)
    if ranges.exists():
        ranges.unlink()
    print("SHA-256 vérifié : " + target.name, flush=True)


def download_ranges(spec, part, metadata):
    size = spec["size"]
    if metadata.exists():
        state = json.loads(metadata.read_text())
        if state["sha256"] != spec["sha256"] or not part.exists():
            raise RuntimeError("État de reprise incompatible")
    else:
        prefix = part.stat().st_size if part.exists() else 0
        state = {"sha256": spec["sha256"], "prefix": prefix, "done": []}
        metadata.write_text(json.dumps(state))
        with part.open("ab") as stream:
            stream.truncate(size)
    chunks = [(start, min(start + 64 * 1024**2, size) - 1)
              for start in range(state["prefix"], size, 64 * 1024**2)]
    lock = threading.Lock()

    def receive(item):
        start, end = item
        if start in state["done"]:
            return
        for attempt in range(3):
            try:
                request = urllib.request.Request(spec["url"], headers={"Range": "bytes=%s-%s" % (start, end)})
                with urllib.request.urlopen(request, timeout=45) as response, part.open("r+b") as stream:
                    if response.status != 206 or not response.headers.get("Content-Range", "").startswith("bytes %s-" % start):
                        raise RuntimeError("Le serveur n'a pas respecté la plage demandée")
                    stream.seek(start)
                    remaining = end - start + 1
                    while remaining:
                        block = response.read(min(1024**2, remaining))
                        if not block:
                            raise OSError("Transfert incomplet")
                        stream.write(block)
                        remaining -= len(block)
                with lock:
                    state["done"].append(start)
                    temporary = metadata.with_suffix(".tmp")
                    temporary.write_text(json.dumps(state))
                    temporary.replace(metadata)
                    received = state["prefix"] + sum(b - a + 1 for a, b in chunks if a in state["done"])
                    print("Modèle reçu : %.2f / %.2f Go" % (received / 1e9, size / 1e9), flush=True)
                return
            except OSError:
                if attempt == 2:
                    raise
                time.sleep(2)

    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(receive, chunks))


def binary():
    matches = list(RUNTIME.rglob("llama-server")) if RUNTIME.exists() else []
    if not matches:
        raise RuntimeError("Moteur absent ; lancer python3 scripts/local-llm.py setup")
    return matches[0]


def setup():
    if platform.system() != "Darwin" or platform.machine() != "arm64":
        raise RuntimeError("Ce profil utilise macOS Apple Silicon ; voir llm/README.md pour un autre serveur")
    CACHE.mkdir(parents=True, exist_ok=True)
    os.chmod(CACHE, 0o700)
    if not MODEL.exists() and shutil.disk_usage(CACHE).free < CONFIG["model"]["size"] + 1024**3:
        raise RuntimeError("Prévoir au moins 6 Gio libres pour l'installation")
    archive = CACHE / "llama-macos-arm64.tar.gz"
    download(CONFIG["runtime"], archive)
    if not RUNTIME.exists():
        RUNTIME.mkdir()
        with tarfile.open(archive) as tar:
            # L'archive est vérifiée, mais refuser aussi les chemins hors destination.
            for member in tar.getmembers():
                destination = (RUNTIME / member.name).resolve()
                if RUNTIME.resolve() not in destination.parents:
                    raise RuntimeError("Chemin d'archive invalide")
                if member.issym() or member.islnk():
                    linked = (destination.parent / member.linkname).resolve()
                    if RUNTIME.resolve() not in linked.parents:
                        raise RuntimeError("Lien d'archive invalide")
            tar.extractall(RUNTIME)
    download(CONFIG["model"], MODEL)
    if not TOKEN.exists():
        descriptor = os.open(TOKEN, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w") as stream:
            stream.write(secrets.token_urlsafe(32) + "\n")
    print("Apertus prêt. Démarrer : python3 scripts/local-llm.py start", flush=True)


def status():
    request = urllib.request.Request(
        "http://127.0.0.1:%s/v1/models" % CONFIG["port"],
        headers={"Authorization": "Bearer " + TOKEN.read_text().strip()},
    )
    with urllib.request.urlopen(request, timeout=3) as response:
        models = json.load(response)
    if CONFIG["alias"] not in [model["id"] for model in models["data"]]:
        raise RuntimeError("Un autre modèle occupe le port configuré")
    return models


def serve():
    engine = binary()
    if not MODEL.exists() or not TOKEN.exists():
        raise RuntimeError("Installation incomplète ; lancer setup")
    os.chdir(engine.parent)
    os.execv(str(engine), [
        str(engine), "--model", str(MODEL), "--alias", CONFIG["alias"],
        "--host", "0.0.0.0", "--port", str(CONFIG["port"]),
        "--api-key-file", str(TOKEN), "--ctx-size", str(CONFIG["context_size"]),
        "--n-gpu-layers", "99", "--parallel", "1", "--threads", "8",
        "--jinja", "--no-webui",
    ])


def start():
    try:
        status()
        print("Apertus est déjà démarré")
        return
    except (OSError, ValueError):
        pass
    binary()
    with (CACHE / "llama-server.log").open("a") as log:
        process = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), "serve"],
                                   stdout=log, stderr=log, start_new_session=True)
    PID.write_text(str(process.pid))
    print("Chargement du modèle ; journal : " + str(CACHE / "llama-server.log"), flush=True)
    deadline = time.monotonic() + 120
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError("Le serveur s'est arrêté ; consulter le journal")
        try:
            status()
            print("Apertus actif sur le port %s (Metal, authentification requise)" % CONFIG["port"], flush=True)
            return
        except (OSError, ValueError):
            time.sleep(2)
    raise RuntimeError("Le chargement dépasse 120 secondes ; consulter le journal")


def stop():
    if not PID.exists():
        print("Aucun processus suivi")
        return
    pid = int(PID.read_text())
    args = subprocess.run(["ps", "-p", str(pid), "-o", "args="], capture_output=True, text=True).stdout
    if "llama-server" in args and str(MODEL) in args:
        os.kill(pid, signal.SIGTERM)
        PID.unlink()
        print("Serveur Apertus arrêté")
    else:
        raise RuntimeError("Le PID ne correspond pas au serveur du projet ; aucun processus arrêté")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["setup", "start", "serve", "status", "stop"])
    args = parser.parse_args()
    try:
        if args.action == "status":
            print(json.dumps(status(), indent=2))
        else:
            {"setup": setup, "start": start, "serve": serve, "stop": stop}[args.action]()
    except (RuntimeError, OSError, ValueError, subprocess.CalledProcessError) as error:
        print("ÉCHEC : " + str(error), file=sys.stderr)
        sys.exit(1)
