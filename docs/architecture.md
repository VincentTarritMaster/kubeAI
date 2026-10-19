# Architecture prévue

## Configuration du tronc commun

Open WebUI, l'agent et la collecte sont déployables par `./start.sh` (construction
de l'image locale puis `kubectl apply -k k8s/`).
Dans le namespace `monitoring`, Alloy collecte les métriques cAdvisor via le
proxy Kubernetes, l'état des workloads via kube-state-metrics et les logs via
l'API Kubernetes. Les métriques sont envoyées à Mimir, les logs à Loki.
Les données et le WAL des métriques Alloy reposent sur des PVC. Mimir conserve
les blocs sans purge automatique ; Loki garde sa rétention de 48 h. Voir
`k8s/monitoring/README.md` pour la validation et les limites de stockage.

Le projet collectif de Kevin, Vincent et Anass reprend le Mimir monolithique
de `kevin_version/k8s/monitoring/` dans les manifests principaux. Le PDF de Kevin
reste la référence d'architecture. Le circuit de collecte configuré est
**Alloy → Mimir + Loki → FastAPI**, sans serveur Prometheus autonome dans le rendu
Kustomize, sans traces ni Grafana à cette étape. Les protocoles Prometheus et
les requêtes PromQL sont conservés. L'agent expose `/cluster/status` avec l'état des pods du
namespace `kubeai` et leurs métriques CPU/mémoire. Son ServiceAccount ne peut que
lister les pods de ce namespace. L'accès aux logs, l'analyse par LLM et la
connexion à Open WebUI restent implémentés : le chat appelle Mistral API par défaut
après une collecte et une réduction des observations. Apertus, auto-hébergé sur
macOS via llama.cpp et Metal, reste une option explicite apportée par Vincent.
L'agent lit les logs dans Loki. Le contrat de chat compatible OpenAI garantit
la collecte avant chaque appel au modèle. La proposition antérieure avec un
serveur d'outils OpenAPI n'est pas le chemin utilisé pour ce profil.

L'intégration Mimir est une configuration à valider sur un vrai cluster. Les
vérifications statiques et tests avec réponses simulées ne démontrent pas
l'ingestion, la persistance ou le démarrage réel. Le poste Windows de 8 Go sert
aux tests légers ; le déploiement complet doit être essayé sur une machine
disposant de davantage de ressources. L'intégration Mistral a été testée avec
des réponses HTTP simulées ; aucun appel réel ni test d'hébergement régional
n'a été effectué.

## Composants

- **Open WebUI** : interface conversationnelle utilisée par la personne qui pose une question.
- **Agent IA** : API FastAPI qui assemble Kubernetes, Mimir et Loki et appelle Mistral API ; elle conserve également la route JSON `/cluster/status`.
- **Mimir** : stockage monolithique des métriques reçues d'Alloy, avec API compatible Prometheus/PromQL sur le port 9009.
- **Loki** : reçoit et recherche les logs.
- **Alloy** : découvre les pods et nœuds, collecte leurs logs vers Loki et les métriques vers Mimir.
- **kube-state-metrics** : expose l'état et les redémarrages des pods et l'état des workloads.
- **Modèle de langage** : Mistral Small 4 via API HTTPS par défaut. Apertus 8B Instruct quantifié reste une option locale explicite sur Mac.
- **Kubernetes** : exécute les composants et fournit l'état des pods à l'agent via un Role de lecture ; l'accès aux nœuds et événements reste à ajouter.

## Flux général

Alloy écrit les métriques sur `http://mimir.monitoring.svc:9009/api/v1/push`.
FastAPI utilise `MIMIR_URL=http://mimir.monitoring.svc:9009/prometheus` ; le
client existant `PrometheusClient` ajoute `/api/v1/query` à ce préfixe. La variable
historique `PROMETHEUS_URL` est acceptée uniquement en repli. Loki conserve son
endpoint de réception et son API LogQL.

Open WebUI transmet la conversation à `/v1/chat/completions` de l'agent. Celui-ci
lit les pods, leurs métriques et un extrait de logs, prépare un contexte borné,
puis appelle Mistral API. La réponse est retransmise en streaming avec le périmètre
et les sources. Aucune commande de remédiation n'est exécutée.

Les données Open WebUI sont stockées sur un PVC de 1 Gio. La clé Mistral est
fournie par Secret Kubernetes ou environnement privé, jamais par ConfigMap.
Les observations nécessaires quittent le cluster pour le fournisseur externe.
La région d'inférence et l'utilisation de données sensibles restent à valider
avec le groupe. Voir [la configuration Mistral](mistral-api.md).

Les anciens manifests et le PVC Prometheus sont conservés hors du déploiement
actif. Réappliquer Kustomize ne retire pas les anciennes ressources du cluster.
La validation et la transition opérationnelle sont décrites dans
[`k8s/monitoring/README.md`](../k8s/monitoring/README.md) ; aucun PVC ne doit être
supprimé pour cette transition.
