# Décisions à prendre en groupe

Les choix locaux de collecte ci-dessous sont implémentés dans `k8s/monitoring`.
Les autres éléments restent ouverts et devront être validés collectivement.

| Sujet | État |
|---|---|
| Fournisseur et environnement Kubernetes cible | À décider |
| Modèle de langage et hébergement local | Apertus 8B Instruct 2509 Q4_K_M, conversion Unsloth ; llama.cpp b11669 natif macOS/Metal, sans API cloud |
| Connexion Open WebUI | API compatible OpenAI de l'agent, modèle virtuel `kubeai-apertus` ; collecte des données avant chaque génération |
| Collecte locale | Prometheus 3.15.0, Loki 3.7.8, Alloy 1.20.1, kube-state-metrics 2.20.0 ; manifests Kustomize sans Helm |
| Stockage et rétention locale | PVC Prometheus 2 Gio, Loki 2 Gio, Alloy 1 Gio ; rétention 48 h, limite de blocs Prometheus 1 Go |
| Évolution Mimir et déploiement cloud | À décider ; la proposition `architecture_kevin.md` ne décrit pas le déploiement local actuel |
| Droits RBAC de l’agent | En local : `list pods` dans `kubeai` uniquement ; logs via Loki, événements à ajouter |
| Remédiation automatique ou recommandations uniquement | À décider |
