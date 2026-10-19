# Agent kubeAI

Ce dossier contient le backend Python de kubeAI : une API FastAPI en lecture seule.
La version actuelle fournit l'état des pods et leurs mesures CPU/mémoire, récupère
des logs dans Loki et utilise Mistral API pour répondre aux questions. Apertus reste facultatif.

| Fichier / dossier | Rôle |
|---|---|
| `app/main.py` | Point d’entrée et API principale |
| `app/config.py` | Configuration et variables d’environnement |
| `app/models.py` | Formats des requêtes et réponses |
| `app/api/` | Routes HTTP de l’agent |
| `app/clients/prometheus_client.py` | Interroger Mimir via son API compatible Prometheus/PromQL |
| `app/clients/loki_client.py` | Rechercher les logs dans Loki |
| `app/clients/kubernetes_client.py` | Lister les nœuds, pods et événements avec pagination bornée |
| `app/services/cluster_status.py` | Assembler l'état du namespace et l'inventaire du cluster |
| `app/services/global_health.py` | Évaluer et présenter la santé globale avec des règles déterministes |
| `app/services/diagnosis.py` | Produire le diagnostic |
| `app/services/llm_service.py` | Appeler le modèle de langage |
| `app/services/privacy.py` | Masquer les secrets et identifiants détectables |
| `app/prompts/` | Instructions du modèle |
| `tests/test_api.py` | Tests automatisés avec réponses Kubernetes/Mimir simulées |
| `tests/test_chat.py` | Tests du chat, streaming, contexte réel et erreurs des sources |
| `tests/test_mistral.py` | Tests HTTP simulés de Mistral, du SSE et des erreurs |
| `tests/test_cluster_inventory.py` | Tests simulés des nœuds, pods, événements et permissions |
| `tests/test_demo_cluster_health.py` | Tests simulés du parcours Open WebUI pour la démonstration A |
| `tests/integration/test_live_cluster.py` | Tests explicites sur le vrai cluster |
| `requirements.txt` | Dépendances Python |
| `Dockerfile` | Construire l’image Docker |
| `k8s/` | Déploiement Kubernetes |
| `docs/` | Documentation du projet |
| `scripts/` | Scripts de démarrage et validation |

## Flux implémenté

Collecte : Alloy → Mimir + Loki → FastAPI.

Utilisateur → Open WebUI → FastAPI → Kubernetes/Mimir/Loki → Mistral API → réponse

Open WebUI se connecte aux routes compatibles OpenAI de cet agent. À chaque tour,
celui-ci prépare une observation Kubernetes/Mimir/Loki, masque les valeurs sensibles
détectables et appelle Mistral API. Le modèle virtuel exposé est `kubeai` ;
`kubeai-apertus` reste accepté pour les conversations historiques. L'inventaire
des nœuds, pods de tous les namespaces et événements est exposé séparément sur
`/cluster/inventory`. Voir [la configuration Mistral](../docs/mistral-api.md) et
[le profil Apertus facultatif](../llm/README.md). L'intégration Mimir doit
encore être validée sur un cluster ; les tests simulés ne prouvent pas son ingestion.

La question « Quel est l'état de santé de mon cluster ? » utilise cet inventaire
dans le chat Open WebUI et dans `/diagnose`. Les faits et le niveau de santé sont
calculés avant Mistral ; les métriques Mimir agrégées couvrent les conteneurs de
pods de tous les namespaces. Le modèle ne peut ajouter qu'une priorité de lecture
étayée ; sans clé ou en cas d'échec, le résumé déterministe reste disponible.
Les règles et scénarios figurent dans [la démonstration A](../docs/demo-scenarios.md).

## Installation dans Minikube

Ces commandes concernent une machine adaptée. Sur Windows avec 8 Go, rester aux
tests légers ci-dessous ; le déploiement complet et le modèle local dépassent
le cadre de validation de ce poste.

Depuis la racine du dépôt, après démarrage de Minikube :

```bash
bash start.sh minikube
```

