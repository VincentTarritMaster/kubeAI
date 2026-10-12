# Agent KubeAI

Ce dossier prépare la future API d’analyse. Les fichiers de code sont volontairement vides : aucune fonctionnalité de l’agent n’est implémentée.

| Fichier / dossier | Rôle |
|---|---|
| `app/main.py` | Point d’entrée et API principale |
| `app/config.py` | Configuration et variables d’environnement |
| `app/models.py` | Formats des requêtes et réponses |
| `app/api/` | Routes HTTP de l’agent |
| `app/clients/prometheus_client.py` | Interroger Prometheus |
| `app/clients/loki_client.py` | Rechercher les logs dans Loki |
| `app/clients/kubernetes_client.py` | Lire les pods et événements |
| `app/services/diagnosis.py` | Produire le diagnostic |
| `app/services/correlation.py` | Relier métriques, logs et événements |
| `app/services/llm_service.py` | Appeler le modèle de langage |
| `app/prompts/` | Instructions du modèle |
| `tests/` | Tests futurs |
| `requirements.txt` | Dépendances Python |
| `Dockerfile` | Construire l’image Docker |
| `k8s/` | Déploiement Kubernetes |
| `docs/` | Documentation du projet |
| `scripts/` | Scripts de démarrage et validation |

## Flux prévu

Utilisateur → Open WebUI → Agent IA → Prometheus/Loki/Kubernetes → Modèle de langage → Diagnostic
