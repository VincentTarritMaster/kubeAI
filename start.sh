#!/usr/bin/env bash

set -e

echo "Starting KubeAI..."

kubectl apply -k k8s/

echo "KubeAI started"
echo "Open WebUI:"
echo "minikube service open-webui -n kubeai"
