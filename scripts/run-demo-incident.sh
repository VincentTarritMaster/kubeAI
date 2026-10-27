#!/usr/bin/env bash

if [ -z "${BASH_VERSION:-}" ]; then
  exec bash "$0" "$@"
fi
set -euo pipefail

project_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
context="${1:-$(python3 "$project_dir/scripts/cluster-context.py" observed)}"
namespace="${2:-$(python3 "$project_dir/scripts/cluster-context.py" namespace)}"
pod="kubeai-demo-incident"
kube=(kubectl --context "$context" --request-timeout=30s -n "$namespace")

# Une erreur de connexion doit arrêter le lancement, pas être prise pour un pod absent.
existing="$("${kube[@]}" get pod "$pod" --ignore-not-found \
  -o jsonpath='{.status.phase}{"|"}{.metadata.labels.kubeai-demo}')"
if [[ -n "$existing" ]]; then
  phase="${existing%%|*}"
  label="${existing#*|}"
  if [[ "$label" != "incident" ]]; then
    echo "Refus : $pod existe sans le label de cette démonstration." >&2
    exit 1
  fi
  case "$phase" in
    Succeeded|Failed)
      "${kube[@]}" delete pods --field-selector="metadata.name=$pod" \
        --selector=kubeai-demo=incident --wait=true --timeout=60s
      ;;
    Running|Pending)
      echo "Le scénario $pod est déjà en cours. Suivre ses logs avec :" >&2
      echo "kubectl --context $context -n $namespace logs -f pod/$pod" >&2
      exit 1
      ;;
    *)
      echo "État inattendu pour $pod : $phase. Aucun pod remplacé." >&2
      exit 1
      ;;
  esac
fi

"${kube[@]}" apply -k "$project_dir/k8s/demo/incident"
"${kube[@]}" wait --for=condition=Ready "pod/$pod" --timeout=180s
echo "Scénario en cours (environ 4 minutes). Les marqueurs sont horodatés en UTC."
log_file="$(mktemp "${TMPDIR:-/tmp}/kubeai-demo-incident.XXXXXX")"
trap 'rm -f -- "$log_file"' EXIT
# La limite courte des appels de contrôle ne convient pas au flux de 4 minutes.
"${kube[@]}" --request-timeout=0 logs --follow "pod/$pod" | tee "$log_file"
if ! grep -Eq ' KUBEAI_DEMO_INCIDENT phase=complete$' "$log_file"; then
  echo "Le scénario ne s'est pas terminé normalement : marqueur phase=complete absent." >&2
  exit 1
fi
"${kube[@]}" wait --for=jsonpath='{.status.phase}'=Succeeded "pod/$pod" --timeout=30s
echo "Scénario terminé. Dans Open WebUI, demander :"
echo "Analyse l'incident du dernier script de démonstration."
echo "Puis : Pour ce même incident, corrèle les logs, le CPU et les erreurs HTTP."
echo "Les preuves restent dans Loki et Mimir selon leur rétention."
