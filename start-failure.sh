#!/usr/bin/env bash
set -euo pipefail

project_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
context="${1:-minikube}"
namespace="kubeai"
pod="kubeai-demo-failure"

existing="$(kubectl --context "$context" get pod "$pod" -n "$namespace" --ignore-not-found \
  -o jsonpath='{.metadata.name}{"|"}{.metadata.labels.kubeai-demo}')"
if [[ -n "$existing" && "$existing" != "$pod|manual-failure" ]]; then
  echo "Refus : $pod existe déjà sans le label de cette démonstration." >&2
  exit 1
fi

if [[ -z "$existing" ]]; then
  kubectl --context "$context" create -f "$project_dir/k8s/demo/failure-pod.yaml"
else
  echo "Le pod de démonstration existe déjà ; ./stop-failure.sh permet de le supprimer."
fi

kubectl --context "$context" wait -n "$namespace" "pod/$pod" \
  --for=jsonpath='{.status.phase}'=Failed --timeout=60s
kubectl --context "$context" get pod "$pod" -n "$namespace"
kubectl --context "$context" logs "$pod" -n "$namespace" --timestamps
echo "Dans Open WebUI, demander : Quels pods ont un problème dans kubeai ?"
echo "Attendre la collecte des logs par Alloy ; poser la question dans les 10 minutes."
echo "Pour supprimer le pod : ./stop-failure.sh $context"
