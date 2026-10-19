# Collecte locale des métriques et des logs

Cette pile est déployée dans le namespace `monitoring` par Kustomize. Elle est
incluse dans `kubectl apply -k k8s/` ; Helm n'est pas nécessaire.

| Composant | Version | Rôle |
|---|---|---|
| Mimir monolithique | 3.2.1 | Stockage et API compatible Prometheus/PromQL des métriques |
| Loki | 3.7.8 | Stockage et API LogQL des logs |
| Alloy | 1.20.1 | Découverte des pods/nœuds, collecte et expédition |
| kube-state-metrics | 2.20.0 | État, disponibilité et redémarrages des workloads |

Mimir est le stockage de métriques du tronc commun kubeAI de Kevin, Vincent et
Anass. Le déploiement actif utilise les manifests Kustomize du dépôt. Aucun
serveur Prometheus autonome n'est inclus ; les composants Alloy `prometheus.*`
et les requêtes PromQL restent nécessaires. Les configurations applicatives
Prometheus sont collectées uniquement par opt-in des pods du namespace `kubeai`
avec les annotations `prometheus.io/scrape: "true"` et
`prometheus.io/port: "<port>"` (chemin facultatif `prometheus.io/path`).

**État de validation :** configuration Mimir intégrée pour validation statique et
tests HTTP simulés ; fonctionnement réel sur Kubernetes non vérifié pendant cette
étape. La validation de bout en bout ci-dessous reste à effectuer collectivement
sur une machine adaptée. Le poste Windows de 8 Go est réservé aux tests
légers, sans démarrage de Docker, Minikube, Mimir ou LLM.

La syntaxe de `config/config.alloy` reste à valider avec le binaire officiel
Grafana Alloy : celui-ci n'est pas installé dans l'environnement de contrôle et
aucun téléchargement n'a été effectué. Le rendu Kustomize ne valide pas cette
syntaxe. Avant déploiement, exécuter `alloy validate` sur ce fichier avec la
version Alloy prévue (1.20.1), puis conserver le résultat dans le suivi de
validation.

## Circulation des données

```text
Kubelet/cAdvisor ──────┐
kube-state-metrics ───┼── Alloy ── remote_write ── Mimir ── PromQL ── FastAPI
Services monitoring ─┘
Logs des pods ─────────── Alloy ── push ────────── Loki ── LogQL ── FastAPI
```

Alloy découvre tous les nœuds et pods. Les métriques cAdvisor sont lues via le
proxy de l'API Kubernetes, avec token de ServiceAccount et validation du certificat.
La sélection conserve CPU, mémoire, réseau, disque et démarrage des conteneurs.
Les scrapes des workloads sont effectués toutes les 15 secondes, ceux des
services de monitoring toutes les 30 secondes. Aucun addon metrics-server n'est requis.

Les logs stdout/stderr sont suivis via l'API Kubernetes, sans conteneur privilégié
ni montage du système de fichiers de l'hôte. Loki reçoit les labels `namespace`,
`pod`, `container`, `node` et `job`. Une seule réplique Alloy collecte le cluster ;
augmenter simplement ce nombre dupliquerait les données. Pour plusieurs collecteurs,
configurer d'abord le clustering/sharding.

## Installation

Prérequis : un cluster démarré, kubectl et une StorageClass par défaut. Minikube
fournit la classe `standard`. Les commandes de cette section sont à exécuter
ultérieurement sur une machine adaptée, depuis la racine du dépôt :

```bash
minikube start --driver=docker
./start.sh
```

Le script cible explicitement `minikube`, applique tout le projet et attend les
déploiements. Il construit également l'image locale de l'agent avant son
déploiement. Pour un autre contexte déjà configuré : `./start.sh nom-du-contexte`,
avec une image d'agent préalablement publiée et configurée pour ce cluster.
La commande fonctionne également depuis un autre répertoire.

Pour installer uniquement le monitoring :

