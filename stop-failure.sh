#!/usr/bin/env bash
set -euo pipefail

context="${1:-minikube}"

# Combiner le nom et le label évite de supprimer un pod étranger à cette démo.
kubectl --context "$context" delete pods -n kubeai \
  --field-selector=metadata.name=kubeai-demo-failure \
  --selector=kubeai-demo=manual-failure --ignore-not-found --wait=true --timeout=60s
