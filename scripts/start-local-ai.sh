#!/usr/bin/env bash
set -euo pipefail
project_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
echo "Apertus est facultatif et doit être configuré explicitement."
echo "Consultez $project_dir/llm/README.md pour les commandes de préparation et de lancement."
