"""Keep A/B/C evidence across normal stop, restart and pod replacement."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_loki_retention_and_query_window_cover_a_soutenance_cycle():
    config = (ROOT / "k8s/monitoring/config/loki.yml").read_text(encoding="utf-8")
    assert "retention_period: 30d" in config
    assert "max_query_lookback: 30d" in config
    assert "reject_old_samples_max_age: 30d" in config
    assert "retention_enabled: true" in config


def test_monitoring_data_and_alloy_wal_use_persistent_claims():
    expected = {
        "loki.yaml": ("loki-data", "/var/lib/loki"),
        "mimir.yaml": ("mimir-data", "/data/mimir"),
        "alloy.yaml": ("alloy-data", "/var/lib/alloy"),
    }
    for name, (claim, mount) in expected.items():
        manifest = (ROOT / "k8s/monitoring" / name).read_text(encoding="utf-8")
        assert f"claimName: {claim}" in manifest
        assert f"mountPath: {mount}" in manifest


def test_start_and_stop_never_delete_monitoring_claims_or_namespace():
    scripts = "\n".join((ROOT / "scripts" / name).read_text(encoding="utf-8")
                          for name in ("start.ps1", "stop.ps1", "setup.ps1", "common.ps1"))
    assert "delete pvc" not in scripts.lower()
    assert "delete namespace" not in scripts.lower()
    assert "--prune" not in scripts


def test_demo_replay_replaces_only_its_finished_pod():
    script = (ROOT / "scripts/run-demo-incident.ps1").read_text(encoding="utf-8")
    assert "-n','kubeai','delete','pod','kubeai-demo-incident'" in script
    assert "delete pvc" not in script.lower()
    assert "phase=complete" in script
