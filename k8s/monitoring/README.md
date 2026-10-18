# Collecte locale des métriques et des logs

Cette pile est déployée dans le namespace `monitoring` par Kustomize. Elle est
incluse dans `kubectl apply -k k8s/` ; Helm n'est pas nécessaire.

| Composant | Version | Rôle |
|---|---|---|
| Prometheus | 3.15.0 | Stockage et API PromQL des métriques |
| Loki | 3.7.8 | Stockage et API LogQL des logs |
| Alloy | 1.20.1 | Découverte des pods/nœuds, collecte et expédition |
| kube-state-metrics | 2.20.0 | État, disponibilité et redémarrages des workloads |

Prometheus est le backend local retenu ici. Le document `docs/architecture_kevin.md`
décrit une proposition avec Mimir ; aucun Mimir n'est installé par ces manifests.
L'agent utilise `PROMETHEUS_URL` avec l'API Prometheus et pourra pointer
vers une API compatible Mimir si le groupe choisit de migrer.

## Circulation des données

```text
Kubelet/cAdvisor ──────┐
kube-state-metrics ───┼── Alloy ── remote_write ── Prometheus
Services monitoring ─┘
Logs des pods ─────────── Alloy ── push ────────── Loki
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
fournit la classe `standard`. Depuis la racine du dépôt :

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

## Validation de bout en bout

Python 3 et kubectl suffisent, sans bibliothèque Python supplémentaire :

```bash
python3 scripts/verify-observability.py --context minikube
```

Sous Windows, remplacer `python3` par `python` si nécessaire.
Le namespace applicatif `kubeai` doit exister ; il est créé par le déploiement racine.
Le script crée un pod temporaire BusyBox, qui émet un marqueur unique toutes les
cinq secondes. Il attend des scrapes récents et réussis, lit CPU/mémoire et l'état
du pod dans Prometheus, puis recherche ce même marqueur dans Loki. Il affiche un
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

### Vérification effectuée le 18 octobre 2026

Sur Minikube avec Kubernetes 1.37.0, les quatre déploiements sont Ready et les
trois PVC sont Bound. La validation a réussi avec le pod temporaire (CPU, mémoire,
état et marqueur de log unique), puis avec le vrai pod Open WebUI. Le pod temporaire
a été supprimé. Ses logs ont été retrouvés après remplacement du pod Loki et ses
échantillons métriques après redémarrage de Prometheus. `./start.sh minikube` a été
rejoué avec succès. Les droits effectifs des ServiceAccounts ont été contrôlés :
lecture des ressources prévues, sans lecture des Secrets ni écriture des workloads.

Ce constat valide le fonctionnement local ; il ne constitue pas un test de charge,
de rétention sur 48 h, de sauvegarde ou de haute disponibilité.

## Accès aux API

| Consommateur dans le cluster | URL de base |
|---|---|
| Métriques | `http://prometheus.monitoring.svc.cluster.local:9090` |
| Logs | `http://loki.monitoring.svc.cluster.local:3100` |

Ces adresses figurent aussi dans `.env.example`, qui n'est pas chargé automatiquement.

Pour explorer depuis la machine locale, garder chaque tunnel dans un terminal :

```bash
kubectl --context minikube port-forward -n monitoring service/prometheus 9090:9090
kubectl --context minikube port-forward -n monitoring service/loki 3100:3100
```

Prometheus propose une interface sur <http://127.0.0.1:9090>. Exemple mémoire :

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

- Prometheus : PVC 2 Gio, rétention 48 h ou 1 Go de blocs, selon la première limite
  atteinte. Le WAL et les blocs en cours occupent aussi de l'espace.
- Loki : PVC 2 Gio, index TSDB v13 et chunks sur filesystem. Le compactor applique
  une rétention de 48 h, avec suppression différée. Cette rétention n'est pas un
  quota disque : surveiller l'espace disponible si le volume de logs augmente.
- Alloy : PVC 1 Gio pour son état et le WAL des métriques. La collecte via l'API
  peut relire des logs après un redémarrage ; elle ne garantit pas l'exactement une fois.
- Les PVC survivent au remplacement des pods. Les supprimer ou supprimer le
  namespace peut effacer les données. Le stockage Minikube n'est pas une sauvegarde.
- Les quatre composants demandent au total 350 millicœurs et 768 Mio de mémoire ;
  leurs limites cumulées sont 3,25 cœurs et 2 176 Mio. Open WebUI s'ajoute à ces besoins.
- Les ServiceAccounts ne lisent pas les Secrets et ne modifient pas les workloads.
  Alloy peut lire les logs de tous les namespaces et accéder à `nodes/proxy` pour
  cAdvisor ; ce droit donne accès au proxy kubelet et doit rester réservé au collecteur.
- Les services sont internes (`ClusterIP`), sans authentification applicative.
  Les autres pods du cluster peuvent les joindre : ce profil est destiné au
  développement local sur cluster de confiance. Avant une exposition cloud, prévoir
  contrôle d'accès, NetworkPolicies adaptées au CNI, TLS et stockage dimensionné.

Grafana, alertes, traces et LLM ne font pas partie de cette installation.
L'agent est déployé par la racine `k8s/`, dans `kubeai`, et utilise cette API Prometheus.
L'installation de `k8s/monitoring/` seule ne le déploie pas.

## Diagnostic

```bash
kubectl --context minikube get pods,pvc -n monitoring
kubectl --context minikube logs -n monitoring deployment/alloy --tail=50
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
- [Stockage Loki sur filesystem](https://grafana.com/docs/loki/latest/operations/storage/filesystem/)
