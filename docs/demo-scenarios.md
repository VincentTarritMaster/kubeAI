# Scénarios de démonstration

Les trois démonstrations prévues reprennent le sujet du projet. Le premier test de bout en bout ci-dessous a été exécuté ; les scénarios plus avancés restent à préciser.

## Incident visible dans Open WebUI

Test exécuté le 18 octobre 2026 sur le contexte local `minikube`, dans le namespace `kubeai`, avec Apertus 8B.

1. Lancer `./start-failure.sh` : il crée `kubeai-demo-failure` avec `restartPolicy: Never`, affiche des messages identifiables, puis termine avec le code `42`. Le message de variable absente est une simulation : le script affiche du texte, sans vérifier réellement une dépendance. Un message supplémentaire explicite cette limite.
2. Vérifier l'état `Failed` et le code de sortie dans Kubernetes. Comparer `kubectl logs --timestamps` aux lignes collectées par Loki pour ce pod.
3. Dans une nouvelle conversation Open WebUI avec le modèle `kubeai-apertus`, demander : « Quels pods ont un problème dans kubeai ? Identifie le pod concerné, cite son état Kubernetes et les logs pertinents, et distingue faits et hypothèses. » Poser la question dans les dix minutes suivant l'incident, fenêtre de logs utilisée par l'agent.
4. Vérifier le nom du pod, son état et les citations des logs. Les hypothèses doivent rester distinctes des faits ; le texte simulé ne prouve pas une panne réelle de base de données.
5. Lancer `./stop-failure.sh` pour supprimer uniquement ce pod temporaire, puis vérifier que `/cluster/status` revient à `healthy` et que les services restent prêts.

Résultat : le pod `kubeai-demo-failure-67cd36f8` a terminé en `Failed` avec le code `42`. Ses deux lignes étaient identiques dans Kubernetes et Loki. La réponse Open WebUI, transmise par l'utilisateur car l'accès navigateur de l'outil était bloqué, identifiait le bon pod et citait exactement les deux lignes. Après suppression, l'agent indiquait deux pods prêts, aucun pod en erreur et un état `healthy`.

Réserves observées lors de ce premier test : la réponse suggérait une détection effective de variable absente alors que le pod ne faisait qu'afficher un message simulé ; elle contenait également deux horodatages de relevé différents.

Corrections : les consignes imposent de signaler l'incident simulé sans présenter la variable absente comme un fait établi. L'horodatage automatique des réponses a été supprimé ; seules les sources sont ajoutées. Les anciens pieds de page sont retirés de l'historique transmis au modèle et les relevés générés par le modèle sont filtrés, y compris en streaming. Les horodatages des événements utiles au diagnostic restent disponibles.

Vérification des corrections : 25 tests unitaires passent. Avec Apertus local et le nouveau pod de démonstration, les réponses normales et en streaming distinguent explicitement la simulation d'une panne réelle, ne reprennent pas l'ancien horodatage injecté dans l'historique et contiennent une seule liste des sources, sans date de relevé. Ces vérifications ont utilisé l'API de l'agent. Les scripts ont été exécutés avec création depuis le manifeste séparé, démarrage répété, suppression et arrêt répété sans erreur. Les réponses de contrôle sont conservées localement dans `.cache/incident-tests/fixes-live.json`.

Les preuves locales de cette exécution (état Kubernetes, logs, réponse copiée et comparaison) sont conservées dans `.cache/incident-tests/67cd36f8/`, dossier ignoré par Git.

## Santé globale du cluster

Répondre sur l’état général du cluster à partir de ses métriques, logs et événements disponibles.

## Investigation avec chronologie

Présenter dans l’ordre les signaux pertinents d’un incident concernant un nœud, un pod ou un service.

## Corrélation et hypothèses de cause

Relier logs et métriques pour proposer des hypothèses de cause, en distinguant clairement les faits observés des hypothèses.
