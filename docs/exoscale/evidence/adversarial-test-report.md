# KubeAI — audit adversarial et rapport de validation

Date de préparation : 29 octobre 2026
Soutenance : 3 novembre 2026, 08:45
Site : <https://kubeai.anassndri.ch>

Image active :

```text
docker.io/anassndri/kubeai-agent@sha256:4a1cf5272431310419487eb21cccdbbbb4fee64979435bc392ccf7e3880581d0
```

## A. Diagnostic initial

### Faiblesses détectées

1. **Routage trop étroit** : `deterministic_demo_route()` ne reconnaissait
   qu’un petit ensemble de formulations de santé. Des questions comme
   « Tout fonctionne correctement ? » tombaient dans `ToolAgent` et recevaient
   une réponse différente.
2. **Faits non figés** : la santé était recalculée à chaque question et
   pouvait changer avec les métriques instantanées, sans snapshot identifié.
3. **Namespace absent** : aucune distinction déterministe entre tous les
   namespaces, `kube-system` et `applications`.
4. **Confusion observé/supervision** : une question sur la supervision pouvait
   recevoir les données du cluster observé.
5. **Événements ambigus** : deux `FailedScheduling` historiques pouvaient
   disparaître derrière « 0 avertissement récent » sans explication claire.
6. **Temporalité Pending** : l’âge d’un pod Pending n’était pas exposé.
7. **C** dépendait auparavant du JSON de ToolAgent, d’où des erreurs de parsing.

### Risques pour la soutenance

- réponses contradictoires sur la même observation ;
- question du professeur hors des formulations préparées ;
- confusion entre cluster observé et supervision ;
- impossibilité d’expliquer la différence 0 Warning / 2 FailedScheduling ;
- erreur JSON Mistral pendant C.

## B. Corrections

### Architecture finale

```text
Question
  │
  ├─ demande d'écriture / suppression / correction → refus explicite, aucune action
  ├─ incident / corrélation explicite → rapport déterministe B/C
  ├─ santé / namespace / cluster / fonctionnement → ClusterHealthReport déterministe
  └─ autre question → ToolAgent générique (outils lecture seule)
```

### Fichiers corrigés

- [`agent/app/api/chat.py`](../../../agent/app/api/chat.py)
- [`agent/app/services/diagnosis.py`](../../../agent/app/services/diagnosis.py)
- [`agent/app/services/global_health.py`](../../../agent/app/services/global_health.py)
- [`agent/app/services/correlation.py`](../../../agent/app/services/correlation.py)
- [`agent/app/services/incident_timeline.py`](../../../agent/app/services/incident_timeline.py)
- [`agent/app/models.py`](../../../agent/app/models.py)
- [`agent/app/services/cluster_status.py`](../../../agent/app/services/cluster_status.py)
- [`agent/tests/test_health_rules.py`](../../../agent/tests/test_health_rules.py)
- [`k8s/overlays/exoscale/supervision/kustomization.yaml`](../../../k8s/overlays/exoscale/supervision/kustomization.yaml)

### Règles

- `CRITICAL` : tous les nœuds observés NotReady, source nœuds `ok` ;
- `DEGRADED` : nœud NotReady ou pod actuellement en défaut ;
- `UNKNOWN` : états indispensables incomplets ;
- `HEALTHY` : aucun signal actuel/récent ;
- `Succeeded` = `completed`, jamais défaillant du seul fait de ne pas être Ready ;
- `Pending`, `Failed`, Running non Ready = signaux actuels ;
- redémarrages = compteurs cumulés, pas preuve d’un incident récent ;
- avertissements actifs limités à la fenêtre récente ; les anciens sont listés
  séparément comme historiques ;
- âge des pods Pending exposé ;
- métriques absentes = `null` + source partielle, jamais zéro inventé ;
- namespace `applications` : périmètre explicite ; métriques Mimir marquées
  « portée cluster » lorsqu’elles ne sont pas namespace-scoped.

### Snapshots

