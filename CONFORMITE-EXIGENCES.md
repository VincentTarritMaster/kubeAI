# KubeAI — conformité aux exigences du sujet

**Projet P5-G17 — état au 22 octobre 2026**

## Tableau de conformité

| Exigence                                                   | État                              | Reste à faire                                                                                                                                       |
| ---------------------------------------------------------- | --------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Déploiement sur infrastructure Cloud**                   | ❌ Non                            | C’est le point le plus important : le projet tourne sur Docker Desktop local, pas sur un cloud                                                      |
| Cluster Kubernetes                                         | ✅ Local                          | À refaire sur le cloud                                                                                                                              |
| Stack Prometheus/Mimir + Loki                              | ✅ Mimir + Loki + Alloy           | —                                                                                                                                                   |
| Collecte/requêtes métriques                                | ✅ KSM + cAdvisor + PromQL        | —                                                                                                                                                   |
| Collecte/requêtes logs                                     | ✅ Alloy → Loki → LogQL           | —                                                                                                                                                   |
| Récupération d’événements                                  | ✅ API K8s, inventaire borné      | —                                                                                                                                                   |
| **Alertes**                                                | ❌ `alerts=not_configured`        | Ajouter des règles d’alerte + Alertmanager, et le démontrer                                                                                         |
| Choix d’un LLM                                             | ✅ Mistral API, Apertus optionnel | —                                                                                                                                                   |
| Open WebUI                                                 | ✅ Modèle `kubeai`, auth          | —                                                                                                                                                   |
| **Orchestration des outils / réponses structurées**        | ⚠️ Partiel                        | Le backend orchestre de façon **déterministe** ; l’LLM ne fait que signaler une priorité. À documenter comme choix, ou ajouter une couche « tools » |
| **Trois démonstrations A/B/C**                             | ✅ Validées, preuves réelles      | Captures d’écran + rapport                                                                                                                          |
| **Évaluation exactitude / fiabilité / sécurité / limites** | ⚠️ Partielle                      | Les validations existent, mais pas de document consolidé `docs/evaluation.md`                                                                       |
| Lecture seule                                              | ✅ RBAC + aucune remédiation      | —                                                                                                                                                   |
| Application de test                                        | ✅ Pod synthétique                | —                                                                                                                                                   |
| Scénarios reproductibles                                   | ✅ Scripts + docs                 | —                                                                                                                                                   |
| Réponses avec preuves                                      | ✅ `/diagnose` + sources          | —                                                                                                                                                   |
| Traces / Tempo                                             | ✅ Hors périmètre                 | Rien, c’est la consigne du professeur                                                                                                               |
| Remédiation automatique                                    | ✅ Non implémentée                | Rien, c’est le choix read-only                                                                                                                      |
