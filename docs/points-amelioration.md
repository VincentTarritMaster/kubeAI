# Points d’amélioration — décisions du 27 octobre 2026

Ce document fixe le périmètre accepté avec le professeur et la liste des points
qui restent réellement à traiter.

## Décisions du professeur

- **Alertes : abandonnées.** Le professeur a demandé de laisser tomber les
  règles d’alerte et Alertmanager. `alerts=not_configured` est donc assumé et ne
  doit plus être présenté comme un oubli.
- **Cloud : non obligatoire** tant que le projet tourne en local. Le déploiement
  Docker Desktop / Minikube reste le mode de démonstration.
- **Sécurité cloud : hors périmètre** en l’absence de déploiement cloud.
- **Équipe : 3 étudiants accepté.** Le projet n’a pas à ajouter un quatrième
  rôle ; la mention « 4 étudiants » de la fiche est une hypothèse de départ,
  remplacée par l’accord du professeur.

## Points restants

### 1. Évaluation consolidée — priorité haute

Produire `docs/evaluation.md` avec :

- exactitude : A/B/C attendu vs observé ;
- fiabilité : 360 tests + 6 loopback + exécutions réelles ;
- sécurité : RBAC, auth, redaction, allowlist, `nodes/proxy` ;
- limites : cAdvisor, rétention, données synthétiques ;
- mesure des objectifs de la fiche : **≥ 90 % de faits vérifiés corrects** et
  **réponse < 60 s**.

C’est le point le plus rentable pour le barème « tests/évaluation ».

### 2. Audit et quotas — priorité moyenne

La fiche demande de journaliser les accès sans secrets (utilisateur, outil,
portée, période, résultat, délai) et de prévoir des quotas de requêtes.
Aujourd’hui :

- l’agent utilise une clé Bearer partagée ;
- aucun journal d’audit structuré n’est produit ;
- aucune limite de requêtes côté agent n’est appliquée.

À ajouter ou à documenter comme limite locale assumée.

### 3. Orchestration des outils — priorité moyenne

LangChain Core est intégré pour la chaîne de sélection/validation, mais :

- les outils Kubernetes/Mimir/Loki sont choisis par le routeur FastAPI ;
- le LLM ne fait que prioriser une hypothèse.

C’est défendable, mais la fiche parle d’un « agent qui choisit et exécute des
outils ». Il faut soit l’assumer explicitement dans le rapport, soit ajouter un
routeur d’outils borné.

### 4. Livrables — priorité haute

- captures A/B/C dans Open WebUI ;
- rapport et slides ;
- répétition de la démonstration ;
- commit/push final.

## Points supprimés

- ❌ Déploiement cloud (non requis si local).
- ❌ Alertes / Alertmanager (consigne du professeur).
- ❌ Sécurité cloud (TLS/Ingress/NetworkPolicies) tant qu’il n’y a pas de cloud.
- ❌ Quatrième étudiant / rôle 4 (3 étudiants acceptés).

## Bilan

Il reste **3 chantiers techniques** :

1. évaluation consolidée ;
2. audit + quotas ;
3. orchestration des outils.

Et **1 chantier de livraison** :

4. captures, rapport, slides, commit/push.

Soit **4 points**, dont 3 techniques et 1 livrable.