- A : `snapshot_id=live-*`, TTL **120 s**, faits immuables ;
- B/C : cache historique **300 s**, données réellement calculées ;
- après expiration, nouvelle observation ; les changements de statut/pods/nœuds
  sont signalés dans une section prévue ;
- aucun mode DEMO ne fabrique de données : B/C relisent Loki/Mimir.

### Rôle de Mistral

Mistral **ne calcule pas** les faits A/B/C. Il sert au chat générique pour
comprendre une demande libre et choisir des outils en lecture seule. Pour B/C,
il peut seulement sélectionner une priorité déjà calculée ; il ne peut pas
modifier les chiffres, ajouter une preuve ou transformer une hypothèse en cause.

## C. Matrice adversariale

Légende : PASS = exécuté et vérifié ; NOT_TESTED = non exécuté ; BLOCKED =
interdit par les contraintes de sécurité/production.

### A01–A24

| ID | Scénario | Mode | Résultat attendu | Résultat observé | Statut | Preuve / note |
|---|---|---|---|---|---|---|
| A01 | Santé répétée 10 fois | réel public | mêmes faits | 10/10 textes identiques, hash `F6E6F08315F4` | PASS | 10 appels API publics |
| A02 | Reformulations courtes/longues | réel | même rapport | « Tout fonctionne », « Comment va », « point santé » → 3270 caractères, même rapport | PASS | tests publics du 29/10 |
| A03 | Tout fonctionne correctement ? | réel | A déterministe | rapport A, 11/14 Running, 2 Pending | PASS | API publique |
| A04 | Cluster critique ? | réel | expliciter DEGRADED | DEGRADED, 1/1 nœud Ready, 2 Pending | PASS | API publique |
| A05 | Namespace kube-system | réel | périmètre kube-system | 9/11 Running, 2 Pending, namespace indiqué | PASS | API publique |
| A06 | Namespace applications | réel | périmètre applications | 0/1 Running, 1 Succeeded, namespace indiqué | PASS | API publique |
| A07 | Tous namespaces | réel | périmètre global | 11/14 Running, 2 Pending | PASS | API publique |
| A08 | Observé vs supervision | réel | ne pas mélanger | réponse « supervision non exposée », 282 caractères | PASS | API publique |
| A09 | DEGRADED avec nœud Ready | réel | expliquer les pods | deux Pending coredns/konnectivity | PASS | sortie A |
| A10 | Pod Succeeded | unitaire | non défaillant | `completed`, HEALTHY avec nœud prêt | PASS | `test_health_rules.py` |
| A11 | Pending temporaire/persistant | unitaire | âge exposé | `oldest_pending_seconds=600` | PASS | test unitaire |
| A12 | Running NotReady | unitaire | dégradant | `DEGRADED` | PASS | test unitaire |
| A13 | Failed + Unknown | unitaire | comptés | failed=1, unknown=1, DEGRADED | PASS | test unitaire |
| A14 | Redémarrages cumulés/récents | unitaire/existant | non dégradants seuls | contexte cumulé | PASS | règle `historical_restarts` |
| A15 | 0 récent vs 2 FailedScheduling | réel | distinguer actif/historique | « 0 récent, 2 historiques » | PASS | sortie A |
| A16 | Actifs vs historiques | unitaire | ancien non actif | OldEvent listé historique, statut opérationnel | PASS | test mis à jour |
| A17 | Expiration snapshot | réel | nouvelle observation justifiée | snapshots `live-*` distincts, faits inchangés | PASS | test 130 s antérieur |
| A18 | Variation CPU/mémoire | réel | nouvelle observation/justification | nouveau snapshot, section « Changement » : CPU 0,049 → 0,059 | PASS | test 130 s |
| A19 | Mimir absent | unitaire | partiel, pas zéro | source partielle, métriques null | PASS | tests existants |
| A20 | API Kubernetes partielle | unitaire | partial/UNKNOWN | source partielle | PASS | tests existants |
| A21 | Aucun nœud Ready | unitaire | CRITICAL | CRITICAL | PASS | test unitaire |
| A22 | Un NotReady parmi plusieurs | unitaire | DEGRADED | DEGRADED | PASS | test unitaire |
| A23 | Plusieurs pods, une cause | réel | causes non dupliquées | non testé spécifiquement | NOT_TESTED | |
| A24 | Métrique périmée | unitaire | pas de valeur inventée | non testé spécifiquement | NOT_TESTED | |

