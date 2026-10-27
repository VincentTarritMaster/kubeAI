#!/usr/bin/env bash
if [ -z "${BASH_VERSION:-}" ]; then
  exec bash "$0" "$@"
fi
set -euo pipefail

project_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
context="${1:-$(python3 "$project_dir/scripts/cluster-context.py" observed)}"
namespace="${2:-$(python3 "$project_dir/scripts/cluster-context.py" namespace)}"

# Nettoyer seulement les pods exacts du scénario, jamais les collecteurs.
for pair in kubeai-demo-incident:incident kubeai-demo-failure:manual-failure kubeai-demo-correlation:correlation; do
  kubectl --context "$context" --request-timeout=30s delete pods -n "$namespace" \
    --field-selector="metadata.name=${pair%%:*}" --selector="kubeai-demo=${pair#*:}" \
    --ignore-not-found --wait=true --timeout=60s
done
kubectl --context "$context" --request-timeout=30s delete configmaps -n "$namespace" \
  --field-selector=metadata.name=kubeai-demo-incident-app --selector=kubeai-demo=incident \
  --ignore-not-found --wait=true --timeout=60s
echo "Cluster observé nettoyé. La supervision et les preuves collectées restent disponibles."
