#!/usr/bin/env bash

if [ -z "${BASH_VERSION:-}" ]; then
  exec bash "$0" "$@"
fi
set -euo pipefail

project_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
context=""
runner="sh"
context_set=false
while [[ $# -gt 0 ]]; do
  case "$1" in
    --shell)
      [[ $# -ge 2 ]] || { echo "Valeur attendue après --shell (sh ou ps1)." >&2; exit 2; }
      runner="$2"; shift 2 ;;
    --context)
      [[ $# -ge 2 && -n "$2" ]] || { echo "Valeur attendue après --context." >&2; exit 2; }
      context="$2"; context_set=true; shift 2 ;;
    -h|--help)
      echo "Usage : bash start-failure.sh [contexte] [--shell sh|ps1] [--context contexte]"
      exit 0 ;;
    -*) echo "Option inconnue : $1" >&2; exit 2 ;;
    *)
      if [[ "$context_set" == true ]]; then
        echo "Un seul contexte Kubernetes est accepté." >&2; exit 2
      fi
      context="$1"; context_set=true; shift ;;
  esac
done

if [[ -z "$context" ]]; then
  context="$(python3 "$project_dir/scripts/cluster-context.py" observed)"
fi

case "$runner" in
  sh) exec bash "$project_dir/scripts/run-demo-incident.sh" "$context" ;;
  ps1)
    if ! command -v pwsh >/dev/null 2>&1; then
      echo "PowerShell (pwsh) est absent. Sur Mac, utiliser simplement : bash start-failure.sh" >&2
      exit 1
    fi
    exec pwsh -NoProfile -File "$project_dir/scripts/run-demo-incident.ps1" -Context "$context"
    ;;
  *) echo "--shell doit être sh ou ps1." >&2; exit 2 ;;
esac
