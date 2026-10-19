#!/usr/bin/env bash

set -euo pipefail

project_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
context="${1:-minikube}"

echo "Déploiement de kubeAI sur le contexte ${context}..."
kubectl --context "$context" get nodes
if [[ "$context" == "minikube" ]]; then
  bash "$project_dir/scripts/build-agent.sh" minikube
fi
python3 "$project_dir/scripts/prepare-webui-storage.py" --context "$context"
kubectl --context "$context" apply -k "$project_dir/k8s/"
if [[ "$context" == "minikube" ]]; then
  # Le tag local est réutilisé pendant le développement ; recharger l'image construite.
  kubectl --context "$context" rollout restart deployment/kubeai-agent -n kubeai
fi

for deployment in mimir loki kube-state-metrics alloy; do
  kubectl --context "$context" rollout status "deployment/$deployment" -n monitoring --timeout=5m
done
kubectl --context "$context" rollout status deployment/open-webui -n kubeai --timeout=5m
kubectl --context "$context" rollout status deployment/kubeai-agent -n kubeai --timeout=5m

echo "kubeAI est prêt. Vérifier la collecte :"
echo "python3 scripts/verify-observability.py --context $context"
echo "Ouvrir Open WebUI :"
echo "kubectl --context $context port-forward -n kubeai service/open-webui 8080:80"
echo "Ouvrir la documentation de l'agent sur http://127.0.0.1:8000/docs :"
echo "kubectl --context $context port-forward -n kubeai service/kubeai-agent 8000:8000"
