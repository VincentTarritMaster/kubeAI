# Agent kubeAI

Ce dossier contient le backend Python de kubeAI : une API **FastAPI** en lecture
seule, avec des fonctions typées **LangChain Core** que le modèle choisit à
partir de la conversation. Le modèle appelle Kubernetes, Mimir et Loki via ces
fonctions, puis rédige sa réponse dans la langue de la question. Mistral et
Apertus local sont configurables. Voir [les outils de l'agent](../docs/agent-tools.md).

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
| `app/services/llm_service.py` | Client LLM et validation des appels de fonctions natifs |
| `app/services/tool_agent.py` | Boucle de décision du modèle, exécution et réponse |
| `app/services/observability_tools.py` | Outils LangChain, schémas et lectures bornées |
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

Utilisateur → Open WebUI → modèle → appels de fonctions validés → Kubernetes/Mimir/Loki → modèle → réponse

Open WebUI se connecte aux routes compatibles OpenAI de cet agent. À chaque tour,
le modèle reçoit l'historique récent et les descriptions des outils. Il choisit
les lectures nécessaires, lit leurs résultats puis rédige sa réponse. Les valeurs
sensibles détectables sont masquées. Le modèle virtuel exposé est `kubeai` ;
`kubeai-apertus` reste accepté pour les conversations historiques. L'inventaire
des nœuds, pods de tous les namespaces et événements est exposé séparément sur
`/cluster/inventory`. Voir [la configuration Mistral](../docs/mistral-api.md) et
[le profil Apertus facultatif](../llm/README.md). L'intégration Mimir/Loki a été
exercée sur le cluster Docker Desktop du projet ; elle reste à revalider sur une
autre cible, et les tests simulés ne prouvent pas l'ingestion.

Une question de santé générale peut appeler `get_cluster_inventory`, tandis
qu'une investigation historique peut combiner recherche d'incident, logs,
métriques et événements. Aucun mot particulier n'est exigé dans la question.
Le mode `KUBEAI_CHAT_MODE=legacy` conserve l'ancien parcours déterministe pour
compatibilité ; les processus démarrent par défaut en mode `agent`.

La configuration de référence scrape cAdvisor via le proxy de l'API Kubernetes.
Le ServiceAccount Alloy possède `list/watch` sur les nœuds et `get` sur
`nodes/proxy` ; ce droit élargit le RBAC mais reste en lecture seule. `/cluster/status`
peut donc renseigner `cpu_cores` et `memory_working_set_bytes` ; les démos B/C
utilisent aussi `process_cpu_seconds_total` du pod annoté. Le script
[`verify-observability.py`](../scripts/verify-observability.py) vérifie KSM, cAdvisor
et Loki ; `--require-container-metrics` rend l'absence de métriques conteneur
bloquante. Le détail des signaux est dans [`docs/metrics.md`](../docs/metrics.md).

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
- Capacités réelles (KSM, cAdvisor, HTTP) : <http://127.0.0.1:8000/capabilities>
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

Les valeurs par défaut déployées sont dans `k8s/agent-configmap.yaml`.
`bash start.sh` lit le `.env` racine, crée les deux clusters via
`scripts/start-two-clusters.py`, applique le fournisseur et les Secrets
via `scripts/configure-runtime.py`, puis redémarre l'agent. Le kubeconfig cible
le cluster observé et `OBSERVED_CLUSTER` filtre les lectures Mimir/Loki.
Voir le [graphique de la topologie](../docs/two-clusters.md). Le processus Python
dans Kubernetes ne charge pas lui-même de `.env`. `.env.example` sert de modèle.

| Variable | Défaut |
|---|---|
| `KUBERNETES_ACCESS_MODE` | `in-cluster`; choisir `kubeconfig` pour un lancement local hors cluster |
| `KUBERNETES_NAMESPACE` | `kubeai` |
| `MIMIR_URL` | `http://mimir.monitoring.svc:9009/prometheus` |
| `PROMETHEUS_URL` | Ancien nom accepté en repli, lorsque `MIMIR_URL` n'est pas définie |
| `KUBERNETES_API_URL` | `https://kubernetes.default.svc` |
| `KUBERNETES_TOKEN_FILE` | Token projeté du ServiceAccount |
| `KUBERNETES_CA_FILE` | Certificat CA projeté du cluster |
| `KUBERNETES_KUBECONFIG_FILE` | `KUBECONFIG` ou `~/.kube/config`, en mode `kubeconfig` |
| `KUBERNETES_CONTEXT` | Contexte requis explicitement en mode `kubeconfig` |
| `KUBEAI_API_KEY` | Clé Bearer obligatoire pour l'API OpenAI compatible |
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
Le Service reste interne au cluster. Les routes `/v1/*` et `/diagnose`
exigent `Authorization: Bearer <KUBEAI_API_KEY>` ; la clé est fournie au
déploiement par le Secret Kubernetes `kubeai-api-auth` (clé `api-key`) et ne
doit pas être enregistrée dans Git. Les endpoints de santé restent sans
authentification pour les probes Kubernetes.

## Développement hors du cluster

Python 3.10 minimum, Python 3.13 recommandé comme dans l'image. Depuis la racine :

```bash
python3.13 -m venv agent/.venv
agent/.venv/bin/python -m pip install -r agent/requirements.txt
```

Si l'API Kubernetes du contexte n'est pas joignable directement depuis Windows
ou macOS, le proxy `kubectl` reste un mode de repli :

```bash
kubectl --context <contexte> proxy --port=8001
kubectl --context <contexte> -n monitoring port-forward --address 127.0.0.1 service/mimir 9009:9009
```

Puis dans un troisième terminal, depuis `agent/` :

```bash
export KUBERNETES_ACCESS_MODE=in-cluster
export KUBERNETES_API_URL=http://127.0.0.1:8001
export KUBERNETES_TOKEN_FILE=
export KUBERNETES_CA_FILE=
export MIMIR_URL=http://127.0.0.1:9009/prometheus
.venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

Pour tester le chat hors du cluster, lancer aussi un tunnel vers Loki et configurer
`LOKI_URL=http://127.0.0.1:3100` ainsi que `MISTRAL_API_KEY` dans l'environnement
privé ou `MISTRAL_API_KEY_FILE` avec un fichier hors du dépôt. En développement
local, le `.env` racine est chargé seulement si `KUBEAI_ENV=development` est
défini explicitement ; les variables du processus restent prioritaires. Ce mode
n'est pas activé dans Kubernetes. Aucun appel réel n'est nécessaire pour les
tests unitaires. Les anciennes variables `LLM_*` de
connexion locale ne sont lues que si `LLM_PROVIDER=local` (l'alias `apertus`
reste accepté). Le backend ne télécharge pas de modèle et ne charge pas `.env`
automatiquement. Les profils et les commandes Windows/Mac figurent dans
[`docs/architecture-llm-profiles.md`](../docs/architecture-llm-profiles.md).

En mode `kubeconfig`, le client Kubernetes officiel charge l'identité du
contexte sélectionné, ses mécanismes d'authentification et ses certificats.
La validation TLS reste obligatoire. L'agent ne lit pas le token projeté du pod
dans ce mode. L'accès est en lecture seule et exige `list` sur `nodes`, `pods`
et `events`. Les permissions viennent de l'identité du kubeconfig ; le
ServiceAccount `kubeai-agent` n'est utilisé qu'en mode `in-cluster`.

### Préparer un lancement local Windows (commandes non exécutées ici)

Choisir le contexte après avoir démarré le cluster séparément. Le contexte doit
exister dans le kubeconfig et être fourni explicitement ; l'application ne
change pas le contexte courant :

```powershell
$Context = 'docker-desktop'
kubectl config get-contexts $Context
kubectl --context $Context get --raw=/version
kubectl --context $Context auth can-i list nodes
kubectl --context $Context auth can-i list pods --all-namespaces
kubectl --context $Context auth can-i list events --all-namespaces
```

Dans deux fenêtres PowerShell, ouvrir les tunnels limités à l'interface locale.
Les ports correspondent aux Services `mimir` (9009) et `loki` (3100) dans le
namespace `monitoring` :

```powershell
$Context = 'docker-desktop'
kubectl --context $Context -n monitoring port-forward --address 127.0.0.1 service/mimir 9009:9009
```

```powershell
$Context = 'docker-desktop'
kubectl --context $Context -n monitoring port-forward --address 127.0.0.1 service/loki 3100:3100
```

Dans une troisième fenêtre, configurer et lancer FastAPI depuis la racine du
dépôt. `KUBERNETES_KUBECONFIG_FILE` est facultatif si le fichier est au chemin
par défaut. Le mode kubeconfig impose HTTPS et refuse un contexte absent ou un
kubeconfig qui désactive la validation TLS :

```powershell
$Context = 'docker-desktop'
$env:KUBEAI_ENV = 'development'
$env:LLM_PROVIDER = 'mistral-api'
$env:KUBERNETES_ACCESS_MODE = 'kubeconfig'
$env:KUBERNETES_CONTEXT = $Context
$env:KUBERNETES_KUBECONFIG_FILE = "$env:USERPROFILE\.kube\config"
$env:MIMIR_URL = 'http://127.0.0.1:9009/prometheus'
$env:LOKI_URL = 'http://127.0.0.1:3100'
Set-Location .\agent
..\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Dans un quatrième terminal, vérifier le backend, l'inventaire, les métriques
et les API locales Mimir/Loki sans déclencher de génération LLM :

```powershell
Invoke-RestMethod http://127.0.0.1:8000/health
Invoke-RestMethod http://127.0.0.1:8000/capabilities
Invoke-RestMethod http://127.0.0.1:8000/cluster/inventory
Invoke-RestMethod http://127.0.0.1:8000/cluster/status
Invoke-RestMethod 'http://127.0.0.1:9009/prometheus/api/v1/query?query=up'
Invoke-RestMethod 'http://127.0.0.1:3100/loki/api/v1/labels'
```

Pour arrêter chaque tunnel, utiliser `Ctrl+C` dans son terminal. `kubectl proxy`
reste une solution de repli si l'API du cluster n'est pas joignable directement
depuis Windows ; ce mode utilise les permissions de l'utilisateur `kubectl`.

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
