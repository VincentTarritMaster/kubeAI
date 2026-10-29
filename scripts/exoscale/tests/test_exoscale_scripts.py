"""Offline tests for the Exoscale PowerShell scripts.

These tests never contact Exoscale. They use a local exo.cmd stub for status.
"""
from pathlib import Path
import ipaddress
import json
import os
import shutil
import subprocess
import sys
import tempfile

import pytest


ROOT = Path(__file__).resolve().parents[3]
SCRIPTS = ROOT / "scripts" / "exoscale"


def run(script, *args, env=None):
    command = ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass",
               "-File", str(SCRIPTS / script), *args]
    return subprocess.run(command, cwd=ROOT, env=env, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=60)


def test_start_and_stop_dry_run():
    for script in ("start.ps1", "stop.ps1"):
        result = run(script, "-DryRun")
        assert result.returncode == 0, result.stderr
        assert "ch-gva-2" in result.stdout
        assert "0,14 CHF/h" in result.stdout


def test_bootstrap_dry_run():
    result = run("bootstrap.ps1", "-DryRun")
    assert result.returncode == 0, result.stderr
    assert "kubeai-private" in result.stdout
    assert "kubeai-observed" in result.stdout
    assert "10.20.0.2 -> 10.20.0.200" in result.stdout
    assert "Mode simulation" in result.stdout


def test_zero_nodepool_lifecycle_contract():
    bootstrap = (SCRIPTS / "bootstrap.ps1").read_text(encoding="utf-8")
    assert "nodepool add" not in bootstrap
    start = (SCRIPTS / "start.ps1").read_text(encoding="utf-8")
    assert "nodepool add" in start
    assert "--size 1" in start
    stop = (SCRIPTS / "stop.ps1").read_text(encoding="utf-8")
    assert "nodepool scale" in stop
    policy = json.loads((ROOT / "infra" / "exoscale" / "iam" /
                         "kubeai-deployer-policy.json").read_text(encoding="utf-8"))
    rules = policy["services"]["compute"]["rules"]
    assert policy["default-service-strategy"] == "deny"
    assert len(rules) == 13
    assert any("int(parameters.size) == 1" in r["expression"] for r in rules)
    assert any("int(parameters.size) == 0 || int(parameters.size) == 1"
               in r["expression"] for r in rules)
    assert not any("delete-" in r["expression"] for r in rules)


def assert_valid_dhcp_range(cidr, start, end, netmask):
    network = ipaddress.ip_network(cidr, strict=False)
    if network.prefixlen != 24 or netmask != "255.255.255.0":
        raise ValueError("only /24 with 255.255.255.0 is supported")
    start_ip = ipaddress.ip_address(start)
    end_ip = ipaddress.ip_address(end)
    if start_ip not in network or end_ip not in network:
        raise ValueError("address outside network")
    if start_ip == network.network_address or end_ip == network.broadcast_address:
        raise ValueError("network/broadcast address is reserved")
    # Exoscale managed private networks reserve .254 for DHCP.
    reserved_dhcp = network.network_address + 254
    if end_ip == reserved_dhcp or end_ip >= reserved_dhcp:
        raise ValueError(".254 and above are reserved by Exoscale DHCP")


def test_private_network_dhcp_range_is_valid():
    config = json.loads((ROOT / "infra" / "exoscale" / "config.json").read_text(
        encoding="utf-8"))
    assert config["privateNetworkCidr"] == "10.20.0.0/24"
    assert config["privateNetworkStartIp"] == "10.20.0.2"
    assert config["privateNetworkEndIp"] == "10.20.0.200"
    assert config["privateNetworkNetmask"] == "255.255.255.0"
    assert_valid_dhcp_range(
        config["privateNetworkCidr"], config["privateNetworkStartIp"],
        config["privateNetworkEndIp"], config["privateNetworkNetmask"])

    with pytest.raises(ValueError):
        assert_valid_dhcp_range("10.20.0.0/24", "10.20.0.2", "10.20.0.254",
                                "255.255.255.0")

    for script in SCRIPTS.glob("*.ps1"):
        assert "10.20.0.254" not in script.read_text(encoding="utf-8")


def test_security_group_commands_do_not_use_zone_flag():
    for script in SCRIPTS.glob("*.ps1"):
        text = script.read_text(encoding="utf-8")
        assert "'security-group', 'list', '--zone'" not in text
        assert "security-group list --zone" not in text
        for line in text.splitlines():
            assert not ("security-group" in line and "--zone" in line), line


def test_security_group_help_has_no_zone_flag():
    exo = shutil.which("exo")
    if not exo:
        return
    commands = (
        ["compute", "security-group", "list"],
        ["compute", "security-group", "create"],
        ["compute", "security-group", "show"],
        ["compute", "security-group", "rule", "add"],
    )
    for command in commands:
        result = subprocess.run([exo, *command, "--help"], capture_output=True,
                                text=True, encoding="utf-8", errors="replace",
                                timeout=30)
        assert result.returncode == 0, result.stderr
        assert "--zone" not in result.stdout
        assert "--use-account" in result.stdout


