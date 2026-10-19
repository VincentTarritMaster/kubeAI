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
| Stockage et rétention | PVC Mimir 10 Gio sans purge automatique des blocs (`0s`), Loki 2 Gio avec rétention 48 h, Alloy 1 Gio ; anciens PVC Prometheus conservés |
| API métrique | `MIMIR_URL=http://mimir.monitoring.svc:9009/prometheus` ; client `PrometheusClient` et PromQL conservés ; `PROMETHEUS_URL` en repli |
| Transition Prometheus → Mimir | Serveur Prometheus retiré du rendu cible ; anciens manifests conservés ; transition d'un cluster existant après validation, sans suppression de PVC |
| Poste Windows 8 Go | Tests unitaires et rendu statique uniquement ; validation Kubernetes Mimir à réaliser sur une machine adaptée par Kevin ou Vincent |
| Déploiement cloud | Étape ultérieure ; non implémentée dans l'intégration statique Mimir |
| Droits RBAC de l’agent | En local : `list pods` dans `kubeai` uniquement ; logs via Loki, événements à ajouter |
| Remédiation automatique ou recommandations uniquement | À décider |
