# Validation des métriques HTTP et cAdvisor — 22 octobre 2026

## Contexte

Cette validation accompagne l’ajout des métriques HTTP au pod de démonstration,
la mise en cohérence de la collecte et l’activation de cAdvisor via le proxy de
l’API Kubernetes. Elle a été réalisée sur Windows, avec un poste de 8 Go dont la
mémoire libre est restée entre 0,2 et 0,76 Gio. Le cluster Docker Desktop du
projet était démarré ; un agent FastAPI temporaire a été utilisé sur le port
8001 pour les contrôles réels.

## Changements validés

### Démo C

Le pod [app.py](../k8s/demo/incident/app.py) expose désormais :

- `http_requests_total{status="200"}` et `{status="500"}` ;
- `http_request_duration_seconds` (histogramme) ;
- `process_cpu_seconds_total` (conservé).

Il s’auto-génère un trafic `/work` toutes les 500 ms. Pendant `anomaly_start`,
les appels passent en 500 avec une latence plus élevée et le pod émet un log
`KUBEAI_DEMO_DEPENDENCY connection refused` après une tentative réelle vers un
port local fermé ; après `recovered`, ils reviennent à 200.

L’agent interroge le ratio d’erreurs 5xx et la latence p95 dans
[prometheus_client.py](../agent/app/clients/prometheus_client.py), puis calcule
l’hypothèse `demo_http_errors` dans
[correlation.py](../agent/app/services/correlation.py). Le log
`connection refused` peut renforcer l’hypothèse `connectivity` avec les mêmes
erreurs 5xx.

### cAdvisor

cAdvisor est maintenant scrapé via le proxy de l’API Kubernetes, avec le job
`kubernetes-cadvisor`. Alloy reçoit `list/watch` sur les nœuds et `get` sur
`nodes/proxy` via
[rbac-cadvisor.yaml](../k8s/monitoring/rbac-cadvisor.yaml). Le script
[verify-observability.py](../scripts/verify-observability.py) vérifie KSM,
cAdvisor et Loki ; `--require-container-metrics` rend l’absence de métriques
conteneur bloquante. Voir [metrics.md](metrics.md).

### Capacités

La route `GET /capabilities` interroge Mimir et expose `kube_state_metrics`,
`cadvisor`, `application_http`, le fournisseur LLM et les limites, sans appeler
le LLM ni renvoyer de clé. Elle est couverte par
[test_capabilities.py](../agent/tests/test_capabilities.py).

### Tests

- [pytest.ini](../pytest.ini) permet de lancer les tests depuis la racine.
- Les tests live ont été alignés sur `kubeai` et le pied de page français.
- Les compteurs de requêtes Mimir ont été mis à jour pour les deux nouvelles
  séries HTTP.

## Résultats exécutés

```text
360 passed, 1 warning in 40.77s
6 passed, 1 warning in 6.44s   (tests HTTP loopback A/B/C)
```

Le cas `connectivity` + erreurs HTTP et les tests `/capabilities` sont donc
couverts et passent.

Un test local du handler HTTP du pod a produit :

```text
http_requests_total{status="200"} 2
http_requests_total{status="500"} 1
http_request_duration_seconds_bucket{le="+Inf"} 3
```

Ce test vérifie le compteur, le statut 500 et les buckets de l’histogramme.

### Validation réelle sur Docker Desktop

Le 22 octobre 2026, après le scénario, la commande suivante a réussi :

```powershell
python scripts\verify-demo-metrics.py --context docker-desktop
```

Résultat observé :

```json
{
  "status": "success",
  "phase": "Succeeded",
  "window": "1h",
  "markers": ["complete", "recovered", "anomaly_start", "normal", "..."],
  "metrics": {
    "up": 1.0,
    "http_requests_total": 1028,
    "http_5xx_total": 275,
    "http_duration_count": 1028,
    "process_cpu_seconds_total": 34.376344
  }
}
```

Alloy a donc bien scrapé le pod annoté : Mimir conserve `up`, les compteurs
HTTP, 24 séries de buckets d’histogramme et le CPU du processus. Le log
`KUBEAI_DEMO_DEPENDENCY connection refused` est présent dans Loki. Le script lit
la dernière heure, donc il fonctionne **après** la fin du pod.

### Validation cAdvisor

Le 22 octobre 2026, après application de
[rbac-cadvisor.yaml](../k8s/monitoring/rbac-cadvisor.yaml) et de la configuration
Alloy :

- `up{job="kubernetes-cadvisor"}` vaut `1` pour
  `desktop-control-plane` ;
