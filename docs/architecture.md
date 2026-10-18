# Architecture prévue

## État implémenté en local

Open WebUI, l'agent et la collecte sont déployables par `./start.sh` (construction
de l'image locale puis `kubectl apply -k k8s/`).
Dans le namespace `monitoring`, Alloy collecte les métriques cAdvisor via le
proxy Kubernetes, l'état des workloads via kube-state-metrics et les logs via
l'API Kubernetes. Les métriques sont envoyées à Prometheus, les logs à Loki.
Les données et le WAL des métriques Alloy reposent sur des PVC. La rétention
des backends est de 48 h. Voir `k8s/monitoring/README.md` pour la validation.

Le document `architecture_kevin.md` reste une proposition distincte avec Mimir,
Tempo et Grafana. La pile locale implémentée utilise Prometheus, sans traces ni
Grafana. L'agent expose désormais `/cluster/status` avec l'état des pods du
namespace `kubeai` et leurs métriques CPU/mémoire. Son ServiceAccount ne peut que
lister les pods de ce namespace. L'accès aux logs, l'analyse par LLM et la
connexion à Open WebUI sont maintenant implémentés : le chat utilise Apertus
8B Instruct Q4_K_M, auto-hébergé sur macOS via llama.cpp et Metal. L'agent lit
les logs dans Loki. Le contrat de chat compatible OpenAI de l'agent garantit
la collecte avant chaque appel au modèle. La proposition antérieure avec un
serveur d'outils OpenAPI n'est pas le chemin utilisé pour ce profil.

## Composants

- **Open WebUI** : interface conversationnelle utilisée par la personne qui pose une question.
- **Agent IA** : API FastAPI qui assemble Kubernetes, Prometheus et Loki et appelle Apertus ; elle conserve également la route JSON `/cluster/status`.
- **Prometheus** : stocke les métriques reçues d'Alloy et expose l'API PromQL.
- **Loki** : reçoit et recherche les logs.
- **Alloy** : découvre les pods et nœuds, collecte leurs logs vers Loki et les métriques vers Prometheus.
- **kube-state-metrics** : expose l'état et les redémarrages des pods et l'état des workloads.
- **Modèle de langage** : Apertus 8B Instruct quantifié, servi sur le GPU du Mac par llama.cpp ; le serveur natif est protégé par une clé locale montée dans l'agent.
- **Kubernetes** : exécute les composants et fournit l'état des pods à l'agent via un Role de lecture ; l'accès aux nœuds et événements reste à ajouter.

## Flux général

Open WebUI transmet la conversation à `/v1/chat/completions` de l'agent. Celui-ci
lit les pods, leurs métriques et un extrait de logs, prépare un contexte borné,
puis appelle Apertus. La réponse est retransmise en streaming avec le périmètre
et les sources. Aucune commande de remédiation n'est exécutée.

Les données Open WebUI sont stockées sur un PVC de 1 Gio. Les poids du modèle,
le runtime et la clé locale sont dans le cache du projet, exclu de Git.
Le modèle est auto-hébergé sur le Mac, les autres services dans Minikube ; le
profil cloud devra adapter l'hébergement du modèle et le contrôle d'accès.
