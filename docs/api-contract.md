# Contrat API futur

Ces routes sont prévues pour l’agent ; elles ne sont pas encore implémentées. Les formats des requêtes et des réponses restent à définir.

| Méthode | Route | Rôle prévu |
|---|---|---|
| GET | `/health` | Vérifier que le processus répond. |
| GET | `/ready` | Indiquer si le service est prêt à recevoir une demande. |
| POST | `/diagnose` | Demander une analyse du cluster. |