- `container_cpu_usage_seconds_total` et
  `container_memory_working_set_bytes` sont ingérés ;
- `/cluster/status` renvoie `data_status: complete` et, pour `open-webui` :
  `metrics.status: available`, `cpu_cores ≈ 0,0079`, mémoire ≈ 295 Mo ;
- l’agrégat Mimir donne ≈ `0,324` cœur et ≈ `1,51 Go` de working set ;
- la réponse A affiche `14/15 Running (0 en erreur), 1 terminé(s)`, CPU des
  conteneurs ≈ `0,291` cœur et mémoire ≈ `1438,4 Mio` ;
- `/capabilities` renvoie `cadvisor: available` ;
- `verify-observability.py --require-container-metrics` réussit avec un pod
  temporaire : `container_metrics: available`, `cpu_cores ≈ 0,000414`,
  `memory_working_set_MiB ≈ 0,64`, sources KSM + cAdvisor + Loki.

La fenêtre de démonstration `17:53–17:55` a été collectée **avant** l’activation
de cAdvisor : ses séries conteneur sont donc absentes. Un nouveau scénario lancé
après l’activation les fournit : sur `19:36–19:41`, la réponse C contient
**80 observations**, dont **8 références CPU conteneur** et **9 références
mémoire conteneur**, avec `missing` vide et `connectivity` toujours en hypothèse
principale.

### Validation C réelle

Le 22 octobre 2026, un `/diagnose` réel a été exécuté sur la fenêtre
`17:52:00Z → 17:56:00Z` avec le pod `kubeai/kubeai-demo-incident` :

- `connectivity` est l’hypothèse principale, avec la confiance
  `étayée par log et erreurs HTTP, causalité non prouvée` ;
- `demo_app_cpu` et `demo_http_errors` sont aussi calculées, avec 5 preuves
  chacune ;
- la chronologie contient 52 observations sur cette fenêtre de quatre minutes ;
- le ratio d’erreurs 5xx atteint `1,0000` à `17:54:30Z` ;
- la latence p95 atteint `0,1950 s` pendant l’anomalie ;
- le log `connection refused` est cité dans les preuves ;
- les sources Kubernetes/Mimir/Loki sont `ok` ; le CPU/mémoire conteneur était
  absent pour cette fenêtre, collectée avant l’activation de cAdvisor.

Sur la fenêtre plus courte `17:53:00Z → 17:55:00Z`, la réponse B contient
**33 observations** et affiche désormais dans sa chronologie visible :

- `19:54:30 — Taux d’erreurs HTTP` avec un ratio 5xx de `1,0000` ;
- `19:54:30 — Latence HTTP p95` à `0,1950 s`.

La réponse C de cette même fenêtre expose les trois hypothèses
`connectivity`, `demo_app_cpu` et `demo_http_errors`, avec `connectivity` en
hypothèse principale. Même si Mistral sélectionne `demo_http_errors` comme
priorité, l’ordre déterministe n’est pas modifié : `connectivity` reste en tête,
et les autres hypothèses sont listées dans « Autres hypothèses calculées » avec
leurs preuves, dont le log `connection refused`.

### Dernier test complet (21:36–21:41)

Après redémarrage de Docker Desktop et activation de cAdvisor :

- `/capabilities` : `kube_state_metrics=available`, `cadvisor=available`,
  `application_http=available`, `mimir=available` ;
- A : `14/15 Running (0 en erreur), 1 terminé(s)`, CPU conteneur ≈ `0,310` cœur,
  mémoire ≈ `1324,1 Mio`, `9` avertissements récents ;
- ces 9 avertissements sont des sondes de readiness transitoires observées
  pendant le redémarrage de Kubernetes et le remplacement des pods Open WebUI :
  les cibles ont ensuite disparu ou sont redevenues Ready ;
- C : `connectivity` principale, `connection refused`, ratio 5xx
  `0,4690 → 1,0000`, latence p95 `0,1950 s`, CPU/mémoire conteneur présents
  dans les détails, aucune entrée « CPU/mémoire conteneur absent » ;
- `verify-observability.py --require-container-metrics` : succès,
  `container_metrics=available`, `cpu_cores=0,000458`,
  `memory_working_set_MiB=3,25`.

### Non-régression après intégration LangChain (22 octobre)

La chaîne LangChain a été exercée sur le chemin réellement utilisé par Open
WebUI :

- `/capabilities` : `llm.framework=langchain`, `cadvisor=available` ;
- A via `/v1/chat/completions` : `13/14 Running`, CPU ≈ `0,294` cœur,
  mémoire ≈ `1089,9 Mio`, sources `ok` ;