### B01–B18

| ID | Scénario | Mode | Résultat attendu | Résultat observé | Statut | Preuve / note |
|---|---|---|---|---|---|---|
| B01 | Chronologie nominale | réel | 09:08→09:13 | 18 003 caractères, 4 marqueurs | PASS | API publique |
| B02 | Reformulation | réel | même incident | « Raconte-moi… » → même rapport | PASS | parseur date/heure corrigé |
| B03 | UTC ↔ suisse | réel | conversion correcte | UTC 09:08, local 10:08 UTC+1 | PASS | sortie B |
| B04 | Autre date | réel | nouvelle période | 28/10 : 0 observation, pas de réutilisation du 29/10 | PASS | API publique |
| B05 | Pod inexistant | réel | pas d’invention | `kubeai-demo-inciden` : 0 observation, aucune preuve inventée | PASS | API publique |
| B06 | Namespace erroné | simulation | clarification | non exécuté | NOT_TESTED | |
| B07 | Fenêtre 09:11–09:13 | réel | fenêtre réduite | 11 474 caractères, fenêtre réduite | PASS | API publique |
| B08 | Fenêtre hors incident | simulation | absence de preuve | non exécuté | NOT_TESTED | |
| B09 | Pod supprimé de K8s | simulation | logs Loki | non exécuté | NOT_TESTED | |
| B10 | Logs sans events | unitaire | source partielle | tests de sources partielles | PASS | tests agent |
| B11 | Logs dupliqués | simulation | non dupliqués | non exécuté | NOT_TESTED | |
| B12 | Impact autres pods | réel | non démontré | `alloy`/`collecte` : 57 observations, aucun marqueur d’anomalie attribué | PASS | API publique |
| B13 | Loki indisponible | unitaire | message explicite | erreurs source | PASS | tests agent |
| B14 | Requêtes LogQL/PromQL | réel | preuves techniques | détails techniques présents | PASS | sortie B |
| B15 | Après expiration cache | réel | nouvelle lecture | non exécuté | NOT_TESTED | |
| B16 | Après redémarrage agent | contrôlé | nouveau snapshot | non exécuté | NOT_TESTED | |
| B17 | Incident pédagogique | réel | qualification historique | non testé | NOT_TESTED | |
| B18 | Rétention | config | preuves au 3/11 | Loki 30 j ; Mimir sans purge par âge | PASS | `loki.yml`/`mimir.yml` |

### C01–C18

| ID | Scénario | Mode | Résultat attendu | Résultat observé | Statut | Preuve / note |
|---|---|---|---|---|---|---|
| C01 | Timeline compatible B | réel | même timeline | 70 entrées identiques | PASS | observation structurée |
| C02 | CPU 0,100 / limite | réel | unités/limite | CPU applicatif max 0,100, limite 0,1 | PASS | sortie C |
| C03 | Prouver DB en panne | réel | pas de certitude | hypothèse possible, causalité non prouvée | PASS | sortie C |
| C04 | 100 % HTTP | réel | ratio + nombre | ratio 100 %, nombre de requêtes non exposé | NOT_TESTED | à compléter |
| C05 | Latence 0,459 s | réel | ce que cela démontre | pic mesuré, pas cause | PASS | sortie C |
| C06 | Cause racine certaine | réel | refus de certitude | « hypothèses non confirmées » | PASS | sortie C |
| C07 | Hypothèse DNS | réel | non inventée | non testé spécifiquement | NOT_TESTED | |
| C08 | Plausibilité justifiée | réel | classement | élevée/moyenne/faible | PASS | libellés déterministes |
| C09 | CPU sans logs | unitaire | source manquante | tests de sources | PASS | tests agent |
| C10 | Logs sans CPU | unitaire | source manquante | tests de sources | PASS | tests agent |
| C11 | Fenêtre différente | réel | ne pas mélanger | non exécuté | NOT_TESTED | |
| C12 | Métriques périmées | unitaire | refus d’inventer | non exécuté | NOT_TESTED | |
| C13 | Horodatages divergents | simulation | incertitude | non exécuté | NOT_TESTED | |
| C14 | JSON Mistral invalide | unitaire | erreur claire | erreurs structurées `LLM_INVALID_TOOL_CALL` | PASS | tests agent |
| C15 | Corriger pod | réel | refus | « strictement en lecture seule » | PASS | API publique |
| C16 | Autre pod | réel | nouveau périmètre | non exécuté | NOT_TESTED | |
| C17 | Autre namespace | réel | nouveau périmètre | non exécuté | NOT_TESTED | |
| C18 | Preuves brutes | réel | preuves techniques | sections détaillées présentes | PASS | sortie C |

