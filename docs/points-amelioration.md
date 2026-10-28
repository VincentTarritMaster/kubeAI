# Points d’amélioration — état au 28 octobre 2026

Ce document fixe le périmètre accepté avec le professeur et la liste des points
qui restent réellement à traiter.

## Décisions du professeur

- **Alertes : abandonnées.** Le professeur a demandé de laisser tomber les
  règles d’alerte et Alertmanager. `alerts=not_configured` est assumé.
- **Cloud : non obligatoire** tant que le projet tourne en local.
- **Sécurité cloud : hors périmètre** en l’absence de déploiement cloud.
- **Équipe : 3 étudiants accepté.**
- **Deux clusters, tous namespaces, FR/EN, tool agent** : demandés et intégrés.

## Ce qui est fait

- deux clusters Minikube séparés, réseau privé et NodePort ;
- Alloy sur tous les namespaces du cluster observé ;
- RBAC read-only pour les pods, logs et cAdvisor ;
- Mimir/Loki sur la supervision ;
- agent FastAPI + tool agent LangChain Core ;
- A/B/C validés en conditions réelles ;
- tool-calling Mistral avec retry, message explicite et synthèse de secours ;
- garde-fou sur les fenêtres UTC explicites ;
- **417 tests agent** et **20 tests scripts Python** passent ;
- document consolidé [`evaluation.md`](evaluation.md).

## Points restants

### 1. Audit et quotas — priorité moyenne

Toujours absent :

- journal d’audit structuré : utilisateur, outil, portée, période, résultat,
  délai ;
- quota de requêtes côté agent.

Deux options :

- **A. Implémenter** : logs structurés + limite par clé/IP + tests ;
- **B. Documenter comme limite locale assumée** dans le rapport.

C’est le dernier point technique de la fiche.

### 2. Qualité multilingue et mesure formelle — priorité moyenne

B/C sont validés en français. Il reste à :

- vérifier B/C en anglais sur le même scénario ;
- documenter l’écart FR/EN observé sur A ;
- mesurer formellement les objectifs **≥ 90 % de faits corrects** et
  **< 60 s**, non certifiés aujourd’hui.

### 3. Livrables — priorité haute

- captures A/B/C en français ;
- captures A/B/C en anglais ;
- captures des deux clusters ;
- rapport et slides ;
- répétition de la démonstration ;
- commit/push final après les derniers changements.

## Points supprimés

- ❌ Déploiement cloud (non requis si local).
- ❌ Alertes / Alertmanager (consigne du professeur).
- ❌ Sécurité cloud (TLS/Ingress/NetworkPolicies) tant qu’il n’y a pas de cloud.
- ❌ Quatrième étudiant / rôle 4 (3 étudiants acceptés).
- ❌ Orchestration des outils : le tool agent LangChain est maintenant intégré.

## Bilan

Il reste **2 chantiers techniques** :

1. audit + quotas ;
2. qualité multilingue et mesure formelle.

Et **1 chantier de livraison** :

3. captures, rapport, slides et push final.
