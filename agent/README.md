# Agent KubeAI

Ce dossier contient le backend Python de KubeAI : une API FastAPI en lecture seule.
La version actuelle fournit l'état des pods et leurs mesures CPU/mémoire, récupère
des logs dans Loki et utilise Apertus auto-hébergé pour répondre aux questions.

| Fichier / dossier | Rôle |
|---|---|
| `app/main.py` | Point d’entrée et API principale |
| `app/config.py` | Configuration et variables d’environnement |
| `app/models.py` | Formats des requêtes et réponses |
| `app/api/` | Routes HTTP de l’agent |
| `app/clients/prometheus_client.py` | Interroger Prometheus |
| `app/clients/loki_client.py` | Rechercher les logs dans Loki |
| `app/clients/kubernetes_client.py` | Lister les pods avec le ServiceAccount et gérer la pagination |
| `app/services/cluster_status.py` | Assembler l'état Kubernetes et les métriques récentes |
| `app/services/diagnosis.py` | Produire le diagnostic |
| `app/services/correlation.py` | Relier métriques, logs et événements |
| `app/services/llm_service.py` | Appeler le modèle de langage |
| `app/prompts/` | Instructions du modèle |
| `tests/test_api.py` | Tests automatisés avec réponses Kubernetes/Prometheus simulées |
| `tests/test_chat.py` | Tests du chat, streaming, contexte réel et erreurs des sources |
| `tests/integration/test_live_cluster.py` | Tests explicites sur le vrai cluster |
| `requirements.txt` | Dépendances Python |
| `Dockerfile` | Construire l’image Docker |
| `k8s/` | Déploiement Kubernetes |
| `docs/` | Documentation du projet |
| `scripts/` | Scripts de démarrage et validation |

## Flux implémenté

Utilisateur → Open WebUI → Agent IA → Prometheus/Loki/Kubernetes → Modèle de langage → Diagnostic

Open WebUI se connecte aux routes compatibles OpenAI de cet agent. À chaque tour,
celui-ci prépare une observation Kubernetes/Prometheus/Loki et appelle Apertus
sur le Mac via llama.cpp/Metal. Le modèle virtuel exposé est `kubeai-apertus`.
Les événements et le service de corrélation avancée restent à développer.
Voir [le profil Apertus local](../llm/README.md) pour l'installation et les limites.

## Installation dans Minikube

Depuis la racine du dépôt, après démarrage de Minikube :

```bash
bash scripts/start-local-ai.sh
```

Le script construit `kubeai-agent:0.1.0` dans le runtime Minikube, applique les
manifests et redémarre l'agent pour charger l'image locale reconstruite.
Les dépendances Python et l'image de base sont figées dans `requirements.txt`
et le Dockerfile. Une clé locale aléatoire est générée pour le serveur du modèle
et montée dans l'agent depuis un Secret ; aucune clé d'API commerciale n'est requise.
Les installations suivantes peuvent être relancées par `./start.sh`.

Pour reconstruire uniquement l'agent, sans redéployer le monitoring :

```bash
bash scripts/build-agent.sh minikube
kubectl --context minikube apply -k k8s/
kubectl --context minikube rollout restart deployment/kubeai-agent -n kubeai
kubectl --context minikube rollout status deployment/kubeai-agent -n kubeai --timeout=3m
```

Le tag est local au profil Minikube. Pour un autre cluster, construire/publier
l'image dans un registre accessible et adapter le champ `image` du Deployment
via un overlay Kustomize avant déploiement. `./start.sh autre-contexte` n'effectue
pas de construction ni de chargement d'image Minikube.

## Appeler l'API

```bash
kubectl --context minikube port-forward -n kubeai service/kubeai-agent 8000:8000
```

Garder ce terminal ouvert. Consulter :

- API : <http://127.0.0.1:8000/cluster/status>
- Documentation interactive : <http://127.0.0.1:8000/docs>
- Contrat OpenAPI : <http://127.0.0.1:8000/openapi.json>
- État du moteur : <http://127.0.0.1:8000/llm/status>
- Modèles disponibles : <http://127.0.0.1:8000/v1/models>

Le nom `/cluster/status` ne signifie pas un inventaire de tous les namespaces :
la réponse expose explicitement `scope: namespace` et `namespace: kubeai`.
Ce périmètre correspond à son Role Kubernetes. Les nœuds et événements ne sont
pas encore interrogés.

Le contrat détaillé et les comportements en cas de panne figurent dans
[`docs/api-contract.md`](../docs/api-contract.md).

## Configuration

