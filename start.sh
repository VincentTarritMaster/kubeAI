#!/usr/bin/env bash

set -e

echo "🚀 Starting KubeAI..."

kubectl apply -f k8s/namespace.yaml

echo "✅ KubeAI started"