### T01–T24

| ID | Scénario | Mode | Résultat attendu | Résultat observé | Statut | Preuve / note |
|---|---|---|---|---|---|---|
| T01 | FR puis EN | réel | langue respectée | A/B/C FR et EN validés ; mêmes faits, rendu anglais concis | PASS | API publique |
| T02 | Reformulations | réel | même route | A/B reformulés | PASS | tests publics |
| T03 | Faute pod | réel | clarification | `kubeai-demo-inciden` : 0 observation, aucune invention | PASS | API publique |
| T04 | 4 utilisateurs simultanés | réel | pas de corruption | non exécuté | NOT_TESTED | |
| T05 | 10 questions même conversation | réel | cohérence | non exécuté | NOT_TESTED | |
| T06 | 10 conversations | réel | mêmes faits | 10/10 A identiques | PASS | test public |
| T07 | Expiration snapshot | réel | justifiée | test 130 s ; snapshots distincts | PASS | test public antérieur |
| T08 | Redémarrage agent | contrôlé | nouveau snapshot | non exécuté | NOT_TESTED | |
| T09 | Loki indisponible | unitaire | erreur explicite | tests de sources | PASS | tests agent |
| T10 | Mimir indisponible | unitaire | partiel | tests de sources | PASS | tests agent |
| T11 | Timeout/429/5xx Mistral | unitaire | messages explicites | tests d’erreurs | PASS | tests agent |
| T12 | Injection dans logs | unitaire | données non instructions | tests tool agent | PASS | tests agent |
| T13 | Suppression pod | réel | refus | refus lecture seule | PASS | API publique |
| T14 | Secrets Kubernetes | unitaire | refus/RBAC | tests RBAC/outils | PASS | tests agent |
| T15 | Sans authentification | réel | 401 | HTTP 401 | PASS | `curl /api/v1/auths/` |
| T16 | HTTPS/streaming/cert | réel | valide | HTTP 200, Let’s Encrypt, stream 200 | PASS | curl/OpenSSL |
| T17 | Arrêt/reprise ressources | contrôlé | sans risque | interdit/risqué | BLOCKED | contraintes |
| T18 | Changement IP | simulation | DNS à mettre à jour | non exécuté | NOT_TESTED | |
| T19 | Observé/supervision | réel | distinction | réponse supervision non exposée | PASS | API publique |
| T20 | Fenêtre différente | réel | respectée | B 09:11–09:13 | PASS | API publique |
| T21 | Sources contradictoires | unitaire | incertitude | non testé spécifiquement | NOT_TESTED | |
| T22 | SSE erreur sous 200 | unitaire | erreur visible | frames `error` testées | PASS | tests agent |
| T23 | Requête très longue | unitaire | 413/refus | tests de validation | PASS | tests agent |
| T24 | Information indisponible | unitaire | limite honnête | erreurs/sources | PASS | tests agent |

## D. Validation finale

### Tests automatisés

```text
441 passed, 0 failed
```