- A et C via SSE : frames `delta` reçues, terminaison `data: [DONE]` ;
- B via `/diagnose` : `76` observations, 5xx et latence présents ;
- C via `/diagnose` : `connectivity` principale, `76` observations,
  `connection refused`, ratio `0,4690 → 1,0000`, latence `0,1950 s` ;
  la seule donnée manquante est l’événement Kubernetes historique ;
- Open WebUI `0.11.4` répond `200` sur `/` et `/api/version` ;
- sans session, `/api/v1/models` et `/api/v1/auths/` renvoient `401` : le
  contrôle d’accès fonctionne ;
- la connexion stockée par Open WebUI vers `http://host.docker.internal:8000/v1`
  répond `200` pour `/models` ; l’ancienne URL de Service
  `kubeai-agent.kubeai.svc.cluster.local` est indisponible dans le mode Windows,
  et Open WebUI bascule sur la seconde connexion.

Le clic dans le navigateur n’a pas été automatisé (session utilisateur non
disponible), mais l’interface est servie et le chemin Open WebUI → agent est
testé de bout en bout, y compris le streaming SSE.

### Validation de `/capabilities`

Un second processus FastAPI a été lancé sur le port 8001 avec le kubeconfig de
lecture seule. Le 22 octobre 2026, `/capabilities` a répondu :

```text
kube_state_metrics : available
cadvisor           : available
application_http   : available
mimir               : available
llm.configured      : false
```

`application_http: available` est dû à la fenêtre d’une heure : le pod est
terminé, mais ses séries HTTP sont encore dans Mimir.

## Ce qui reste à valider

1. L’affichage final et les captures A/B/C dans Open WebUI.
2. Un passage sur une machine 32 Go pour confirmer cAdvisor et les trois démos.
3. Les quatre marqueurs `normal`, `anomaly_start`, `recovered`, `complete` dans
   la réponse B.
4. Les preuves restent synthétiques ; aucune donnée HUG réelle.

## Protocole de validation

Depuis la racine du dépôt, avec un cluster Docker Desktop démarré :

```powershell
.\scripts\stop.ps1
.\scripts\start.ps1
.\scripts\run-demo-incident.ps1
python scripts\verify-demo-metrics.py --context docker-desktop
```

Puis, dans Open WebUI :

> Analyse l’incident du dernier script de démonstration.

Puis :

> Pour ce même incident, corrèle les logs, le CPU et les erreurs HTTP.

Vérifier dans la réponse :

- la période et les marqueurs du scénario ;
- la présence de `connectivity`, `demo_http_errors` ou d’une preuve HTTP dans les hypothèses ;
- les valeurs de `http_requests_total` et de latence p95 ;
- l’absence de valeurs inventées lorsque les séries manquent.

## Limites de cette validation

- L’affichage visuel dans Open WebUI n’a pas été vérifié ; le chemin API
  `/diagnose` l’est.
- Les captures et chiffres de production ne sont pas disponibles.
- Les preuves restent synthétiques ; aucune donnée HUG réelle n’est utilisée.

## Fichiers concernés

- [app.py](../k8s/demo/incident/app.py)
- [prometheus_client.py](../agent/app/clients/prometheus_client.py)
- [correlation.py](../agent/app/services/correlation.py)
- [incident_timeline.py](../agent/app/services/incident_timeline.py)
- [presentation.py](../agent/app/services/presentation.py)
- [cluster_status.py](../agent/app/services/cluster_status.py)
- [routes.py](../agent/app/api/routes.py)
- [models.py](../agent/app/models.py)
- [test_correlation.py](../agent/tests/test_correlation.py)
- [test_capabilities.py](../agent/tests/test_capabilities.py)
- [test_cadvisor_manifest.py](../agent/tests/test_cadvisor_manifest.py)
- [llm_service.py](../agent/app/services/llm_service.py)
- [requirements.txt](../agent/requirements.txt)
- [langchain.md](langchain.md)
- [config.alloy](../k8s/monitoring/config/config.alloy)
- [rbac-cadvisor.yaml](../k8s/monitoring/rbac-cadvisor.yaml)
- [kustomization.yaml](../k8s/monitoring/kustomization.yaml)
- [verify-observability.py](../scripts/verify-observability.py)
- [verify-demo-metrics.py](../scripts/verify-demo-metrics.py)
- [pytest.ini](../pytest.ini)
- [metrics.md](metrics.md)
