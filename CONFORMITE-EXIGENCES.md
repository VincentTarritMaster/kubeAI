# KubeAI — conformité aux exigences du sujet



**Projet P5-G17 — état au 22 octobre 2026**



## Tableau de conformité

|Exigence|État|Reste à faire|
|-|-|-|
|**Déploiement sur infrastructure Cloud**|Non requis|Le projet est validé localement sur deux clusters ; le cloud n’est pas exigé par l’équipe|
|Cluster Kubernetes|✅ Local, deux clusters|—|
|Deux clusters séparés|✅ Supervisé + supervisé|—|
|Tous les namespaces|✅ Alloy, RBAC, Loki, statut|—|
|Stack Prometheus/Mimir + Loki|✅ Mimir + Loki + Alloy|—|
|Collecte/requêtes métriques|✅ KSM + cAdvisor + PromQL|—|
|Collecte/requêtes logs|✅ Alloy → Loki → LogQL|—|
|Récupération d’événements|✅ API K8s, inventaire borné|—|
|**Alertes**|Hors périmètre|Le professeur a retiré cette exigence|
|Choix d’un LLM|✅ Mistral API, Apertus optionnel|—|
|Open WebUI|✅ Modèle `kubeai`, auth|—|
|**Orchestration des outils / réponses structurées**|✅ Tool agent LangChain|Le modèle choisit les outils et arguments ; garde-fou de preuves à maintenir|
|**Trois démonstrations A/B/C**|✅ Validées FR/EN, preuves réelles|Captures d’écran + rapport|
|**Évaluation exactitude / fiabilité / sécurité / limites**|⚠️ Partielle|Les validations existent, mais pas de document consolidé `docs/evaluation.md`|
|Lecture seule|✅ RBAC + aucune remédiation|—|
|Application de test|✅ Pod synthétique|—|
|Scénarios reproductibles|✅ Scripts + docs|—|
|Réponses avec preuves|✅ `/diagnose` + sources|—|
|Traces / Tempo|✅ Hors périmètre|Rien, c’est la consigne du professeur|
|Remédiation automatique|✅ Non implémentée|Rien, c’est le choix read-only|

> **Décisions du professeur (27 octobre 2026) :** cloud non obligatoire tant que
> le projet tourne en local ; alertes abandonnées ; sécurité cloud hors
> périmètre ; équipe de 3 étudiants acceptée. Voir
> [`docs/points-amelioration.md`](docs/points-amelioration.md).
