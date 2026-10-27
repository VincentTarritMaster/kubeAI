"""Vérifier la séparation réelle des manifests et les identités distantes."""
import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import yaml

ROOT = Path(__file__).resolve().parents[1]


def load(name):
    spec = importlib.util.spec_from_file_location(name.replace('-', '_'), ROOT / 'scripts' / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


topology = load('start-two-clusters')
contexts = load('cluster-context')


class TwoClusterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        rendered = subprocess.run(['kubectl', 'kustomize', str(ROOT / 'k8s')],
                                  capture_output=True, text=True, check=True).stdout
        cls.base = list(yaml.safe_load_all(rendered))

    def stacks(self, namespace='kubeai'):
        return topology.split_manifests(self.base, 'supervision', 'observed', '192.168.79.2', namespace)

    def test_kubectl_json_stream_and_list_are_both_supported(self):
        objects = [{'kind': 'Namespace', 'metadata': {'name': 'one'}},
                   {'kind': 'Namespace', 'metadata': {'name': 'two'}}]
        self.assertEqual(topology.decode_manifests('\n'.join(json.dumps(obj) for obj in objects)), objects)
        self.assertEqual(topology.decode_manifests(json.dumps({'kind': 'List', 'items': objects})), objects)

    def test_minikube_operations_can_temporarily_remove_second_interface(self):
        result = subprocess.CompletedProcess([], 0, json.dumps({'kubeai-observed': {}, 'kubeai-private': {}}))
        with patch.object(topology.subprocess, 'run', return_value=result), patch.object(topology, 'run') as run:
            topology.detach_private_network('observed')
        run.assert_called_once_with(['docker', 'network', 'disconnect', 'kubeai-private', 'observed'])

    def test_private_link_is_restored_with_stable_ip(self):
        before = json.dumps({'kubeai-observed': {'IPAddress': '192.168.67.2'}})
        after = json.dumps({'kubeai-observed': {}, 'kubeai-private': {'IPAddress': '192.168.79.3'}})
        with patch.object(topology, 'run', side_effect=[before, '', after]) as run:
            address = topology.attach_private_network('observed', '192.168.79.3')
        self.assertEqual(address, '192.168.79.3')
        self.assertEqual(run.call_args_list[1].args[0],
                         ['docker', 'network', 'connect', '--ip', '192.168.79.3', 'kubeai-private', 'observed'])

    def test_profile_rename_only_stops_replaced_project_profile(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / '.cache').mkdir()
            (root / '.cache/topology.json').write_text(json.dumps({'supervision': 'old-supervision', 'observed': 'observed'}))
            with patch.object(topology, 'ROOT', root), patch.object(topology, 'detach_private_network') as detach, \
                    patch.object(topology.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0)), \
                    patch.object(topology, 'run') as run:
                topology.stop_previous_topology('new-supervision', 'observed')
            detach.assert_called_once_with('old-supervision')
            run.assert_called_once_with(['minikube', '--profile', 'old-supervision', 'stop'])

    def test_same_profile_names_do_not_stop_current_clusters(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / '.cache').mkdir()
            (root / '.cache/topology.json').write_text(json.dumps({'supervision': 'supervision', 'observed': 'observed'}))
            with patch.object(topology, 'ROOT', root), patch.object(topology, 'detach_private_network') as detach, \
                    patch.object(topology, 'run') as run:
                topology.stop_previous_topology('supervision', 'observed')
            detach.assert_not_called()
            run.assert_not_called()

    def test_namespaces_have_distinct_names_between_clusters(self):
        stacks = topology.split_manifests(self.base, 'supervision', 'observed', '192.168.79.2')
        namespaces = lambda role: {obj['metadata']['name'] for obj in stacks[role] if obj['kind'] == 'Namespace'}
        self.assertEqual(namespaces('observed'), {'applications', 'collecte'})
        self.assertEqual(namespaces('supervision'), {'kubeai', 'supervision'})

    def test_components_live_only_in_their_cluster(self):
        stacks = self.stacks()
        names = lambda role: {obj['metadata']['name'] for obj in stacks[role] if obj['kind'] == 'Deployment'}
        self.assertEqual(names('observed'), {'alloy', 'kube-state-metrics'})
        self.assertEqual(names('supervision'), {'mimir', 'loki', 'kubeai-agent', 'open-webui'})
        self.assertFalse(any(obj['kind'] == 'Deployment' and obj['metadata']['name'] == 'kubeai-agent'
                             for obj in stacks['observed']))

    def test_collector_sends_labelled_data_to_supervision(self):
        observed = self.stacks()['observed']
        config = next(obj for obj in observed if obj['kind'] == 'ConfigMap' and obj['metadata']['name'].startswith('alloy-config-'))
        text = config['data']['config.alloy']
        self.assertEqual(text.count('external_labels = {"cluster" = sys.env("OBSERVED_CLUSTER")}'), 2)
        self.assertIn('sys.env("LOKI_WRITE_URL")', text)
        self.assertIn('sys.env("MIMIR_WRITE_URL")', text)
        self.assertNotIn('mimir.monitoring.svc', text)
        self.assertNotIn('loki.monitoring.svc', text)
        target = next(obj for obj in observed if obj['metadata']['name'] == 'kubeai-collector-targets')
        self.assertEqual(target['data']['MIMIR_WRITE_URL'], 'http://192.168.79.2:30009/api/v1/push')

    def test_namespace_is_propagated_without_moving_supervision(self):
        stacks = self.stacks('applications')
        self.assertIn('applications', {obj['metadata']['name'] for obj in stacks['observed'] if obj['kind'] == 'Namespace'})
        self.assertIn('kubeai', {obj['metadata']['name'] for obj in stacks['supervision'] if obj['kind'] == 'Namespace'})
        reader = next(obj for obj in stacks['observed'] if obj['kind'] == 'ServiceAccount' and obj['metadata']['name'] == 'kubeai-agent')
        self.assertEqual(reader['metadata']['namespace'], 'applications')
        binding = next(obj for obj in stacks['observed'] if obj['kind'] == 'ClusterRoleBinding' and obj['metadata']['name'] == 'kubeai-agent')
        self.assertEqual(binding['subjects'][0]['namespace'], 'applications')
        alloy = next(obj for obj in stacks['observed'] if obj['kind'] == 'ConfigMap' and obj['metadata']['name'].startswith('alloy-config-'))
        self.assertNotIn('namespaces {', alloy['data']['config.alloy'])
        self.assertNotIn('names = ["applications"]', alloy['data']['config.alloy'])

    def test_agent_uses_only_remote_reader_and_cluster_filter(self):
        supervision = self.stacks()['supervision']
        agent = next(obj for obj in supervision if obj['kind'] == 'Deployment' and obj['metadata']['name'] == 'kubeai-agent')
        self.assertFalse(agent['spec']['template']['spec']['automountServiceAccountToken'])
        self.assertFalse(any(obj['kind'] == 'ClusterRoleBinding' for obj in supervision))
        config = next(obj for obj in supervision if obj['kind'] == 'ConfigMap' and obj['metadata']['name'] == 'kubeai-agent-config')
        self.assertEqual(config['data']['OBSERVED_CLUSTER'], 'observed')
        self.assertEqual(config['data']['KUBERNETES_ACCESS_MODE'], 'kubeconfig')

    def test_reader_config_contains_no_administrator_credentials(self):
        config = topology.remote_kubeconfig({'certificate-authority-data': 'ca', 'client-key-data': 'admin-key'},
                                            'reader-token', 'https://192.168.79.3:8443', 'observed', 'applications')
        self.assertNotIn('admin-key', json.dumps(config))
        self.assertEqual(config['users'][0]['user'], {'token': 'reader-token'})
        self.assertEqual(config['contexts'][0]['context']['namespace'], 'applications')
        with self.assertRaises(ValueError):
            topology.remote_kubeconfig({'insecure-skip-tls-verify': True}, 'token', 'https://host', 'observed')

    def test_contexts_use_env_before_start_and_actual_state_after_start(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / '.env').write_text('KUBEAI_OBSERVED_CLUSTER=other\nKUBERNETES_NAMESPACE=applications\n')
            self.assertEqual(contexts.context('observed', root), 'other')
            self.assertEqual(contexts.context('namespace', root), 'applications')
            (root / '.cache').mkdir()
            (root / '.cache/topology.json').write_text(json.dumps({'observed': 'active', 'namespace': 'active-apps'}))
            self.assertEqual(contexts.context('observed', root), 'active')
            self.assertEqual(contexts.context('namespace', root), 'active-apps')


if __name__ == '__main__':
    unittest.main()
