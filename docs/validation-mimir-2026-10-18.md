# kubeAI — contrôle de l'intégration Mimir, étape 2

Projet collectif de Kevin, Vincent et Anass. Contrôle du 18 octobre 2026.
Branche : `integration/kubeai`. Base : `23260c91b45ed202e6aef61576263d6cbd1cb615`.

> Rapport historique de l'étape 2. Depuis ce contrôle, Mistral API et les
> scénarios A/B/C ont été intégrés et exercés ; l'état actuel est résumé dans le
> [README](../README.md#état-du-projet-et-démonstrations).

| Niveau | Résultat |
|---|---|
| Code modifié | Client/configuration/tests/scripts adaptés à Mimir, PromQL conservé |
| Configuration validée statiquement | Rendu Kustomize et YAML/liaisons vérifiés hors ligne |
| Tests unitaires | 36 réussis : les 27 existants adaptés + 9 nouveaux cas |
| Fonctionnement réel Kubernetes | **Non vérifié** : aucun déploiement, ingestion ou stockage réel testé |

## 1. Résumé des modifications réalisées

Le déploiement cible contient désormais **Alloy → Mimir pour les métriques** et **Alloy → Loki pour les logs**, consommés par FastAPI. Mimir 3.2.1 est configuré en une seule instance monolithique, sans Kafka ni stockage objet externe. Aucun serveur Prometheus autonome ne figure dans le rendu cible.

Le backend garde `PrometheusClient`, son champ interne `prometheus_url` et les requêtes PromQL existantes. Sa variable active devient `MIMIR_URL`, avec `PROMETHEUS_URL` accepté en repli. La source JSON et les erreurs de métriques sont maintenant identifiées par `mimir`, à la place de `prometheus` : les consommateurs de `sources.prometheus` devront lire `sources.mimir`.

Le démarrage et la validation réelle existants ont été adaptés pour attendre/interroger Mimir. Le fournisseur LLM reste provisoirement Apertus ; aucune connexion Mistral n'a été ajoutée.

## 2. Liste exacte des fichiers créés ou modifiés

### Créés pour l'étape 2

| Fichier | Rôle |
|---|---|
| `k8s/monitoring/mimir.yaml` | PVC, Deployment monolithique et Service Mimir |
| `k8s/monitoring/config/mimir.yml` | Configuration Mimir commune |
| `agent/tests/test_mimir.py` | 9 cas HTTP simulés et configuration d'environnement |
| `docs/validation-mimir-2026-10-18.md` | Présent rapport |

### Modifiés pour l'étape 2

| Fichier | Modification ciblée |
|---|---|
| `.env.example` | MIMIR_URL active, sans secret |
| `k8s/agent-configmap.yaml` | Base de lecture Mimir |
| `agent/app/config.py` | Défaut Mimir ; MIMIR_URL prioritaire et fallback PROMETHEUS_URL |
| `agent/app/clients/prometheus_client.py` | Chemin relatif api/v1/query ; erreurs/source Mimir |
| `agent/app/services/cluster_status.py` | Sources et statuts métriques nommés mimir |
| `agent/app/api/routes.py` | Description OpenAPI de readiness actualisée |
| `agent/tests/test_api.py` | Assertions de chemin API et source Mimir ; logique des tests conservée |
| `agent/tests/test_chat.py` | Assertions de collecte et source Mimir |
| `k8s/monitoring/kustomization.yaml` | Mimir remplace Prometheus dans les ressources et ConfigMaps actives |
| `k8s/monitoring/config/config.alloy` | Destination remote_write et cible d'autosurveillance Mimir |
| `start.sh` | Attente de deployment/mimir |
| `scripts/verify-observability.py` | Lecture PromQL sur Mimir ; logs Loki conservés |
| `README.md` | Circuit commun, limites Windows et validation à effectuer |
| `agent/README.md` | MIMIR_URL, comportement, tests et validation réelle |
| `docs/architecture.md` | Architecture commune avec Mimir |
| `docs/decisions.md` | Décisions Mimir et limites de cette étape |
| `docs/api-contract.md` | URL, source mimir et comportement partiel |
| `k8s/monitoring/README.md` | Configuration, ressources, conservation, commandes de validation réelle |

À la date de ce contrôle (18 octobre 2026), le dossier de contributions initiales `kevin_version/`, le PDF original, `k8s/kustomization.yaml`, le RBAC, les manifests/configurations Loki et les fichiers Prometheus étaient inchangés. Ce rapport conserve cet état historique ; le dossier de contributions a depuis été retiré après intégration.

Fichiers de contrôle locaux ignorés : `.cache/mimir-validation/rendered.yaml` et `check-static.py`. Ils contiennent le rendu et ses assertions statiques, sans donnée de cluster. PyYAML 6.0.3 a été ajouté uniquement à `.venv` pour lire les YAML ; aucune dépendance runtime versionnée n'a changé.

## 3. Configurations Kevin réutilisées

Source consultée pour ce contrôle : `kevin_version/k8s/monitoring/mimir-deployment.yaml`, conservée à cette date. Le manifeste commun a depuis été validé et la copie historique retirée.

Paramètres repris : image 3.2.1, mode `target: all`, locataire unique, filesystem sous `/data/mimir`, TSDB/WAL et synchronisation, compactor, ruler, anneaux en mémoire, réplication 1, ports 9009/9095, PVC 10 Gi, requête 500m/1 Gi et limites 2 CPU/2 Gi.

Adaptations au projet commun :

- ConfigMap générée par Kustomize à partir de `config/mimir.yml`, sans ConfigMap concurrente.
- Un seul Service `mimir` ; l'alias redondant `mimir-prometheus` n'est pas ajouté.
- StorageClass par défaut du cluster, au lieu de forcer `standard` dans le nouveau manifeste.
- Stratégie `Recreate` avec un seul PVC RWO ; probes et contexte de sécurité alignés sur les composants racine.
- Écritures de l'activity tracker dirigées sous le PVC pour permettre la racine en lecture seule.
- `ingest_storage.enabled: false` et préfixe de lecture `/prometheus` explicites.
- Conservation sans purge par âge (`compactor_blocks_retention_period: 0s`), comme le comportement Kevin initial.

Le fichier Kevin `mimir-values.yaml` n'est pas repris : ses réglages Helm ne correspondent pas au déploiement direct retenu, notamment ses champs `ingestion_rate_mb` et `ingestion_burst_size_mb`. Les sections Alloy racine sont conservées pour garder le proxy cAdvisor et la vérification TLS déjà développés.

Sources de compatibilité : [configuration officielle](https://grafana.com/docs/mimir/latest/configure/configuration-parameters/), [ingestion dans la version 3.2.1](https://raw.githubusercontent.com/grafana/mimir/mimir-3.2.1/pkg/storage/ingest/config.go), [activity tracker 3.2.1](https://raw.githubusercontent.com/grafana/mimir/mimir-3.2.1/pkg/util/activitytracker/activity_tracker.go).

## 4. Anciennes configurations Prometheus désactivées

Retirés uniquement de la Kustomization active lors du contrôle du 18 octobre :

- Ressources de `k8s/monitoring/prometheus.yaml` : Deployment/Service `prometheus` et PVC `prometheus-data`.
- ConfigMap `prometheus-config`, issue de `k8s/monitoring/config/prometheus.yml`.
- Destination Alloy `:9090/api/v1/write` et autoscrape du serveur Prometheus.

À la date du contrôle, les deux fichiers historiques étaient encore présents et identiques à HEAD. Ils ont depuis été supprimés du dépôt ; aucun PVC ni donnée de cluster n'a été supprimé. Les données historiques Prometheus ne sont pas automatiquement transférées vers Mimir.

**Un futur `kubectl apply -k` ne supprimera pas les anciennes ressources d'un cluster existant.** L'arrêt de cet ancien serveur devra être organisé après validation réelle, en préservant son PVC. Aucun `--prune`, `delete` ou arrêt d'ancienne ressource n'a été exécuté.

Le composant Alloy garde son identifiant `prometheus.remote_write "local"` et son PVC/WAL existant. Seules deux valeurs ont changé dans `config.alloy` ; les autres lignes sont identiques à HEAD, notamment tout le circuit Loki.

## 5. Tests exécutés et résultats

| Exécution réellement effectuée | Résultat |
|---|---|
| Avant modification : 27 tests API/chat existants | 27 réussis, 0 échoué, 0 ignoré |
| Après adaptation : 27 existants + 9 nouveaux | 36 réussis, 0 échoué, 0 ignoré |
| Exécution finale depuis agent/, après toutes les modifications de code | **36 réussis, 0 échoué, 0 ignoré, 19,24 s** |
| Compilation syntaxique en mémoire app/tests/scripts | 28 fichiers Python valides |
| `pip check` | Aucun conflit |
| `bash -n start.sh` | Code retour 0, script non exécuté |
| Script verify-observability avec subprocess simulé | 8 requêtes métriques Mimir et 1 requête Loki vérifiées ; code 0 |
| Contrôle du rendu/YAML/liaisons | Réussi : 31 ressources rendues, 20 fichiers YAML sources lus |
| `git diff --check` | Réussi |

Un avertissement de dépréciation Starlette/httpx, déjà présent à l'étape 1, subsiste. Les 5 tests live sont exclus de la collecte, **non exécutés**. Aucun test Kubernetes, appel LLM ou appel Mistral réel.

Reproduction des tests depuis la racine :

```powershell
Push-Location agent
try {
    $env:PYTEST_DISABLE_PLUGIN_AUTOLOAD='1'
    ..\.venv\Scripts\python.exe -B -m pytest tests/test_api.py tests/test_chat.py tests/test_mimir.py -v -p no:cacheprovider
} finally {
    Pop-Location
}
.\.venv\Scripts\python.exe -m pip check
```

## 6. Vérification des chemins API Mimir

| Usage | URL configurée |
|---|---|
| Écriture Alloy | `http://mimir.monitoring.svc:9009/api/v1/push` |
| Base backend MIMIR_URL | `http://mimir.monitoring.svc:9009/prometheus` |
| Lecture HTTP finale testée | `http://mimir.monitoring.svc:9009/prometheus/api/v1/query` |
| Santé Mimir pour essai réel | `http://mimir.monitoring.svc:9009/ready` |

Le client ajoute le chemin relatif `api/v1/query` à la base. Les mocks HTTPX ont vérifié le chemin effectivement envoyé avec base sans slash, avec slash et avec préfixe personnalisé, sans duplication. Les tests API/chat le contrôlent aussi. Les paramètres PromQL, `time` et `timeout` sont préservés.

La priorité `MIMIR_URL` > `PROMETHEUS_URL` > défaut est testée avec environnement isolé. Les erreurs HTTP et les résultats invalides restent explicites. Le mode configuré `multitenancy_enabled: false` ne requiert pas de tenant `X-Scope-OrgID`. Ces chemins sont ceux de [l'API officielle Mimir](https://grafana.com/docs/mimir/latest/references/http-api/) ; le [code 3.2.1](https://raw.githubusercontent.com/grafana/mimir/mimir-3.2.1/pkg/api/api.go) confirme le préfixe.

## 7. Résultat Kustomize et validité des manifests

Outils présents : kubectl client 1.36.1, Kustomize intégré 5.8.1.

```powershell
kubectl version --client -o yaml
kubectl kustomize 'C:\Users\anass\OneDrive\Documents\GitHub\kubeAI\k8s'
```

Le premier rendu limité par le bac à sable a échoué sur l'accès au chemin ; le rendu avec accès autorisé a réussi, sans connexion à un cluster.

Résultat : **31 ressources**, dont six Deployments (`alloy`, `mimir`, `loki`, `kube-state-metrics`, `kubeai-agent`, `open-webui`). Aucun Deployment, image serveur, Service, ConfigMap ou PVC Prometheus autonome dans ce rendu.

Les assertions ont vérifié les noms/namespaces uniques, tous les sélecteurs et ports de Services, les références des ConfigMaps générées, les PVC montés et la cohérence entre le Service Mimir, la configuration Alloy et celle de FastAPI. Les 20 fichiers YAML de `k8s/` et le YAML Mimir embarqué ont été lus par PyYAML. Le RBAC existant couvre la découverte pods/nodes et la lecture pods/log et nodes/proxy utilisées par Alloy.

**Limite :** rendu, syntaxe YAML et cohérence structurelle vérifiés. Aucun validateur de schéma Kubernetes complet, serveur API ou binaire Mimir/Alloy n'a chargé les configurations. Aucun démarrage ni déploiement réel vérifié.

## 8. Risques de ressources, stockage et compatibilité

- **Mémoire :** Mimir demande 1 Gi et a une limite de 2 Gi ; ce dimensionnement repris de Kevin est à mesurer, ce n'est pas un minimum universel ni une garantie contre les OOM. Les quatre composants de monitoring demandent 750m/1 536 Mi, avec limites cumulées de 4,25 CPU/3 456 Mi. Kubernetes, Open WebUI, l'agent, Windows et les outils s'ajoutent. Ne pas tester la pile complète sur ce poste 8 Go.
- **Disque :** PVC Mimir 10 Gi, sans purge par âge. Le WAL, les blocs et la compaction peuvent saturer le volume. Décider d'une rétention et de sauvegardes après validation des données. Le stockage doit offrir les opérations POSIX nécessaires ; ce profil filesystem mono-instance n'est pas une haute disponibilité. [Exigences Grafana](https://grafana.com/docs/mimir/latest/set-up/hardware-requirements/)
- **PVC existant :** si `mimir-data` existe déjà, comparer sa StorageClass, sa taille et ses droits avant application ; ses champs immuables peuvent demander une adaptation du manifeste. Préserver le PVC et ses données, sans recréation aveugle.
- **Compatibilité :** image 3.2.1 conservée, paramètres relus dans la documentation et le code officiel ; image non téléchargée et configuration non validée par le binaire.
- **Collecte :** nœuds/pods et kube-state-metrics restent collectés par Alloy ; l'agent garde son périmètre namespace actuel. L'ingestion réelle, les certificats, permissions effectives, la fraîcheur et la persistance attendent les essais.
- **API :** `sources.mimir` remplace `sources.prometheus`. Le nom du client et l'ancienne variable d'URL restent compatibles.
- **Transition :** un ancien serveur Prometheus peut encore occuper des ressources sur un cluster existant ; sa conservation ne figure pas dans le dimensionnement cible.

## 9. Commandes pour tester Alloy → Mimir sur un vrai cluster

**À exécuter ultérieurement par Kevin ou Vincent sur une machine adaptée. Aucune commande de cette section n'a été exécutée ici.** Choisir le bon contexte et examiner/sauvegarder les volumes existants avant application.

Depuis la racine, uniquement pour déployer le monitoring :

```powershell
$clusterContext = 'minikube'
kubectl --context $clusterContext get nodes
kubectl --context $clusterContext get pvc -n monitoring
kubectl --context $clusterContext apply -k .\k8s\monitoring\
kubectl --context $clusterContext rollout status deployment/mimir -n monitoring --timeout=10m
kubectl --context $clusterContext rollout status deployment/alloy -n monitoring --timeout=5m
kubectl --context $clusterContext rollout status deployment/loki -n monitoring --timeout=5m
kubectl --context $clusterContext rollout status deployment/kube-state-metrics -n monitoring --timeout=5m
kubectl --context $clusterContext get pods,services,pvc -n monitoring
kubectl --context $clusterContext logs -n monitoring deployment/alloy --tail=100
kubectl --context $clusterContext logs -n monitoring deployment/mimir --tail=100
```

Dans un autre terminal :

```powershell
kubectl --context minikube port-forward -n monitoring service/mimir 9009:9009
```

Puis, sans LLM :

```powershell
Invoke-WebRequest 'http://127.0.0.1:9009/ready' -UseBasicParsing
$query = [uri]::EscapeDataString('up{job="kube-state-metrics"}')
Invoke-RestMethod "http://127.0.0.1:9009/prometheus/api/v1/query?query=$query"
python scripts/verify-observability.py --context minikube
```

Le script exige que le namespace `kubeai` existe déjà ; le rendu racine le crée. Pour un autre contexte, remplacer aussi `minikube` dans les deux derniers blocs. Le script de vérification crée/supprime un pod temporaire ou accepte `--pod NOM` pour un pod existant.

Résultats attendus : HTTP 200 pour /ready ; une série `up` fraîche valant 1 ; rapport du script `status: success` avec CPU/mémoire et logs du même pod ; absence de rejets remote_write dans Alloy. Conserver ces sorties comme preuve. Un test de persistance après remplacement de pod Mimir reste à planifier séparément. L'agent devra ensuite être reconstruit/redéployé sur cette machine pour charger les changements Python et MIMIR_URL ; les instructions de déploiement racine sont dans le README.

## 10. Résumé de git status et git diff

- Branche exclusive de travail : `integration/kubeai`, HEAD toujours `23260c9`.
- **18 fichiers suivis modifiés : +259 lignes, -99 lignes**.
- **4 nouveaux fichiers pour l'étape 2**, non suivis, dont ce rapport.
- Le rapport d'étape 1 reste un cinquième fichier non suivi, inchangé.
- Aucun fichier supprimé. Aucun changement dans `main`, `AnassNDRI`, `kevin` ou `feat/llm-local` ; aucun commit/push/fusion.
- `git diff --check` réussit ; les nouveaux fichiers ne sont pas compris dans le --stat des fichiers suivis.

Intégrité du rapport étape 1 : SHA-256 `7933c34e7179333c9b44a6843eb02df04abc667076524bcd4fa6d2997eb5d3c1`.
Intégrité du PDF original : SHA-256 `418aea3d0cb4797d1abc3e359d0421261994b23befc9521a1fd1a7294005e2f8`.

**Arrêt après intégration statique et tests légers.** Mistral API, LangChain, événements, démonstrations, Grafana et cloud restent pour les étapes autorisées ultérieurement.
