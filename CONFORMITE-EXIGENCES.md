# KubeAI — conformité aux exigences du sujet



**Projet P5-G17 — état au 28 octobre 2026**



## Tableau de conformité

|Exigence|État|Reste à faire|
|-|-|-|
|**Déploiement sur infrastructure Cloud**|Non requis|Le projet est validé localement sur deux clusters ; le cloud n’est pas exigé par l’équipe|
|Cluster Kubernetes|✅ Local, deux clusters|—|
|Deux clusters séparés|✅ Cluster observé + supervision|—|
|Tous les namespaces|✅ Alloy, RBAC, Loki, statut|—|
|Stack Prometheus/Mimir + Loki|✅ Mimir + Loki + Alloy|—|
|Collecte/requêtes métriques|✅ KSM + cAdvisor + PromQL|—|
|Collecte/requêtes logs|✅ Alloy → Loki → LogQL|—|
|Récupération d’événements|✅ API K8s, inventaire borné|—|
|**Alertes**|Hors périmètre|Le professeur a retiré cette exigence|
|Choix d’un LLM|✅ Mistral API, Apertus optionnel|—|
|Open WebUI|✅ Modèle `kubeai`, auth|—|
|**Orchestration des outils / réponses structurées**|✅ Tool agent LangChain|Le modèle choisit les outils et arguments ; garde-fou de preuves à maintenir|
|**Trois démonstrations A/B/C**|✅ Benchmark FR/EN contrôlé|Captures FR/EN + rapport ; équivalence sémantique stricte non garantie|
|**Évaluation exactitude / fiabilité / sécurité / limites**|✅ 30/30 faits, max 15,76 s|Benchmark contrôlé, non représentatif d’une population|
|Journal d’audit + quotas|✅ Audit JSON + quota par clé|Limite en mémoire, une réplique|
|Tests automatisés|✅ 420 agent + 20 scripts|Tests shell non rejoués sous Windows|
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
