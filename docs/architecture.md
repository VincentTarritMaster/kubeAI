# Architecture prévue

## Composants

- **Open WebUI** : interface conversationnelle utilisée par la personne qui pose une question.
- **Agent IA** : future API qui recevra la demande, interrogera les sources autorisées et préparera les éléments utiles au diagnostic.
- **Prometheus** : fournit les métriques et les séries temporelles du cluster.
- **Loki** : reçoit et recherche les logs.
- **Alloy** : découvre les workloads Kubernetes, collecte leurs logs et les transmet à Loki selon une configuration qui reste à définir.
- **Modèle de langage** : formule une réponse à partir de la question et des données récupérées ; le modèle et son hébergement restent à choisir.
- **Kubernetes** : exécute les composants et fournit l’état des pods, nœuds et événements, sous réserve de droits RBAC à définir.

## Flux général

Open WebUI transmet la question à l’agent. L’agent interrogera ensuite Prometheus, Loki et l’API Kubernetes, puis transmettra les éléments sélectionnés au modèle de langage pour préparer un diagnostic. L’architecture et les contrats d’échange restent à valider en groupe.