```bash
kubectl --context minikube apply -k k8s/monitoring/
kubectl --context minikube get pods,services,pvc -n monitoring
```

Les fichiers de configuration se trouvent dans `config/`. Kustomize génère des
ConfigMaps avec un suffixe calculé depuis leur contenu : réappliquer les manifests
après modification déclenche le redémarrage du composant concerné.

Pour un cluster qui héberge déjà Prometheus, `kubectl apply -k` ne supprime pas
les ressources retirées de Kustomize. Prévoir provisoirement les ressources des
deux backends, puis organiser l'arrêt de l'ancien serveur après validation de
Mimir. Ne pas utiliser `--prune`, ne pas supprimer les PVC ni les données. Les
données historiques Prometheus ne sont pas automatiquement transférées dans Mimir.

Vérification hors cluster, sans appliquer de ressource :

```powershell
kubectl kustomize .\k8s\monitoring\
kubectl kustomize .\k8s\
```

Ces commandes rendent les manifests. Elles ne valident ni les options du binaire
Mimir ni les permissions effectives ou la disponibilité du stockage.

## Validation de bout en bout

Python 3 et kubectl suffisent, sans bibliothèque Python supplémentaire :

```bash
python3 scripts/verify-observability.py --context minikube
```

Sous Windows, remplacer `python3` par `python` si nécessaire.
Le namespace applicatif `kubeai` doit exister ; il est créé par le déploiement racine.
Le script crée un pod temporaire BusyBox, qui émet un marqueur unique toutes les
cinq secondes. Il attend des scrapes récents et réussis, lit CPU/mémoire et l'état
du pod dans Mimir, puis recherche ce même marqueur dans Loki. Il affiche un
rapport JSON et supprime son pod, y compris après un échec. Le délai d'attente par
défaut est 180 secondes ; un échec renvoie un code de sortie non nul.

Pour vérifier un pod existant sans en créer :

```bash
python3 scripts/verify-observability.py --context minikube \
  --namespace kubeai --pod NOM_DU_POD
```

Ce pod doit être Ready et avoir émis des logs dans la dernière heure. Les mesures
CPU/mémoire concernent ses conteneurs applicatifs, sans le conteneur sandbox `POD`.
Le CPU est exprimé en cœurs, la mémoire en Mio de working set. Le premier résultat
CPU nécessite au moins deux échantillons.

Le script passe par le proxy des Services de l'API Kubernetes : aucun tunnel ni
accès Internet aux backends n'est requis. Il utilise les droits de l'utilisateur
kubectl ; l'agent déployé par le projet utilise son propre ServiceAccount.

### Historique du profil Prometheus, avant l'intégration Mimir

Le compte rendu ci-dessous était présent dans le dépôt au 18 octobre 2026. Il
concerne le profil précédent et ne démontre pas le fonctionnement de Mimir.

Sur Minikube avec Kubernetes 1.37.0, les quatre déploiements sont Ready et les
trois PVC sont Bound. La validation a réussi avec le pod temporaire (CPU, mémoire,
état et marqueur de log unique), puis avec le vrai pod Open WebUI. Le pod temporaire
a été supprimé. Ses logs ont été retrouvés après remplacement du pod Loki et ses
échantillons métriques après redémarrage de Prometheus. `./start.sh minikube` a été
rejoué avec succès. Les droits effectifs des ServiceAccounts ont été contrôlés :
lecture des ressources prévues, sans lecture des Secrets ni écriture des workloads.

Ce constat historique ne constitue pas un test de charge,
de rétention sur 48 h, de sauvegarde ou de haute disponibilité.

### Validation Mimir à réaliser sur un vrai cluster

Avec le contexte choisi et le monitoring appliqué, vérifier les déploiements :