def test_mtls_certificates_are_valid():
    openssl = Path(r"C:\Program Files\Git\mingw64\bin\openssl.exe")
    if not openssl.exists():
        return
    with tempfile.TemporaryDirectory() as directory:
        result = run("generate-mtls-certs.ps1", "-Apply", "-CertsOnly", "-Force",
                     "-OutDir", directory)
        assert result.returncode == 0, result.stderr
        for name in ("ca.crt", "server.crt", "client.crt"):
            assert (Path(directory) / name).exists()
        verify = subprocess.run(
            [str(openssl), "verify", "-CAfile", str(Path(directory) / "ca.crt"),
             str(Path(directory) / "server.crt"), str(Path(directory) / "client.crt")],
            capture_output=True, text=True, timeout=30)
        assert verify.returncode == 0, verify.stderr


def test_token_renewal_scripts_dry_run():
    result = run("refresh-observed-token.ps1", "-DryRun")
    assert result.returncode == 0, result.stderr
    assert "Simulation" in result.stdout
    task = run("install-token-renewal.ps1", "-DryRun")
    assert task.returncode == 0, task.stderr
    assert "Simulation" in task.stdout


def test_image_build_dry_run():
    result = run("build-image.ps1", "-DryRun")
    assert result.returncode == 0, result.stderr
    assert "docker build" in result.stdout
    assert "Publication non effectuée" in result.stdout


def test_overlays_have_mtls_and_private_nodeports():
    observed = subprocess.run(
        ["kubectl", "kustomize", "--load-restrictor=LoadRestrictionsNone",
         "k8s/overlays/exoscale/observed"], cwd=ROOT, capture_output=True,
        text=True, encoding="utf-8", errors="replace", timeout=60)
    assert observed.returncode == 0, observed.stderr
    assert "kubeai-alloy-mtls-client" in observed.stdout
    assert "server_name = \\\"mimir.kubeai.svc\\\"" in observed.stdout
    assert "https://CHANGE_ME_SUPERVISION_PRIVATE_IP:30009" in observed.stdout

    supervision = subprocess.run(
        ["kubectl", "kustomize", "--load-restrictor=LoadRestrictionsNone",
         "k8s/overlays/exoscale/supervision"], cwd=ROOT, capture_output=True,
        text=True, encoding="utf-8", errors="replace", timeout=60)
    assert supervision.returncode == 0, supervision.stderr
    assert "ssl_verify_client on" in supervision.stdout
    assert "kubeai-observability-mtls" in supervision.stdout
    assert "nodePort: 30009" in supervision.stdout
    assert "nodePort: 30310" in supervision.stdout
    assert "docker.io/anassndri/kubeai-agent@sha256:" in supervision.stdout


def test_agent_rbac_has_no_secret_or_write_access():
    rbac = (ROOT / "k8s" / "agent-rbac.yaml").read_text(encoding="utf-8")
    assert "secrets" not in rbac
    for verb in ("create", "update", "patch", "delete"):
        assert verb not in rbac


def test_bootstrap_has_no_public_nodeport_rule():
    text = (SCRIPTS / "bootstrap.ps1").read_text(encoding="utf-8")
    assert "30000-32767" not in text
    assert "'--flow', 'ingress', '--protocol', 'all', '--network', '0.0.0.0/0'" not in text
    assert "'--network', $cidr" in text


def test_repository_has_no_private_keys_or_tokens():
    suspicious = []
    for path in list((ROOT / "scripts" / "exoscale").rglob("*")) + list(
            (ROOT / "k8s" / "overlays" / "exoscale").rglob("*")):
        if not path.is_file() or path.suffix not in (".ps1", ".yaml", ".yml", ".conf"):
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        if "BEGIN PRIVATE KEY" in text or "mstrl_" in text:
            suspicious.append(str(path))
    assert not suspicious, suspicious


def test_status_with_simulated_exoscale():
    with tempfile.TemporaryDirectory() as directory:
        stub = Path(directory) / "exo.cmd"
        stub.write_text(
            '@echo off\r\n'
            'if "%1"=="config" (echo [{"name":"fake"}] & exit /b 0)\r\n'
            'if "%1"=="limits" (echo [] & exit /b 0)\r\n'
            'if "%1"=="compute" if "%2"=="sks" if "%3"=="list" '
            '(echo [{"name":"kubeai-observed"},{"name":"kubeai-supervision"}] & exit /b 0)\r\n'
            'if "%1"=="compute" if "%2"=="sks" if "%3"=="nodepool" if "%4"=="show" '
            '(echo {"size":1} & exit /b 0)\r\n'
            'if "%1"=="compute" if "%2"=="instance" (echo [] & exit /b 0)\r\n'
            'if "%1"=="compute" if "%2"=="block-storage" (echo [] & exit /b 0)\r\n'
            'if "%1"=="compute" if "%2"=="load-balancer" (echo [] & exit /b 0)\r\n'
            'if "%1"=="compute" if "%2"=="private-network" (echo [] & exit /b 0)\r\n'
            'exit /b 0\r\n', encoding="ascii")
        env = dict(os.environ)
        env["PATH"] = str(directory) + os.pathsep + env["PATH"]
        result = run("status.ps1", env=env)
        assert result.returncode == 0, result.stderr
        assert "Clusters et Nodepools" in result.stdout
        assert "Coût nœuds estimé" in result.stdout


if __name__ == "__main__":
    test_start_and_stop_dry_run()
    test_status_with_simulated_exoscale()
    print("Exoscale offline tests passed")
