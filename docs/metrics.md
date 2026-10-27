# Métriques, logs et limites de KubeAI

Cette page résume ce que la pile collecte réellement, ce qu’elle ne collecte
pas, et comment le vérifier. Elle sert de référence pour la soutenance et pour
éviter les affirmations plus larges que les preuves disponibles.

## Ce qui est collecté par défaut

| Signal | Source | Fréquence | Utilisation |
|---|---|---|---|
| État des pods, nœuds, contrôleurs | kube-state-metrics (KSM) | 15 s | Santé A, inventaire, redémarrages |
| CPU/mémoire des conteneurs | cAdvisor via le proxy de l’API Kubernetes | 30 s | `/cluster/status`, B/C |
| Métriques applicatives opt-in | Pods de tous les namespaces annotés `prometheus.io/scrape: "true"` | 30 s | CPU du processus, compteurs HTTP de la démo |
| Logs de tous les pods | API Kubernetes → Alloy → Loki | continu | Chronologie B, preuves |
| Événements Kubernetes | API Kubernetes (lecture seule) | à la demande | Chronologie B, avertissements A |

Les manifests sont dans [config.alloy](../k8s/monitoring/config/config.alloy),
[mimir.yml](../k8s/monitoring/config/mimir.yml) et
[loki.yml](../k8s/monitoring/config/loki.yml).

## CPU/mémoire des conteneurs

**cAdvisor est scrapé** via le proxy de l’API Kubernetes
(`job="kubernetes-cadvisor"`). Alloy possède `list/watch` sur les nœuds et `get`
sur `nodes/proxy` ; ce droit élargit le RBAC mais reste en lecture seule. Les
séries `container_cpu_usage_seconds_total` et
`container_memory_working_set_bytes` alimentent `/cluster/status` et les
scénarios B/C.

Si ce scrape est indisponible, les champs redeviennent `null` et `data_status`
vaut `partial` : l’agent ne remplace jamais une mesure absente par zéro.

Les exécutions de démonstration antérieures à l’activation de cAdvisor
(22 octobre, environ 19:33 UTC) n’ont pas de séries conteneur ; il faut relancer
le scénario pour en générer.

### Latence et erreurs HTTP des applications non instrumentées

Le pod de démonstration expose ses propres compteurs HTTP. Une application
réelle qui n’expose pas `http_requests_total` et
`http_request_duration_seconds_bucket` n’a ni taux d’erreur ni latence p95
exploitable. L’agent le signale comme donnée absente, sans inventer de valeur.

### Traces et Tempo

Les traces distribuées et Tempo sont **hors périmètre** : le professeur a
demandé de ne pas y consacrer le temps restant. Aucun composant Tempo n’est
déployé.

### Alertes

Aucune source d’alertes dédiée n’est intégrée. Le rapport A contient
explicitement `alerts: not_configured` et une limite associée.

## Ce que l’agent renvoie

### `/cluster/status`

- Santé Kubernetes de tous les namespaces (`scope=cluster`, `namespace=*`) :
  `healthy`, `degraded`, `unknown` ou `empty` ;
- résumé des pods et redémarrages ;
- métriques CPU/mémoire via cAdvisor ; `null` si le scrape est indisponible ;
- `data_status: partial` lorsque des mesures manquent.

Une mesure absente n’est jamais remplacée par zéro.

### `/cluster/inventory`

Nœuds, pods de tous les namespaces et événements, sans métriques Mimir. Chaque
source a son propre statut (`ok`, `partial`, `unavailable`) ; une source en
erreur ne produit pas une liste vide présentée comme saine.

### Scénarios B et C

- B construit une chronologie à partir des événements, logs et séries Mimir
  disponibles.
- C calcule des hypothèses déterministes : `memory_oom`, `connectivity`,
  `cpu_latency`, `demo_app_cpu`, `demo_http_errors`, etc.
- Le LLM peut seulement signaler une priorité déjà étayée ; il n’écrit pas les
  faits et ne réordonne pas les hypothèses déterministes.

## Pod de démonstration

Le pod [app.py](../k8s/demo/incident/app.py) expose :

