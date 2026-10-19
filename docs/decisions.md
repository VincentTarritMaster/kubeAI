# Décisions à prendre en groupe

Les choix de collecte ci-dessous sont configurés dans `k8s/monitoring`.
L'intégration Mimir doit encore être validée sur un vrai cluster.
Les autres éléments restent ouverts et devront être validés collectivement.

| Sujet | État |
|---|---|
| Fournisseur et environnement Kubernetes cible | À décider |
| Modèle de langage | Mistral API configuré par défaut avec `mistral-small-2603` ; disponibilité du compte et de la région à confirmer avec une vraie clé |
| Hébergement local facultatif | Apertus 8B Instruct 2509 Q4_K_M conservé, activé explicitement par `LLM_PROVIDER=apertus` |
| Connexion Open WebUI | API compatible OpenAI de l'agent, modèle virtuel `kubeai` et alias historique `kubeai-apertus` ; collecte avant chaque génération |
| Région et données sensibles | Décision du groupe en attente ; ne transmettre que des données synthétiques ou autorisées avant validation |
| Collecte du tronc commun | Mimir monolithique 3.2.1, Loki 3.7.8, Alloy 1.20.1, kube-state-metrics 2.20.0 ; manifests Kustomize sans Helm |
| Stockage et rétention | PVC Mimir 10 Gio sans purge automatique des blocs (`0s`), Loki 2 Gio avec rétention 48 h, Alloy 1 Gio ; aucun nettoyage automatique d'éventuels anciens PVC Prometheus |
| API métrique | `MIMIR_URL=http://mimir.monitoring.svc:9009/prometheus` ; client `PrometheusClient` et PromQL conservés ; `PROMETHEUS_URL` en repli |
| Transition Prometheus → Mimir | Aucun serveur autonome dans le rendu ; manifests hérités retirés ; transition d'un cluster existant après validation, sans suppression automatique de ressources ou PVC |
| Poste Windows 8 Go | Tests unitaires et rendu statique uniquement ; validation Kubernetes Mimir réelle à réaliser sur une machine adaptée |
| Déploiement cloud | Étape ultérieure ; non implémentée dans l'intégration statique Mimir |
| Droits RBAC de l’agent | `ClusterRole` : `list` uniquement sur `nodes`, `pods`, `events` ; aucun Secret, `exec` ou verbe de modification ; logs via Loki |
| Remédiation automatique ou recommandations uniquement | À décider |

## Décisions techniques appliquées

- Les composants sont décrits par des manifests Kubernetes et Kustomize. Mimir
  est déployé en monolithe, Loki en instance unique, avec stockage filesystem et
  PVC ; aucun chart Helm ni Prometheus autonome n'est utilisé.
- Les limites et la configuration Mimir suivent le schéma 3.2.1 (`target: all`,
  `ingestion_rate` et `ingestion_burst_size`). Les anciennes valeurs Helm et les
  configurations Mimir 3.x rejetées ne sont pas des configurations de référence.
- Alloy collecte les métriques de nœuds et de kube-state-metrics, les logs via
  l'API Kubernetes et, sur opt-in, les métriques des pods `kubeai` Running portant
  `prometheus.io/scrape: "true"` et `prometheus.io/port`. Le scrape applicatif
  utilise un intervalle de 30 s et écrit vers la même destination Mimir.
- Les comptes de service et leurs RBAC sont gérés dans Kustomize ; ne pas
  combiner cette gestion avec celle d'un chart Helm pour les mêmes ressources.

## Retours d'essais historiques

Les essais initiaux ont révélé que le chart Loki par défaut cherchait un stockage
objet en mode distribué et que les charts Mimir essayés ajoutaient des composants
ou des configurations inadaptés à un petit cluster local. Le choix de manifests
explicites en Kustomize évite ces dépendances. D'anciens champs Mimir (`*_mb`,
`ingestion_rate_strategy`, clés dupliquées) ont échoué avec Mimir 3.2.1 ; ils ne
doivent pas être réintroduits. Les incidents de mémoire Minikube et de conflit
RBAC provenaient d'environnements et de méthodes d'installation antérieurs ; ils
ne définissent pas la configuration actuelle.

L'ancien Service Mimir `mimir-prometheus` exposait le même port HTTP et les mêmes
pods que le Service `mimir` ; il était redondant. Le Service actuel `mimir` sert
les clients PromQL sous `/prometheus` et le port gRPC 9095. Alertmanager n'est
pas activé dans la cible minimale ; son stockage et son endpoint n'ont donc pas
été repris. Le PVC Mimir filesystem de 10 Gio, les ports 9009/9095 et les
endpoints actifs sont conservés.