Le script construit `kubeai-agent:0.1.0` dans le runtime Minikube, applique les
manifests et redémarre l'agent pour charger l'image locale reconstruite.
Les dépendances Python et l'image de base sont figées dans `requirements.txt`
et le Dockerfile. Le Secret `kubeai-mistral` est préparé séparément ; le script
ne télécharge ni ne démarre de modèle. Une clé Mistral réelle est nécessaire
uniquement pour une génération effective, après validation des données transmises.

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
- Inventaire du cluster : <http://127.0.0.1:8000/cluster/inventory>
- Documentation interactive : <http://127.0.0.1:8000/docs>
- Contrat OpenAPI : <http://127.0.0.1:8000/openapi.json>
- État du moteur : <http://127.0.0.1:8000/llm/status>
- Modèles disponibles : <http://127.0.0.1:8000/v1/models>

`/cluster/status` conserve `scope: namespace` et les métriques du namespace
configuré. `/cluster/inventory` retourne `scope: cluster`, les nœuds, les pods
de tous les namespaces et les événements datés, sans métriques Mimir.
Les erreurs de chaque lecture et les limites de collecte sont explicites.

Le contrat détaillé et les comportements en cas de panne figurent dans
[`docs/api-contract.md`](../docs/api-contract.md).

## Configuration

Les valeurs déployées sont dans `k8s/agent-configmap.yaml`. Une modification
de ce ConfigMap nécessite un redémarrage de l'agent ; `./start.sh` s'en charge
sur Minikube. `.env.example` documente les variables mais n'est pas chargé.

| Variable | Défaut |
|---|---|
| `KUBERNETES_NAMESPACE` | `kubeai` |
| `MIMIR_URL` | `http://mimir.monitoring.svc:9009/prometheus` |
| `PROMETHEUS_URL` | Ancien nom accepté en repli, lorsque `MIMIR_URL` n'est pas définie |
| `KUBERNETES_API_URL` | `https://kubernetes.default.svc` |
| `KUBERNETES_TOKEN_FILE` | Token projeté du ServiceAccount |
| `KUBERNETES_CA_FILE` | Certificat CA projeté du cluster |
| `UPSTREAM_TIMEOUT_SECONDS` | `5` |
| `METRICS_MAX_AGE_SECONDS` | `60` |
| `LOKI_URL` | `http://loki.monitoring.svc.cluster.local:3100` |
| `LLM_PROVIDER` | `mistral-api` par défaut ou `local` (alias `mistral` et `apertus` admis) |
| `MISTRAL_BASE_URL` | `https://api.mistral.ai/v1` ; HTTPS obligatoire |
| `MISTRAL_MODEL` | `mistral-small-2603` |
| `MISTRAL_API_KEY` | Clé en environnement, vide par défaut |
| `MISTRAL_API_KEY_FILE` | `/var/run/secrets/kubeai-mistral/api-key` |
| `LLM_TIMEOUT_SECONDS` | `60` pour Mistral, `180` pour Apertus |
| `LLM_BASE_URL`, `LLM_MODEL`, `LLM_API_KEY_FILE` | Profil `local`; clé facultative selon le serveur |

La base Mimir contient `/prometheus` et le client ajoute `/api/v1/query`.
Le nom Python `PrometheusClient` et les requêtes PromQL restent conservés.

Le token est relu à chaque appel Kubernetes pour prendre en compte sa rotation.
L'agent dispose uniquement du droit `list` sur `nodes`, `pods` et `events` à
l'échelle du cluster. Il n'a aucun accès aux Secrets via l'API, aux logs via
Kubernetes, au proxy kubelet, à `exec` ou aux verbes de modification.
Les logs sont lus dans Loki et la clé Mistral est montée comme fichier Secret.
Le Service reste interne au cluster, sans authentification applicative, pour
ce profil local de développement. Ne pas exposer la nouvelle route hors d'un
environnement maîtrisé : les métadonnées et messages d'événements peuvent être
sensibles malgré le masquage au mieux.

## Développement hors du cluster

Python 3.10 minimum, Python 3.13 recommandé comme dans l'image. Depuis la racine :

```bash
python3.13 -m venv agent/.venv
agent/.venv/bin/python -m pip install -r agent/requirements.txt
```

Sur une machine adaptée disposant d'un cluster, lancer le proxy Kubernetes et le tunnel Mimir :

```bash
kubectl --context minikube proxy --port=8001
kubectl --context minikube port-forward -n monitoring service/mimir 9009:9009
```

Puis dans un troisième terminal, depuis `agent/` :