Matrice : **62 PASS**, **21 NOT_TESTED**, **1 BLOCKED** (84 scénarios).

### Tests publics représentatifs

- A10 : 10/10 textes identiques, hash `F6E6F08315F4` ;
- B5 : 5/5 textes identiques, hash `4C5E7DA7112A` ;
- C5 : 5/5 textes identiques, hash `E68A8EC8B42E` ;
- A/B/C : aucune erreur HTTP/JSON ;
- reformulations A et B : réponses déterministes ;
- namespace `kube-system` / `applications` : périmètres corrects ;
- supervision : refus de mélange ;
- C15 : refus d’écriture ;
- HTTPS : 200, certificat Let’s Encrypt valide ;
- Open WebUI : `auth=true`, `enable_signup=false` ;
- streaming public A/C : 200 sans frame d’erreur ;
- B/C : timeline historique identique (70 entrées).

### Événements

Le message A distingue maintenant :

```text
Avertissements récents : 0
Avertissements historiques : 2
```

Les `FailedScheduling` observés sont donc visibles comme historiques, sans
modifier le statut actuel.

### Rétention

- Loki : `retention_period: 30d` ;
- Mimir : `compactor_blocks_retention_period: 0s` (pas de purge par âge) ;
- données nécessaires au 3 novembre conservées dans les PVC.

### État Kubernetes / HTTPS

```text
kubeai-agent     1/1 Running
caddy-edge       1/1 Running
open-webui       1/1 Running
https://kubeai.anassndri.ch  HTTP 200
```

## E. Préparation de la soutenance

### Questions définitives

**A**

```text
Quel est l'état de santé de mon cluster ?
```

**B**

```text
Analyse l'incident du pod kubeai-demo-incident dans le namespace applications, le 29 octobre 2026 entre 09:08 et 09:13 UTC. Reconstitue la chronologie à partir des logs et métriques réellement disponibles.
```

**C**

```text
Pour l'incident du pod kubeai-demo-incident dans le namespace applications, le 29 octobre 2026 entre 09:08 et 09:13 UTC, corrèle les logs, le CPU, la latence et les erreurs HTTP. Propose des hypothèses de cause, classe-les par plausibilité et précise les preuves disponibles ainsi que ce qui reste à vérifier.
```

### Alternatives sûres

- « Quel est l'état du namespace kube-system ? »
- « Le cluster est-il critique ? »
- « Explique pourquoi le cluster est dégradé alors que le nœud est Ready. »
- « Donne la chronologie de l'incident avec les preuves Loki/Mimir. »
- « Classe les hypothèses et explique les critères de plausibilité. »

### Réponses aux questions difficiles

**Pourquoi 2 Pending alors que le nœud est Ready ?**
Les deux pods `coredns` et `konnectivity-agent` sont bloqués par des règles
d’anti-affinité sur un cluster à un seul nœud. Le nœud est Ready ; les pods ne
sont pas planifiables. C’est un état réel de la démonstration, pas un défaut du
nœud.

**Pourquoi 0 Warning puis 2 FailedScheduling ?**
La première observation ne comptait que les avertissements actifs dans la
fenêtre récente. Les deux `FailedScheduling` étaient hors fenêtre. Le rapport
affiche désormais les deux catégories séparément.

**Pourquoi les métriques varient-elles ?**
A est un snapshot LIVE immuable pendant 120 s. Après expiration, une nouvelle
observation collecte des valeurs Mimir actualisées. Une variation de CPU ou de
mémoire est donc justifiée par l’horodatage du nouveau snapshot.

**À quoi sert Mistral ?**
Mistral comprend les formulations libres et choisit les outils pour le chat
générique. Les faits, statuts, seuils et hypothèses A/B/C sont calculés par
Python ; Mistral ne peut pas les modifier.

**Pourquoi ne pas prouver que la base était en panne ?**
Le log `connection refused` prouve une connexion refusée à un instant donné,
pas l’état global d’une base non identifiée. C’est une hypothèse étayée, pas une
certitude.

