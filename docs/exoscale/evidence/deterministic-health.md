# KubeAI — santé déterministe et reproductibilité

Image active :

```text
docker.io/anassndri/kubeai-agent@sha256:e8a46d7e8420a6d797590b145f9e7a2cac73fe18711b45dadb8630fc7822ca74
```

## Séparation des responsabilités

1. **Collecte** : Kubernetes (`nodes`, `pods`, `events`), Mimir (`cluster_usage`), Loki.
2. **Analyse** : `global_health.assess()` et `ClusterHealthReport` calculent les
   compteurs, le niveau de santé, les causes étayées et les données manquantes.
3. **Mistral** : n'intervient pas pour A. Pour B/C, il ne peut que sélectionner
   une priorité de lecture parmi des hypothèses déjà calculées ; il ne modifie
   ni les chiffres, ni les statuts, ni les hypothèses.

## Règles A

| Niveau | Règle |
|---|---|
| `CRITICAL` | tous les nœuds observés sont NotReady et la source nœuds est `ok` |
| `DEGRADED` | au moins un nœud NotReady ou un pod actuellement en défaut (`Pending`, `Failed`, Running non Ready) |
| `UNKNOWN` | nœuds/pods indispensables absents ou incomplets, sans anomalie concluante |
| `HEALTHY` | aucun signal actuel ou récent parmi les contrôles disponibles |

Règles complémentaires :

- `Succeeded` est classé `completed`, jamais défaillant du seul fait qu'il n'est
  pas Ready ;
- les redémarrages sont des compteurs cumulés, rapportés mais non utilisés seuls
  pour dégrader l'état ;
- les événements Warning ne comptent comme actifs que dans la fenêtre récente ;
  les anciens restent historiques ;
- une source indisponible est déclarée dans `unavailable` et rend le rapport
  partiel ; aucune valeur absente n'est remplacée par zéro ;
- chaque anomalie est comptée une fois dans `causes` et `affected`.

## Snapshots

### A — LIVE

Un snapshot A est conservé **120 secondes** en mémoire :

- `snapshot_id` du type `live-...` ;
- `mode=LIVE` ;
- mêmes données, même statut, mêmes chiffres pendant la durée du snapshot ;
- après expiration, une nouvelle observation est collectée. Si le statut, les
  nœuds ou les pods changent, une section de changement est ajoutée ; sinon les
  faits restent identiques.

### B/C — HISTORIQUE

Les questions B/C historiques sont mises en cache **300 secondes** dans le mode
agent. Le cache contient le résultat réellement calculé à partir de Loki/Mimir,
jamais une réponse prédéfinie. Les deux rapports partagent la même fenêtre
`09:08–09:13 UTC` et la même timeline (70 entrées identiques lors du test).

Cette solution est volontairement en mémoire, sans nouvelle ressource payante.
Elle garantit la reproductibilité pendant la soutenance ; un redémarrage de
l'agent crée un nouveau snapshot LIVE, ce qui doit être annoncé comme tel.

## Tests finaux

```text
A  10/10 textes identiques, 0 erreur (image active)
B   5/5  textes identiques, 0 erreur
C   5/5  textes identiques, 0 erreur
```

Observations structurées A/B/C : hash identique à chaque répétition.
B/C : `timeline` identique, 70 entrées.

Tests unitaires : **429 passed**.

## Questions définitives

### A

```text
Quel est l'état de santé de mon cluster ?
```

### B

```text
Analyse l'incident du pod kubeai-demo-incident dans le namespace applications, le 29 octobre 2026 entre 09:08 et 09:13 UTC. Reconstitue la chronologie à partir des logs et métriques réellement disponibles.
```

### C

```text
Pour l'incident du pod kubeai-demo-incident dans le namespace applications, le 29 octobre 2026 entre 09:08 et 09:13 UTC, corrèle les logs, le CPU, la latence et les erreurs HTTP. Propose des hypothèses de cause, classe-les par plausibilité et précise les preuves disponibles ainsi que ce qui reste à vérifier.
```

## Captures

- **05** : A, montrer le statut, les compteurs, les métriques et le snapshot
  `live-...` dans les détails techniques.
- **06** : B, montrer la chronologie et la fenêtre UTC.
- **07** : C, montrer la corrélation, la plausibilité, les autres hypothèses et
  les données manquantes.

Utiliser une nouvelle conversation pour chaque capture, sans redémarrer l'agent
entre 05 et 07.
