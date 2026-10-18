#!/usr/bin/env bash
set -euo pipefail
project_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
python3 "$project_dir/scripts/local-llm.py" setup
bash "$project_dir/start.sh" minikube