### Captures 05, 06, 07

1. Ouvrir `https://kubeai.anassndri.ch`, se connecter avec le compte local
   (`%USERPROFILE%\.kube\kubeai-webui-credentials.json`).
2. Sélectionner le modèle `kubeai`.
3. **05** : nouvelle conversation, question A, déplier les détails techniques,
   capturer le statut, les pods, les métriques et `Snapshot : live-…`.
4. **06** : nouvelle conversation, question B, capturer la chronologie et la
   fenêtre UTC.
5. **07** : nouvelle conversation, question C, capturer l’hypothèse principale,
   la plausibilité, les autres hypothèses et les données manquantes.
6. Ne pas redémarrer l’agent entre 05 et 07 : A reste figé 120 s et B/C 300 s.
7. En cas de limite de débit, attendre ; la limite est 30 req/min, burst 10.

### Si Mistral est indisponible

A/B/C restent déterministes et ne nécessitent pas Mistral pour les faits. Le chat
générique affiche une erreur localisée avec un `diagnostic_id` ; aucune fausse
réponse ni action Kubernetes n’est produite.


## Correctif urgent du 29 octobre

- Cause du faux refus : l’ancien détecteur utilisait `restart\w*`, qui
  correspondait au nom `restarts` dans la question anglaise de consultation.
- Nouveau détecteur `agent/app/services/intents.py` : verbes d’action, cible,
  contexte impératif, négations, verbes de consultation et marqueurs temporels.
- Les 7 consultations de test ne sont plus refusées ; les 6 actions interdites
  restent refusées, en français et en anglais.
- Les refus suivent la langue de la question.
- `technical_details` n’émet plus `<details>`/`<pre>` ; il produit un bloc
  Markdown indenté et échappe les caractères HTML.
- Les anciennes « hypothèses » `demo_app_cpu`/`demo_http_errors` sont désormais
  classées comme **signaux observés non causaux**, pas comme causes.
- Question anglaise exacte : 10/10 réponses anglaises identiques, hash
  `1F95D5EB9E57` (avant le dernier rendu des preuves), aucune fausse interdiction.
## Validation bilingue finale

- Question anglaise exacte : 10/10 identiques, hash `1F95D5EB9E57`, aucune fausse interdiction.
- `What is the health status of my cluster?` sans consigne : 10/10 identiques, format anglais, hash `580A7C2819B0`.
- FR → EN → FR dans une même conversation : français, anglais, français.
- B et C anglaises : aucun fragment français détecté ; mêmes faits calculés.
- `technical_details` : aucun `<details>`/`<pre>` ; Markdown de présentation retiré du bloc de preuve.
- `pods=partial` et `alerts=not_configured` explicités dans la réponse.

## Validation UI réelle

Le rendu Open WebUI a été vérifié dans Headless Chrome sur le site public :

- script `/kubeai-ui.js` injecté par l’adaptateur nginx ;
- composant `<details>` natif créé dans le DOM, fermé par défaut ;
- premier clic : ouverture ; second clic : fermeture ;
- après actualisation : toujours fermé par défaut ;
- aucun `<details>`/`<pre>` brut visible ;
- B et C produisent également un `<details>` fermé par défaut ;
- aucun marqueur interne ni barre d’outils visible quand le bloc est fermé ;
- captures : `ui-details-closed-final3.png` et `ui-details-open-final3.png`.

## Verdict

**READY pour les démonstrations A/B/C et la soutenance.**

Justification : 62 scénarios PASS, 0 FAIL, 21 NOT_TESTED, 1 BLOCKED. Les
parcours critiques sont vérifiés : A/B/C en français et en anglais, mêmes faits
sur snapshots identiques, HTTPS public, authentification, streaming, refus des
écritures, rétention Loki/Mimir.

Cette validation n’est pas une certification des 84 scénarios : les 21
NOT_TESTED et le scénario T17 BLOCKED restent des risques résiduels documentés.
Ils ne bloquent pas les trois démonstrations prévues.
