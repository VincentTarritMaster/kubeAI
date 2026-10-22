# Validation conversationnelle — 20 octobre 2026

> Rapport historique du 20 octobre 2026. Il indique 317 tests à cette date ;
> l’état actuel comporte 359 tests, cAdvisor et des fenêtres de démonstration
> plus récentes. Voir [README](../README.md) et
> [validation-demo-http-2026-10-22.md](validation-demo-http-2026-10-22.md).

## Défauts reproduits

- La formulation A fournie par l'utilisateur était routée vers B à cause du mot
  « redémarrages » et demandait une date.
- Les réponses courtes de clarification étaient traitées comme de nouvelles
  questions génériques ; au-delà de 32 messages, l'ancrage B pouvait disparaître.
- Le balayage quotidien prenait un log `phase=normal` pour une anomalie à cause
  du mot `INCIDENT`. La fenêtre de six heures imposait un pas d'environ 333 secondes
  et manquait la courte série CPU. Deux exécutions pouvaient aussi être mélangées.
- Les sélections JSON du fournisseur avaient un budget de 32 tokens et aucun
  schéma contraint.

## Validation automatisée

Commande depuis `agent/` :

```powershell
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD='1'
..\.venv\Scripts\python.exe -B -m pytest tests -q -p no:cacheprovider
```

Résultat final : **317 réussis, 5 ignorés**, un avertissement de dépréciation Starlette
préexistant. Les tests couvrent les formulations exactes, B → A → C JSON/SSE,
48 tours, les choix de ressources vérifiés, les clarifications, les dates naturelles,
les épisodes CPU séparés, les preuves bornées et le refus hors monitoring sans
appels sortants. Les transports des tests automatisés sont simulés, sauf les tests
HTTP loopback explicitement décrits dans leur fichier.

## Services locaux réels

`scripts/start.ps1` a démarré avec succès Docker Desktop/Kubernetes, Mimir, Loki,
FastAPI et Open WebUI, profil `mistral-api`. Le contrôle de disponibilité du
fournisseur a réussi. Le navigateur atteint l'écran de connexion Open WebUI ;
aucune session utilisateur authentifiée n'était disponible dans le navigateur contrôlé.
Après modification de l'affichage, `stop.ps1` a arrêté les processus suivis.
Un nouveau `start.ps1` a installé `tzdata` et préparé les ressources. Le contrôle
mémoire a refusé Open WebUI à 0,48 Gio puis, lors d'un nouvel essai, à 0,21 Gio
libres (seuil 0,5 Gio). Le script a nettoyé les tunnels et FastAPI qu'il venait
de lancer. Les services de monitoring et les conversations persistées ont été
conservés. L'interface ne pouvait donc pas être revalidée visuellement sur la
version finale.
La branche qui lance Docker Desktop quand il est arrêté a été vérifiée avec
des commandes PowerShell simulées : lancement caché unique, attente du moteur et
réutilisation sans doublon. Docker Desktop n'a pas été arrêté pour ce test.

Un harnais utilisant le code de production et le kubeconfig existant de lecture
seule a exécuté B → A → C → météo → « Quelles preuves ? », avec les vraies sources
Kubernetes/Mimir/Loki et un transport modèle remplacé en mémoire. Aucun export
d'observations vers un modèle externe n'a eu lieu pendant cet essai.

| Étape | HTTP | Mode | Résultat |
|---|---|---|---|
| B, formulation fournie | 200 | JSON | Chronologie, ressource correcte |
| A, formulation avec redémarrages/alertes | 200 | SSE | Bilan global actuel |
| C, incident précédent | 200 | JSON | Corrélation de la même ressource |
| Température d'aujourd'hui | 200 | SSE | Refus explicite, aucun appel modèle |
| Quelles preuves ? | 200 | SSE | Reprise de C |

Le rapport réel du pod fourni contient **83 observations**, dont **11 échantillons
CPU applicatifs**, dans la fenêtre `2026-10-19T18:20:45.054536Z` à
`2026-10-19T18:33:09.808210Z`. Il signale les événements expirés et l'absence des
séries CPU/mémoire historiques des conteneurs. Ces absences ne sont pas remplacées
par des valeurs inventées.

L'utilisateur a ensuite autorisé explicitement l'essai limité B → A → C avec
Mistral. L'essai sur FastAPI démarré par `start.ps1` a réussi en JSON et SSE,
avec `mistral-api` réel : B 3,23 s, A 0,53 s, C 1,27 s, hors sujet 0,02 s et
relance 0,89 s. Les cinq réponses ont eu HTTP 200, sans rejet de format ni
demande d'une nouvelle conversation. Les observations transmises ont été bornées
et masquées par le code de production ; aucune clé n'a été affichée.

## Affichage et validation du 21 octobre 2026

Les réponses A, B et C ont été réorganisées pour placer le résumé et les faits
significatifs avant les détails. Le fuseau par défaut est `Europe/Zurich`, avec
conversion IANA été/hiver et configuration `KUBEAI_DISPLAY_TIMEZONE`. La réponse B
réelle groupe les marqueurs locaux à 20:22:45, 20:24:15, 20:27:09, 20:28:39,
le pic CPU à 20:29:45 (0,0999 cœur du processus), la récupération à 20:30:09
et la fin à 20:31:09. La réponse C relie le CPU au second cycle, sans attribuer
une cause certaine ni affirmer une saturation ou un throttling du conteneur.
Les UTC originaux et toutes les références retenues restent dans la section
« Détails techniques / Preuves » et dans `/diagnose`.

Les tests ajoutés couvrent l'exemple UTC → heure suisse, les transitions de
mars et octobre, la répétition de 02:30 lors du passage à l'heure d'hiver,
plusieurs dates locales, la configuration du fuseau, l'échappement HTML des logs
et la traçabilité des preuves. Un client FastAPI en mémoire a rejoué B → A → C,
la météo et la relance sur les vraies sources Kubernetes/Mimir/Loki : cinq
HTTP 200. Un second essai sur ces sources avec la vraie API Mistral a réussi :
B 1,59 s, A 0,86 s, C 1,22 s, hors sujet 0,02 s et relance 1,66 s. Les réponses
B et C ont le texte français visible, sans timestamp ISO, et les références UTC
dans la section technique. Les appels modèle structurés n'ont pas été rejetés.

## Fichiers de cette correction

- `agent/app/conversation.py` et `models.py` : état conversationnel avant réduction,
  intentions et périmètre ; `api/chat.py` partage le parcours JSON/SSE.
- `services/incident_timeline.py` et `correlation.py` : résolution des paramètres,
  finesse temporelle, cycles et preuves conservées.
- `services/llm_service.py`, `diagnosis.py` et `prompts/system_prompt.txt` : choix
  structurés, budget, contrôle des valeurs et instructions de périmètre.
- `services/presentation.py`, `global_health.py`, `config.py`, `.env.example`,
  `agent/requirements.txt` et `scripts/common.ps1` : fuseau configurable et
  présentation des preuves. `scripts/setup.ps1` lance Docker Desktop quand il
  est installé mais arrêté.
- Tests dédiés : `test_conversation_regressions.py`, `test_scope.py`,
  `test_historical_refinement.py`, `test_llm_selection.py`,
  `test_presentation.py` ; les tests existants
  de sélection JSON et de redaction ont été adaptés aux paramètres renforcés.