```bash
export KUBERNETES_API_URL=http://127.0.0.1:8001
export KUBERNETES_TOKEN_FILE=
export KUBERNETES_CA_FILE=
export MIMIR_URL=http://127.0.0.1:9009/prometheus
.venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

Pour tester le chat hors du cluster, lancer aussi un tunnel vers Loki et configurer
`LOKI_URL=http://127.0.0.1:3100` ainsi que `MISTRAL_API_KEY` dans l'environnement
privé ou `MISTRAL_API_KEY_FILE` avec un fichier hors du dépôt. Aucun appel réel
n'est nécessaire pour les tests unitaires. Les anciennes variables `LLM_*` de
connexion locale ne sont lues que si `LLM_PROVIDER=local` (l'alias `apertus`
reste accepté). Le backend ne télécharge pas de modèle et ne charge pas `.env`
automatiquement. Les profils et les commandes Windows/Mac figurent dans
[`docs/architecture-llm-profiles.md`](../docs/architecture-llm-profiles.md).

Le proxy utilise alors les droits de l'utilisateur kubectl ; les droits du
ServiceAccount sont utilisés uniquement dans le déploiement Kubernetes.

## Tests

Sous Windows, depuis la racine, avec l'environnement léger validé à l'étape 1 :

```powershell
Set-Location agent
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD = '1'
..\.venv\Scripts\python.exe -B -m pytest tests --ignore=tests/integration -v -p no:cacheprovider
```

Ces fichiers utilisent des réponses HTTP simulées et bloquent les sorties HTTP
réelles ; ils ne nécessitent aucun cluster, clé Mistral ou LLM.
Les tests live restent exclus.

Les tests HTTP synthétiques des démonstrations A/B/C démarrent le vrai
`app.main:app` avec Uvicorn sur une socket loopback éphémère. Seuls les clients
Kubernetes, Mimir, Loki et LLM sont remplacés par les fixtures existantes; aucun
endpoint de simulation n'est ajouté à l'application. Exécuter séparément :

```powershell
Set-Location agent
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD = '1'
..\.venv\Scripts\python.exe -B -m pytest tests\integration\test_local_http_demos.py -q -p no:cacheprovider
```

Les réponses testées proviennent uniquement de données synthétiques et ne
décrivent pas un cluster réel. Le processus Uvicorn s'arrête à la fin de chaque
cas.

Depuis `agent/` :

```bash
.venv/bin/python -m unittest discover -s tests -v
```

Les tests couvrent le contrat HTTP/OpenAPI, les métriques absentes, les erreurs
des dépendances, la pagination, la rotation du token, les pods terminés et en
CrashLoopBackOff, ainsi que l'exclusion des métriques d'anciens pods/conteneurs.
Les tests de l'inventaire couvrent Ready/NotReady, tous les namespaces, les
événements ordonnés, les redémarrages, les limites, les 403 et les timeouts.
Les tests de la démonstration A couvrent le parcours JSON/SSE, le bilan global,
les mesures Mimir et le repli sans Mistral.

Tests supplémentaires sur le cluster réel, depuis la racine du dépôt :

```bash
KUBEAI_LIVE_TEST_CONTEXT=minikube python3 agent/tests/integration/test_live_cluster.py
KUBEAI_LIVE_TEST_CONTEXT=minikube python3 agent/tests/integration/test_live_chat.py
```

Ils comparent l'inventaire de l'API à Kubernetes, attendent les mesures CPU/mémoire,
puis créent un pod temporaire qui termine avec le code 1 pour vérifier la détection
d'un état dégradé. Ce pod est supprimé à la fin. Ces tests ne tournent pas sans
la variable de contexte explicite.

Validation historique du profil Prometheus/Apertus, rapportée dans le dépôt avant
l'intégration Mimir : les 22 tests automatisés et les deux tests sur
Minikube ont réussi. L'API a renvoyé les deux pods réels avec leurs mesures, puis
signalé `health: degraded` pour le pod temporaire Failed. Ce pod a été supprimé.
Les deux tests de chat réel ont aussi réussi : diagnostic avec les noms des pods
observés et réponse diffusée en streaming par Apertus. Le contrôle visuel du
navigateur n'a pas été effectué ; la configuration Open WebUI est déployée.
Ce compte rendu historique ne valide pas le fonctionnement de Mimir. Les tests
live ne sont pas exécutés pendant l'intégration statique des étapes 2 et 3.