Les valeurs déployées sont dans `k8s/agent-configmap.yaml`. Une modification
de ce ConfigMap nécessite un redémarrage de l'agent ; `./start.sh` s'en charge
sur Minikube. `.env.example` documente les variables mais n'est pas chargé.

| Variable | Défaut |
|---|---|
| `KUBERNETES_NAMESPACE` | `kubeai` |
| `PROMETHEUS_URL` | `http://prometheus.monitoring.svc.cluster.local:9090` |
| `KUBERNETES_API_URL` | `https://kubernetes.default.svc` |
| `KUBERNETES_TOKEN_FILE` | Token projeté du ServiceAccount |
| `KUBERNETES_CA_FILE` | Certificat CA projeté du cluster |
| `UPSTREAM_TIMEOUT_SECONDS` | `5` |
| `METRICS_MAX_AGE_SECONDS` | `60` |
| `LOKI_URL` | `http://loki.monitoring.svc.cluster.local:3100` |
| `LLM_BASE_URL` | `http://host.minikube.internal:11435/v1` |
| `LLM_MODEL` | `apertus-8b` |
| `LLM_API_KEY_FILE` | `/var/run/secrets/kubeai-llm/api-key` |
| `LLM_TIMEOUT_SECONDS` | `180` |

Le token est relu à chaque appel Kubernetes pour prendre en compte sa rotation.
L'agent dispose uniquement du droit `list pods` dans `kubeai`, sans accès aux
Secrets via l'API, aux logs via Kubernetes, au proxy kubelet ou aux autres namespaces.
Les logs sont lus dans Loki, et la clé du LLM est montée comme fichier. Changer de namespace
nécessite aussi d'adapter le RoleBinding et le rôle dans le namespace cible.
Le Service reste interne au cluster, sans authentification applicative, pour
ce profil local de développement.

## Développement hors du cluster

Python 3.10 minimum, Python 3.13 recommandé comme dans l'image. Depuis la racine :

```bash
python3.13 -m venv agent/.venv
agent/.venv/bin/python -m pip install -r agent/requirements.txt
```

Dans deux terminaux, lancer le proxy Kubernetes et le tunnel Prometheus :

```bash
kubectl --context minikube proxy --port=8001
kubectl --context minikube port-forward -n monitoring service/prometheus 9090:9090
```

Puis dans un troisième terminal, depuis `agent/` :

```bash
export KUBERNETES_API_URL=http://127.0.0.1:8001
export KUBERNETES_TOKEN_FILE=
export KUBERNETES_CA_FILE=
export PROMETHEUS_URL=http://127.0.0.1:9090
.venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

Pour tester le chat hors du cluster, lancer aussi un tunnel vers Loki et configurer
`LOKI_URL=http://127.0.0.1:3100`, `LLM_BASE_URL=http://127.0.0.1:11435/v1` et
`LLM_API_KEY_FILE` avec le chemin absolu de `.cache/apertus/api-key`.

Le proxy utilise alors les droits de l'utilisateur kubectl ; les droits du
ServiceAccount sont utilisés uniquement dans le déploiement Kubernetes.

## Tests

Depuis `agent/` :

```bash
.venv/bin/python -m unittest discover -s tests -v
```

Les tests couvrent le contrat HTTP/OpenAPI, les métriques absentes, les erreurs
des dépendances, la pagination, la rotation du token, les pods terminés et en
CrashLoopBackOff, ainsi que l'exclusion des métriques d'anciens pods/conteneurs.

Tests supplémentaires sur le cluster réel, depuis la racine du dépôt :

```bash
KUBEAI_LIVE_TEST_CONTEXT=minikube python3 agent/tests/integration/test_live_cluster.py
KUBEAI_LIVE_TEST_CONTEXT=minikube python3 agent/tests/integration/test_live_chat.py
```

Ils comparent l'inventaire de l'API à Kubernetes, attendent les mesures CPU/mémoire,
puis créent un pod temporaire qui termine avec le code 1 pour vérifier la détection
d'un état dégradé. Ce pod est supprimé à la fin. Ces tests ne tournent pas sans
la variable de contexte explicite.

Validation du 18 octobre 2026 : les 22 tests automatisés et les deux tests sur
Minikube ont réussi. L'API a renvoyé les deux pods réels avec leurs mesures, puis
signalé `health: degraded` pour le pod temporaire Failed. Ce pod a été supprimé.
Les deux tests de chat réel ont aussi réussi : diagnostic avec les noms des pods
observés et réponse diffusée en streaming par Apertus. Le contrôle visuel du
navigateur n'a pas été effectué ; la configuration Open WebUI est déployée.
