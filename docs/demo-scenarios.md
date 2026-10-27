# Démonstrations A/B/C — P5-G17 | KubeAI

Ces scénarios utilisent le modèle virtuel `kubeai` dans Open WebUI. Les
diagnostics interrogent les sources configurées par FastAPI ; ils ne créent,
ne modifient et ne redémarrent aucune ressource Kubernetes. Les tests
automatisés remplacent Kubernetes, Mimir, Loki et le LLM par des réponses
simulées : ils vérifient la logique, pas la disponibilité du cluster.

## A — Santé globale du cluster

**Question à poser :**

> Quel est l'état de santé de mon cluster ?

**Sources et résultat attendu :** l'agent lit les nœuds, les pods de tous les
namespaces, les redémarrages cumulés et les événements récents. Il relève les
métriques disponibles dans Mimir. Le résultat distingue l'état actuel, les
incidents récents, les redémarrages historiques et les sources absentes. Un
redémarrage ancien ne suffit pas seul à prouver un incident en cours. « Sain »
signifie uniquement sain selon les contrôles disponibles ; ce n'est pas une
garantie de santé fonctionnelle de chaque application.

**Validation rapportée par l'équipe :** réponse de santé globale obtenue depuis
Open WebUI sur les données du cluster. Les règles de classification sont
détaillées dans [l'architecture](architecture.md) et couvertes par les tests
de santé globale.

## B — Investigation historique et chronologie

La demande **« Analyse le dernier incident »** sélectionne automatiquement un
pod et une période dans le namespace observé. L'agent compare les derniers
logs d'anomalie Loki (30 jours de rétention, au plus 200 logs candidats), les
avertissements Kubernetes de pods encore disponibles et le dernier cycle de démo.
Un pod supprimé peut être retrouvé par ses logs. La réponse indique la ressource
retenue et les limites de cette recherche.

Pour la démo complète, les quatre marqueurs donnent les bornes exactes. Pour un
autre signal, la fenêtre de recherche couvre deux minutes avant et jusqu'à
cinq minutes après, sans dépasser l'heure actuelle ; elle ne prouve pas la durée
réelle de l'incident. Sans signal horodaté, l'agent annonce ce qu'il n'a pas pu
retrouver. Deux pods au même horodatage demandent une sélection.
Une date explicite reste prioritaire et n'est pas remplacée par cette recherche.

Après `run-demo-incident.ps1`, la démonstration peut commencer par une question naturelle :

> Analyse l'incident du dernier script de démonstration.

L'agent retrouve d'abord le dernier cycle complet des quatre marqueurs
`KUBEAI_DEMO_INCIDENT` dans Loki. Leurs horodatages UTC délimitent ensuite les
lectures Kubernetes, Loki et Mimir. Il annonce les preuves trouvées et absentes.
Une fenêtre précise reste possible pour examiner un incident daté :

> Analyse l'incident du pod kubeai-demo-incident dans le namespace kubeai, le 19 octobre 2026 entre 18:25 et 18:33 UTC. Reconstitue la chronologie à partir des logs et des métriques réellement disponibles.

Les dates complètes `22.10.2026`, `22/10/2026`, `22-10-2026`, `22 octobre 2026`,
`22 oct. 2026`, `22 October 2026`, `October 22, 2026` et `2026-10-22` sont
interprétées comme dates locales `Europe/Zurich` par défaut. Les heures avec
suffixe `Z` restent en UTC. Une date sans année ou une heure seule provoque une
question de clarification ; aucune période implicite ne remplace les bornes
demandées. Pour un pod préfixé comme `open-webui`, KubeAI vérifie les candidats
dans Kubernetes et demande un choix si plusieurs pods correspondent.

FastAPI interroge les événements Kubernetes disponibles, les séries historiques
Mimir et les logs Loki sur ces mêmes bornes. La réponse présente d'abord un
résumé, puis au plus douze événements significatifs, groupés par date locale et
ordonnés à l'heure suisse par défaut. Les marqueurs réels `normal`,
`anomaly_start`, `recovered` et `complete` situent le scénario ; le cycle CPU
applicatif retenu montre sa mesure de base et son pic lorsque les deux existent.
Les observations UTC et références restent dans « Détails techniques / Preuves ».

**Résultat réel rapporté :** l'équipe a obtenu une chronologie de plus de 50
observations Mimir/Loki dans Open WebUI (52 à 80 selon la fenêtre et
l'activation de cAdvisor). Les événements Kubernetes ne sont inclus que s'ils
ont effectivement été conservés et observés dans la fenêtre. Le statut ou les
redémarrages actuels ne sont pas présentés comme des preuves historiques.

Une absence de logs, d'échantillons ou d'événements est explicitement signalée.
Les événements Kubernetes peuvent expirer avant les séries et logs. La
proximité entre deux observations ne prouve pas une causalité.

## C — Corrélation logs / métriques et hypothèses

Après B, poser dans le même fil :

> Pour ce même incident, corrèle les logs, le CPU et les erreurs HTTP.

On peut aussi intercaler la question A, puis demander « Reprends l'incident précédent
et corrèle ses logs et métriques ». « Pourquoi ? », « Quelles preuves ? » et « Et les
logs ? » poursuivent le sujet en cours. Après A, « Pourquoi ? » conserve le bilan
actuel du cluster. Une question météo est refusée avec une explication du périmètre
monitoring, sans appel au fournisseur.

Le backend reprend les paramètres de la dernière question d'incident pertinente,
y compris si une question sur la santé du cluster s'est intercalée. Si B n'avait
pas pu identifier une ressource, C poursuit avec une question ciblée. Il
présente séparément :

1. les faits horodatés et leurs sources ;
2. les hypothèses appuyées, leur confiance qualitative et les preuves associées ;
3. les alternatives, contradictions, limites et vérifications recommandées.

**Métrique CPU :** `process_cpu_seconds_total` représente le temps CPU du
processus de démonstration. Son taux PromQL est exprimé en cœurs utilisés par
le processus. Ce n'est ni le CPU total du conteneur ni un pourcentage de sa
limite. cAdvisor fournit en parallèle `container_cpu_usage_seconds_total` et
`container_memory_working_set_bytes` pour les conteneurs ; les deux mesures
restent distinctes.

**Métriques HTTP :** le pod expose aussi `http_requests_total{status="200|500"}`
et l'histogramme `http_request_duration_seconds`. Un trafic interne appelle
`/work` toutes les 500 ms ; pendant `anomaly_start`, ces appels passent en 500
avec une latence plus élevée, et le pod émet un log
`KUBEAI_DEMO_DEPENDENCY connection refused` après une tentative réelle vers un
port local fermé. Les appels reviennent à 200 après `recovered`. Le backend
interroge le ratio d'erreurs 5xx et la latence p95 ; une hausse nette pendant
l'anomalie produit l'hypothèse `demo_http_errors`, et le log de connexion peut
renforcer l'hypothèse `connectivity`. Les applications non instrumentées n'ont
pas ces séries et restent limitées aux autres signaux.

**Résultat réel rapporté :** une hausse du CPU du processus a été associée dans
le temps aux logs de l'anomalie ; l'équipe a obtenu des valeurs de l'ordre de
0,0003 à 0,1002 cœur pour les échantillons comparés. Le nouveau scénario HTTP
ajoute un signal d'erreurs/latence corrélé aux mêmes marqueurs. Ces associations
soutiennent des hypothèses limitées, pas une preuve de causalité ni une mesure
de saturation du conteneur. La cause demeure indéterminée si les autres preuves
requises manquent.

## Recréer des preuves réelles pour la soutenance

Les observations du 19 octobre sont encore présentes dans Mimir, mais les logs
Loki ont dépassé l'ancienne rétention de 48 heures et ne peuvent pas être
reconstitués. La configuration garde désormais logs et fenêtre de recherche
pendant 30 jours. Cette durée commence à l'ingestion ; changer la rétention ne
restaure jamais des logs déjà supprimés.

Pour générer un nouvel incident contrôlé, démarrer KubeAI, puis lancer dans
PowerShell depuis la racine du dépôt :

```powershell
.\start-failure.ps1
python scripts\verify-demo-metrics.py
```

Sur macOS avec Minikube, depuis la racine du dépôt :

```bash
bash start-failure.sh
python3 scripts/verify-demo-metrics.py
```

`start-failure.sh` lance désormais le scénario d'incident, via
`scripts/run-demo-incident.sh`. Il accepte un autre contexte, par exemple
`bash start-failure.sh kubeai-observed`, ou le moteur PowerShell lorsqu'il est
installé : `bash start-failure.sh --shell ps1`.
Sous Windows, `.\start-failure.ps1` lance `scripts/run-demo-incident.ps1`
sur le cluster observé de la dernière topologie démarrée ; `-Context` permet de choisir un autre contexte.
Les scripts de démonstration restent directement utilisables. Le suivi des logs
n'a pas de délai global de 30 secondes : il reste ouvert pendant le scénario.
`bash stop-failure.sh` ou `.\stop-failure.ps1` supprime ensuite les pods de démo
dans le namespace `applications`. Le [graphique des deux clusters](two-clusters.md)
montre les emplacements des collecteurs, du stockage et de l'agent.

Le second script vérifie `up{job="kubernetes-pods-annotated"}`, les compteurs
HTTP, l'histogramme de latence, `process_cpu_seconds_total` et les marqueurs
Loki. Il lit la dernière heure, donc il fonctionne même après la fin du pod.
Voir [`metrics.md`](metrics.md) pour le détail des signaux.

Le script attend la fin du scénario et affiche ses marqueurs UTC. Le pod mesure
le CPU réel de son processus, expose des compteurs HTTP et se termine après
environ quatre minutes. Il refuse de remplacer un scénario déjà en cours ; pour
une exécution terminée, il remplace uniquement le pod exact de démonstration.
Interroger B avec « Analyse l'incident du dernier script de démonstration » ou
avec la date et les heures réellement affichées. Les requêtes A, B et C lisent
ces données ; elles ne les créent pas. Le pod terminé reste disponible pour

**Attention au fuseau :** les marqueurs affichés par le script sont en **UTC**,
alors que l'interface Open WebUI affiche l'heure suisse (`Europe/Zurich`,
UTC+2 le 22 octobre). `19:36 UTC` correspond à `21:36` en Suisse. Utiliser
`23:36` revient à demander `21:36 UTC`, en dehors du scénario : la réponse
« aucun début d'anomalie précis » est alors correcte.
Alloy tant qu'il n'est pas remplacé.
`stop.ps1` conserve les PVC de Loki, Mimir et Alloy ; les données restent
interrogeables après redémarrage pendant leur rétention. Le PVC Loki fait 2 Gio,
ce qui impose de surveiller son espace libre pendant les 30 jours.

## Rôle de Mistral / Apertus

Les faits, les requêtes, la chronologie et les hypothèses sont calculés par le
backend avant la contribution facultative du modèle. Pour B, le modèle peut
signaler une source à vérifier ; pour C, il peut signaler une hypothèse déjà
calculée, mais l'ordre déterministe et toutes les hypothèses restent visibles.
Il ne crée aucun fait et ne transforme pas une coïncidence temporelle
en causalité. Le texte indique désormais distinctement si le modèle n'a pas été
sollicité, si l'appel API/transport a échoué, si sa réponse a été rejetée faute
de preuve ou si une priorité étayée a été retenue. Une erreur réelle n'est pas
masquée et le diagnostic déterministe reste visible.

## Reproduction contrôlée

Depuis Open WebUI, sélectionner `kubeai` et envoyer les questions ci-dessus.
Pour les résultats historiques, utiliser les horodatages UTC associés aux logs
du scénario déjà collecté. Ne pas remplacer ces bornes par l'heure actuelle.
Avant une nouvelle exécution réelle, vérifier que la période est encore dans la
rétention de Loki et Mimir et que les séries annotées sont présentes.

Contrôles de lecture seule possibles depuis PowerShell, avec un contexte ayant
les droits nécessaires :

```powershell
$Context = 'docker-desktop'
kubectl --context $Context get nodes
kubectl --context $Context -n kubeai get pods
kubectl --context $Context -n kubeai get events --sort-by=.metadata.creationTimestamp
```

L'API FastAPI expose aussi `/cluster/inventory` et les routes de diagnostic.
Les routes protégées nécessitent le Bearer configuré ; ne pas le placer dans un
fichier suivi ou dans une capture d'écran. Les tunnels et la configuration
Windows figurent dans le [README](../README.md).

## Limites communes

- Le déploiement collecte les métriques Kubernetes par kube-state-metrics,
  cAdvisor pour le CPU/mémoire des conteneurs et les métriques applicatives
  explicitement annotées. Les exécutions antérieures à l'activation de cAdvisor
  (22 octobre, environ 19:33 UTC) n'ont pas de séries conteneur ; relancer la
  démo pour en générer.
- Le pod de démonstration expose aussi des compteurs HTTP (`http_requests_total`)
  et un histogramme de latence ; les applications non instrumentées n'ont ni
  latence ni taux d'erreur exploitables.
- Les traces distribuées et Tempo sont hors périmètre.
- La rétention Loki/Mimir et celle des événements Kubernetes sont différentes.
  Des logs historiques peuvent subsister alors que les événements ont expiré.
- Le cluster Docker Desktop et les composants de monitoring consomment une part
  importante des 8 Go du poste. Les ressources déclarées dans les manifests ne
  sont pas une mesure de consommation réelle.
- L'agent est en lecture seule. Les recommandations ne sont pas exécutées et
  aucune remédiation automatique n'est disponible.
