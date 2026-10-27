# Validation des deux clusters — 27 octobre 2026

Validation réelle sur macOS Apple Silicon, Docker Desktop et deux profils
Minikube indépendants. Apertus tourne sur le Mac. Le graphique et la configuration
sont dans [le guide des deux clusters](two-clusters.md).

## Installation et isolation

- `kubeai-observed` : namespaces `applications` et `collecte` ; Alloy et
  kube-state-metrics sont prêts.
- `kubeai-supervision` : namespaces `kubeai` et `supervision` ; agent, WebUI,
  Mimir et Loki sont prêts.
- Le redémarrage par `bash start.sh` a réussi et le tunnel WebUI répond sur
  `http://127.0.0.1:8080`.
- `/ready`, `/llm/status` et `/cluster/inventory` répondent correctement.
  L'inventaire de l'agent contient uniquement le nœud `kubeai-observed`, avec
  des pods dans `applications`, `collecte` et `kube-system`.
- Le lecteur peut lister les nœuds ; `get secrets` et `delete pods` sont refusés.
- Les jobs cAdvisor, kube-state-metrics et pods annotés arrivent dans Mimir
  avec `cluster="kubeai-observed"`.

## Démo et nettoyage

`bash start-failure.sh` a exécuté le scénario complet dans
`applications/kubeai-demo-incident`, du **27 octobre 2026 à 11:24:48 UTC**
à **11:28:48 UTC** (12:24:48–12:28:48 en Suisse).

`python3 scripts/verify-demo-metrics.py` a confirmé :

| Contrôle | Résultat |
|---|---|
| Pod terminé | `Succeeded` |
| Scrape applicatif | `up = 1` |
| Requêtes HTTP enregistrées | 352 |
| Réponses HTTP 5xx | 89 |
| Observations dans l'histogramme HTTP | 352 |
| CPU cumulé du processus | 10,226669 secondes |
| Marqueurs Loki | `normal`, `anomaly_start`, `recovered`, `complete` |

Deux appels réels ont emprunté la connexion du pod Open WebUI vers l'agent et
Apertus, avec HTTP 200 : « Analyse l'incident du dernier script de démonstration »
(65 observations datées), puis « Pour ce même incident, corrèle les logs, le CPU
et les erreurs HTTP ». Les réponses ciblent bien `applications/kubeai-demo-incident`
et la fenêtre courante.

`bash stop-failure.sh` a supprimé le pod et le ConfigMap de la démo. Aucun pod
ne reste dans `applications`. Les deux collecteurs, Mimir, Loki, l'agent et
WebUI restent `1/1 Running` après le nettoyage.

## Tests automatisés et limites

363 tests de l'agent passent, avec 4 sous-tests. Les tests d'intégration sont
exclus de ce compte. Les 31 tests des scripts passent également. Les contrôles
de syntaxe Bash et les vérifications des différences Git passent.

Les wrappers PowerShell sont fournis, mais aucun moteur PowerShell n'est installé
sur ce Mac : leur exécution réelle Windows n'a pas été validée ici. Les appels
de démonstration ont été testés via les API ; l'interface authentifiée n'a pas
été pilotée visuellement.

## Correction des apostrophes dans les questions copiées

Une question copiée avec `l’incident` était normalisée en `lincident` : la
suppression de l'apostrophe courbe empêchait la reconnaissance du mot
`incident`. La première question passait alors dans le chat général et la
relance de corrélation n'avait pas de fenêtre historique à reprendre.

La normalisation conserve désormais les élisions françaises en convertissant
les apostrophes typographiques en apostrophes simples. Le routage, la lecture
des dates et le bilan de santé utilisent cette même normalisation. Sept tests
supplémentaires couvrent les variantes d'apostrophe, la question exacte copiée,
la chronologie JSON/SSE et la corrélation avec les mêmes bornes Loki.
La suite complète de l'agent passe désormais à **370 tests** (4 sous-tests).
Après déploiement, les deux messages exacts ont été vérifiés en streaming via
le pod Open WebUI : 66 observations et les quatre phases de la démo à
12:33:55–12:37:55, puis corrélation de cette même fenêtre. La relance fonctionne
également lorsque l'historique contient l'ancienne réponse erronée « healthy ».

## Découverte automatique du dernier incident

La demande « analyse le dernier incident » retrouve désormais la ressource et
la période sans nom de pod ni date. Elle compare les derniers logs d'anomalie
du namespace observé, les avertissements de pods Kubernetes et les marqueurs
de démo. Dix-huit tests supplémentaires couvrent les incidents non démonstratifs,
les pods supprimés, la priorité au signal le plus récent, les sources absentes,
les égalités entre pods et les bornes explicites. La suite passe à **388 tests**.

Après déploiement, la demande courte a répondu HTTP 200 en streaming via la
connexion d'Open WebUI : `applications/kubeai-demo-incident`, période
**12:50:44–12:54:44, heure suisse**, 65 observations. La relance « Pour ce même
incident, corrèle les logs et les métriques » a repris cette même fenêtre avec
HTTP 200. Le périmètre et les limites de recherche sont visibles dans la réponse.