| Métrique | Type | Sens |
|---|---|---|
| `process_cpu_seconds_total` | counter | Temps CPU du processus de démonstration |
| `http_requests_total{status="200"}` | counter | Requêtes `/work` réussies |
| `http_requests_total{status="500"}` | counter | Requêtes `/work` en erreur pendant l’anomalie |
| `http_request_duration_seconds` | histogram | Latence des requêtes `/work` |

Le pod s’auto-génère du trafic vers `/work` toutes les 500 ms. Pendant
`anomaly_start`, les appels passent en 500 avec une latence plus élevée et le
pod émet un log `KUBEAI_DEMO_DEPENDENCY connection refused` après une tentative
réelle vers un port local fermé ; après `recovered`, les appels reviennent à 200.

L’agent interroge :

- le ratio d’erreurs 5xx :
  `rate(http_requests_total{status=~"5.."}[1m]) / rate(http_requests_total[1m])` ;
- la latence p95 :
  `histogram_quantile(0.95, rate(http_request_duration_seconds_bucket[1m]))`.

Une hausse nette pendant l’anomalie produit l’hypothèse `demo_http_errors` dans
[correlation.py](../agent/app/services/correlation.py). Le log
`connection refused` peut aussi renforcer l’hypothèse `connectivity` avec les
mêmes erreurs 5xx.

## Vérifier la collecte

### Capacités déclarées

```powershell
Invoke-RestMethod http://127.0.0.1:8000/capabilities
```

La route `/capabilities` interroge Mimir sans appeler le LLM et indique
`kube_state_metrics`, `cadvisor` et `application_http`. Elle ne renvoie ni clé
ni URL upstream. `application_http` vaut `available` si des séries
`http_requests_total` existent dans la dernière heure, même après la fin du pod.

### KSM et Loki

```powershell
python scripts/verify-observability.py --context docker-desktop
```

Le script vérifie KSM, cAdvisor, l’état du pod et les logs Loki. Il contrôle le
CPU/mémoire des conteneurs. `--require-container-metrics` rend leur absence
bloquante :

```powershell
python scripts/verify-observability.py --context docker-desktop --require-container-metrics
```

### Métriques du pod de démonstration

Après `scripts/run-demo-incident.ps1` :

```powershell
python scripts/verify-demo-metrics.py --context docker-desktop
```

Le script vérifie `up{job="kubernetes-pods-annotated"}`, les compteurs HTTP,
l’histogramme de latence, `process_cpu_seconds_total` et les marqueurs
`KUBEAI_DEMO_INCIDENT` dans Loki. Il lit la dernière heure, donc il fonctionne
même après la fin du pod. Il échoue si une série requise manque.

## cAdvisor : configuration active

Le dépôt scrape cAdvisor via le proxy de l’API Kubernetes :

- `job="kubernetes-cadvisor"` ;
- intervalle de 30 s ;
- RBAC `list/watch` sur `nodes` et `get` sur `nodes/proxy` dans
  [`rbac-cadvisor.yaml`](../k8s/monitoring/rbac-cadvisor.yaml) ;
- token et CA du ServiceAccount Alloy.

Le droit `nodes/proxy` est plus large qu’un accès métriques simple : il passe par
le proxy kubelet. Il reste en lecture seule et le collecteur n’est ni privilégié
ni monté sur l’hôte. C’est le compromis retenu pour obtenir les métriques
conteneur. Le DaemonSet cAdvisor n’est pas utilisé.

## Données réelles

Le projet ne dispose pas de données réelles HUG. Toutes les preuves des démos
sont synthétiques et datées ; les rapports le disent explicitement. Il ne faut
pas présenter les chiffres de démonstration comme des mesures de production.

## Formulations utiles pour la soutenance

- « cAdvisor est scrapé via le proxy API, avec un droit `nodes/proxy` en lecture
  seule ; `/cluster/status` renseigne le CPU/mémoire des conteneurs. »
- « Si le scrape cAdvisor tombe, les champs redeviennent `null` et
  `data_status: partial` ; aucune valeur n’est inventée. »
- « La démo C combine logs, CPU du processus, CPU conteneur et erreurs/latence
  HTTP du pod synthétique. »
- « Les traces et Tempo sont hors périmètre, conformément à la consigne. »
- « Les preuves sont synthétiques : aucune donnée HUG réelle n’est utilisée. »
- « Le LLM ne produit pas les faits ; il ne peut que signaler une priorité parmi
  les hypothèses calculées. »
