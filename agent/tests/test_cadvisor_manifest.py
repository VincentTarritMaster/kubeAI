"""Static checks for the opt-in-by-default cAdvisor scrape."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_alloy_scrapes_cadvisor_through_the_kubernetes_api_proxy():
    config = (ROOT / "k8s/monitoring/config/config.alloy").read_text(encoding="utf-8")
    assert 'prometheus.scrape "cadvisor"' in config
    assert 'job_name          = "kubernetes-cadvisor"' in config
    assert "/api/v1/nodes/$1/proxy/metrics/cadvisor" in config
    assert "kubernetes.default.svc:443" in config
    assert "bearer_token_file" in config and "tls_config" in config


def test_cadvisor_rbac_is_bounded_to_read_only_node_metrics():
    rbac = (ROOT / "k8s/monitoring/rbac-cadvisor.yaml").read_text(encoding="utf-8")
    assert "resources: [nodes]" in rbac
    assert "verbs: [list, watch]" in rbac
    assert "resources: [nodes/proxy]" in rbac
    assert "verbs: [get]" in rbac
    assert "kind: ClusterRoleBinding" in rbac
    assert "name: alloy" in rbac and "namespace: monitoring" in rbac
    kustomization = (ROOT / "k8s/monitoring/kustomization.yaml").read_text(encoding="utf-8")
    assert "rbac-cadvisor.yaml" in kustomization


def test_alloy_and_log_rbac_cover_all_namespaces():
    config = (ROOT / "k8s/monitoring/config/config.alloy").read_text(encoding="utf-8")
    assert "namespaces {" not in config
    assert 'regex         = "kubeai"' not in config
    rbac = (ROOT / "k8s/monitoring/rbac-kubeai.yaml").read_text(encoding="utf-8")
    assert "kind: ClusterRole" in rbac
    assert "kind: ClusterRoleBinding" in rbac
    assert "resources: [pods]" in rbac and "resources: [pods/log]" in rbac
    assert "namespace: kubeai" not in rbac