## Agent piloté par le modèle

Le chat déployé utilise désormais `KUBEAI_CHAT_MODE=agent`. Le modèle reçoit
les fonctions LangChain typées, choisit leurs arguments, lit les résultats
réels et rédige la réponse. Le test interdit explicitement tout appel au
routeur historique et à son analyse de mots-clés sur ce parcours.
Les 411 tests de l'agent (dont 23 pour la nouvelle boucle et ses protocoles,
plus 4 sous-tests) et les 31 tests des scripts passent.

Le serveur Apertus local émettait parfois une description fictive d'appel
au lieu d'un appel natif. Le protocole automatique utilise donc une décision
JSON contrainte par schéma (`name`, `arguments`) pour le profil local. Mistral
conserve les appels natifs ; ce nouveau parcours n'a pas été testé avec une
vraie clé Mistral. Le registre et les contrôles d'exécution sont communs.

Les lectures réelles ont retrouvé `applications/kubeai-demo-incident` dans
Loki, sur la fenêtre UTC du 27 octobre **12:06:23–12:10:25**, arrondie vers
l'extérieur pour conserver tous les marqueurs. L'outil de preuves a lu Loki,
Mimir et l'inventaire Kubernetes en environ une seconde dans un essai isolé.
Les mesures incluent un pic CPU applicatif de **0,10003 cœur**, un ratio
HTTP 5xx maximal de **1** et un compteur de redémarrages de **0** sur la fenêtre.

Les premiers essais ont révélé des noms de pods inventés, des dates recopiées
avec une précision réduite et des relances répétant les mêmes appels. Les noms
sans provenance sont maintenant refusés avant lecture, les bornes découvertes
sont arrondies vers l'extérieur et la référence de la fenêtre est conservée
dans la réponse. Le contrôleur indique la progression, borne les appels et
renvoie une erreur explicite si le modèle ne termine pas.

**Limite observée :** Apertus 8B peut encore tirer une conclusion non étayée
malgré des données correctes, notamment sur une dépendance externe ou les
conséquences d'un compteur de redémarrages. La validation des appels ne valide
pas chaque phrase de sa synthèse. Les résultats bruts restent disponibles dans
`/diagnose.observation.tools` ; voir [le fonctionnement de l'agent](agent-tools.md).

Après les ajustements, une relance anglaise en SSE a terminé sans événement
d'erreur en 82 secondes (huit commentaires de maintien de connexion). Le modèle
a choisi `get_metrics`, puis `get_incident_evidence` ; la réponse a conservé
la fenêtre UTC **12:06:23–12:10:25** de l'épisode précédent.

Le test final via le pod Open WebUI a répondu en français en 54 secondes à
« Peux-tu regarder ce qui a déraillé le plus récemment dans l’application et
expliquer comment elle s’en est sortie ? ». La trace contient
`find_latest_incident`, puis `get_incident_evidence`, avec les mêmes ressources
et bornes vérifiées. Le protocole rappelle la question d'origine pour choisir
la langue malgré les instructions techniques rédigées en anglais.

La question anglaise libre a également répondu en anglais en 54 secondes, avec
les mêmes deux appels et les mêmes bornes. Ce contrôle valide la langue et
l'orchestration. Il ne valide pas toute la synthèse : dans cette réponse,
Apertus a affirmé un redémarrage et un pod encore en cours d'exécution, alors
que les outils indiquaient zéro redémarrage et `Succeeded`. Cette contradiction
reste une limite connue du modèle dans cette version.

## Correction de l'erreur interne sur un inventaire non vide

L'extraction des ressources de l'agent parcourait toute clé `pods` comme une
liste. L'inventaire global contient aussi `summary.pods`, un entier : une
valeur positive déclenchait `TypeError: 'int' object is not iterable`, masquée
par le message générique du streaming. Le parcours distingue maintenant les
listes de ressources, les compteurs et les objets d'état des sources.

Quatre régressions couvrent un inventaire avec un vrai pod en JSON et SSE,
les formes différentes du champ `pods`, et la journalisation des exceptions
SSE avec masquage des clés. Les 417 tests de l'agent et 4 sous-tests passent.

Après redéploiement, les deux questions retrouvées dans les conversations en
échec ont été rejouées depuis le pod Open WebUI en streaming : état des pods
(105 secondes) et incidents récents (102 secondes). Les deux flux ont produit
une réponse et `[DONE]`, sans événement d'erreur. Les logs confirment que
`get_cluster_inventory` termine sur l'inventaire réel non vide.

## Audit d'intégration du 27 octobre

- 417 tests agent passent après le portage tous-namespaces.
- 20 tests de scripts Python passent ; les tests du lanceur Bash ne sont pas
  exécutables dans cet environnement Windows (WSL/Bash refusé), mais la
  validation macOS d'origine reste la référence.
- /cluster/status couvre désormais scope=cluster,
amespace=*.
- Alloy et le RBAC couvrent tous les namespaces du cluster observé.
- Les outils get_logs et get_events acceptent l'absence de namespace
  (tous namespaces) ou un namespace explicite.