```powershell
kubectl --context minikube rollout status deployment/mimir -n monitoring --timeout=5m
kubectl --context minikube rollout status deployment/loki -n monitoring --timeout=5m
kubectl --context minikube rollout status deployment/alloy -n monitoring --timeout=5m
kubectl --context minikube rollout status deployment/kube-state-metrics -n monitoring --timeout=5m
kubectl --context minikube get pods,services,pvc -n monitoring
kubectl --context minikube logs -n monitoring deployment/alloy --tail=100
kubectl --context minikube logs -n monitoring deployment/mimir --tail=100
```

Ouvrir un tunnel dans un autre terminal :

```powershell
kubectl --context minikube port-forward -n monitoring service/mimir 9009:9009
```

Puis lire la disponibilité et une série collectée par Alloy :

```powershell
Invoke-WebRequest 'http://127.0.0.1:9009/ready' -UseBasicParsing
$query = [uri]::EscapeDataString('up{job="kube-state-metrics"}')
Invoke-RestMethod "http://127.0.0.1:9009/prometheus/api/v1/query?query=$query"
python scripts/verify-observability.py --context minikube
```

Attendre HTTP 200 pour `/ready`, `status: success` et une valeur `1` pour `up`,
puis le rapport `status: success` du script. Celui-ci contrôle la fraîcheur des
scrapes, CPU/mémoire, disponibilité et logs Loki d'un même pod. Des logs Alloy
sans rejet de `remote_write` et ces séries fraîches attestent de l'ingestion.
Conserver les sorties comme preuve ; aucune de ces commandes de cluster n'a été
exécutée pour l'intégration statique. La persistance après remplacement d'un pod
Mimir nécessite encore un essai dédié ultérieur.

## Accès aux API

| Consommateur dans le cluster | URL de base |
|---|---|
| Écriture Alloy vers Mimir | `http://mimir.monitoring.svc:9009/api/v1/push` |
| Lecture PromQL (`MIMIR_URL`) | `http://mimir.monitoring.svc:9009/prometheus` |
| Logs | `http://loki.monitoring.svc.cluster.local:3100` |

Ces adresses figurent aussi dans `.env.example`, qui n'est pas chargé automatiquement.
`MIMIR_URL` est prioritaire ; `PROMETHEUS_URL` reste accepté en repli. Le client
Python `PrometheusClient` ajoute `/api/v1/query` à la base de lecture, ce qui donne
exactement `/prometheus/api/v1/query`. Le chemin d'écriture ne contient pas ce
préfixe. Le mode à locataire unique n'exige pas d'en-tête `X-Scope-OrgID`.

Pour explorer depuis la machine locale, garder chaque tunnel dans un terminal :

```bash
kubectl --context minikube port-forward -n monitoring service/mimir 9009:9009
kubectl --context minikube port-forward -n monitoring service/loki 3100:3100
```

Utiliser l'API Mimir <http://127.0.0.1:9009/prometheus/api/v1/query> avec le
paramètre `query` encodé, comme ci-dessus. Exemple de requête mémoire :

```promql
sum by (namespace, pod) (
  container_memory_working_set_bytes{namespace="kubeai",container!="",container!="POD"}
) / 1024 / 1024
```

Exemple CPU en cœurs :

```promql
sum by (namespace, pod) (
  rate(container_cpu_usage_seconds_total{namespace="kubeai",container!="",container!="POD"}[2m])
)
```

Exemple état et redémarrages :

```promql
kube_pod_status_ready{namespace="kubeai",condition="true"}
kube_pod_container_status_restarts_total{namespace="kubeai"}
```

Loki expose une API, pas une interface de recherche intégrée. Après le tunnel :

```bash
curl --get http://127.0.0.1:3100/loki/api/v1/query_range \
  --data-urlencode 'query={namespace="kubeai",container="open-webui"}' \
  --data-urlencode 'since=1h' --data-urlencode 'limit=10'
```

## Stockage, droits et limites

