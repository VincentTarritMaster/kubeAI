#!/usr/bin/env bash
set -euo pipefail

project_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
profile="${1:-minikube}"
minikube --profile "$profile" image build --tag kubeai-agent:0.1.0 "$project_dir/agent"
