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

La démonstration utilise la fenêtre UTC et le pod de scénario déjà collecté.
La question est volontairement complète afin que l'agent n'invente pas une
période à partir de l'état actuel :

> Analyse l'incident du pod kubeai-demo-incident dans le namespace kubeai, le 19 octobre 2026 entre 18:25 et 18:33 UTC. Reconstitue la chronologie à partir des logs et des métriques réellement disponibles.

FastAPI interroge les événements Kubernetes disponibles, les séries historiques
Mimir et les logs Loki sur ces mêmes bornes. La réponse présente d'abord une
synthèse chronologique d'au plus huit observations significatives. Les marqueurs
réels `normal`, `anomaly_start`, `recovered` et `complete` situent le scénario ;
les échantillons CPU applicatifs sont résumés avant, pendant et après la charge
si les données existent. Toutes les observations et références restent ensuite
présentes dans la section détaillée.

**Résultat réel rapporté :** l'équipe a obtenu une chronologie de 52 observations
Mimir/Loki dans Open WebUI. Les événements Kubernetes ne sont inclus que s'ils
ont effectivement été conservés et observés dans la fenêtre. Le statut ou les
redémarrages actuels ne sont pas présentés comme des preuves historiques.

Une absence de logs, d'échantillons ou d'événements est explicitement signalée.
Les événements Kubernetes peuvent expirer avant les séries et logs. La
proximité entre deux observations ne prouve pas une causalité.

## C — Corrélation logs / métriques et hypothèses

Après B, poser dans le même fil :

> Pour ce même incident, corrèle les logs et le CPU.

Le backend reprend la fenêtre et la ressource de la question précédente,
uniquement parce que l'utilisateur fait référence au « même incident ». Il
présente séparément :

1. les faits horodatés et leurs sources ;
2. les hypothèses appuyées, leur confiance qualitative et les preuves associées ;
3. les alternatives, contradictions, limites et vérifications recommandées.

**Métrique CPU :** `process_cpu_seconds_total` représente le temps CPU du
processus de démonstration. Son taux PromQL est exprimé en cœurs utilisés par
le processus. Ce n'est ni le CPU total du conteneur ni un pourcentage de sa
limite. La configuration actuelle n'utilise pas cAdvisor et n'accorde pas
`nodes/proxy`. La mesure applicative dépend du scrape opt-in du pod annoté.

**Résultat réel rapporté :** une hausse du CPU du processus a été associée dans
le temps aux logs de l'anomalie ; l'équipe a obtenu des valeurs de l'ordre de
0,0003 à 0,1002 cœur pour les échantillons comparés. Cette association soutient
une hypothèse limitée, pas une preuve de causalité ni une mesure de saturation
du conteneur. La cause demeure indéterminée si les autres preuves requises
manquent.

## Rôle de Mistral / Apertus

Les faits, les requêtes, la chronologie et les hypothèses sont calculés par le
backend avant la contribution facultative du modèle. Pour B, le modèle peut
prioriser une source à vérifier ; pour C, il peut prioriser une hypothèse déjà
calculée. Il ne crée aucun fait et ne transforme pas une coïncidence temporelle
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

- Le déploiement collecte les métriques Kubernetes par kube-state-metrics et
  les métriques applicatives explicitement annotées. cAdvisor est désactivé ;
  les métriques détaillées du CPU/mémoire des conteneurs peuvent donc manquer.
- Aucune latence HTTP ni aucun taux d'erreur applicatif n'est déduit sans
  instrumentation correspondante.
- La rétention Loki/Mimir et celle des événements Kubernetes sont différentes.
  Des logs historiques peuvent subsister alors que les événements ont expiré.
- Le cluster Docker Desktop et les composants de monitoring consomment une part
  importante des 8 Go du poste. Les ressources déclarées dans les manifests ne
  sont pas une mesure de consommation réelle.
- L'agent est en lecture seule. Les recommandations ne sont pas exécutées et
  aucune remédiation automatique n'est disponible.