- Mimir : une réplique monolithique avec stratégie `Recreate`, PVC 10 Gio pour les
  blocs, TSDB et fichiers de travail. La configuration de conservation
  illimitée est maintenue (`compactor_blocks_retention_period: 0s`) pour ne pas
  introduire de purge des données. Le disque peut donc saturer ; surveiller
  l'espace, y compris le WAL et les blocs en cours. Décider d'une rétention après
  validation et sauvegarde. Le stockage filesystem avec StorageClass par défaut
  est destiné au développement, sans haute disponibilité.
- Loki : PVC 2 Gio, index TSDB v13 et chunks sur filesystem. Le compactor applique
  une rétention de 48 h, avec suppression différée. Cette rétention n'est pas un
  quota disque : surveiller l'espace disponible si le volume de logs augmente.
- Alloy : PVC 1 Gio pour son état et le WAL des métriques. La collecte via l'API
  peut relire des logs après un redémarrage ; elle ne garantit pas l'exactement une fois.
- Les PVC survivent au remplacement des pods. Les supprimer ou supprimer le
  namespace peut effacer les données. Le stockage Minikube n'est pas une sauvegarde.
- Mimir demande 500 millicœurs et 1 Gio, avec limites de 2 cœurs et 2 Gio. Les
  quatre composants de monitoring demandent au total 750 millicœurs et 1 536 Mio ;
  leurs limites cumulées atteignent 4,25 cœurs et 3 456 Mio. S'ajoutent Kubernetes,
  Docker, Open WebUI, l'agent, Windows et les applications de travail. Une pile
  complète n'est pas réaliste à valider confortablement sur le poste Windows
  de 8 Go. Ces valeurs initiales doivent être mesurées sur la machine de test ;
  elles ne garantissent pas l'absence d'OOM ni la tenue en charge.
- Les ServiceAccounts ne lisent pas les Secrets et ne modifient pas les workloads.
  Alloy peut lire les logs de tous les namespaces et accéder à `nodes/proxy` pour
  cAdvisor ; ce droit donne accès au proxy kubelet et doit rester réservé au collecteur.
- Les services sont internes (`ClusterIP`), sans authentification applicative.
  Les autres pods du cluster peuvent les joindre : ce profil est destiné au
  développement local sur cluster de confiance. Avant une exposition cloud, prévoir
  contrôle d'accès, NetworkPolicies adaptées au CNI, TLS et stockage dimensionné.

Grafana, alertes, traces et LLM ne font pas partie de cette installation.
L'agent est déployé par la racine `k8s/`, dans `kubeai`, et utilise l'API Mimir compatible Prometheus.
L'installation de `k8s/monitoring/` seule ne le déploie pas.

## Diagnostic

```bash
kubectl --context minikube get pods,pvc -n monitoring
kubectl --context minikube logs -n monitoring deployment/alloy --tail=50
kubectl --context minikube logs -n monitoring deployment/mimir --tail=50
kubectl --context minikube logs -n monitoring deployment/loki --tail=50
kubectl --context minikube get events -n monitoring --sort-by=.lastTimestamp
```

Une cible cAdvisor indisponible indique souvent un problème de droits ou d'accès
au kubelet. Des PVC Pending indiquent une StorageClass/provisioner absent ou un
problème de capacité. Un Loki fraîchement lancé peut rester non prêt pendant
quelques dizaines de secondes, le temps que son ingester rejoigne le ring.

## Références

- [Collecte Kubernetes avec Alloy](https://grafana.com/docs/alloy/latest/reference/components/loki/loki.source.kubernetes/)
- [Scrapes Prometheus avec Alloy](https://grafana.com/docs/alloy/latest/reference/components/prometheus/prometheus.scrape/)
- [Remote write avec Alloy](https://grafana.com/docs/alloy/latest/reference/components/prometheus/prometheus.remote_write/)
- [API HTTP Mimir](https://grafana.com/docs/mimir/latest/references/http-api/)
- [Stockage Loki sur filesystem](https://grafana.com/docs/loki/latest/operations/storage/filesystem/)
