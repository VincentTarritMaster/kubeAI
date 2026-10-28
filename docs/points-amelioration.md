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
- **audit JSON** et envoi vers Loki (`job="kubeai-audit"`) ;
- **quota par clé** : 10 requêtes/minute, rafale 5, réponse 429 ;
- **420 tests agent** et **20 tests scripts Python** passent ;
- document consolidé [`evaluation.md`](evaluation.md).

## Points restants

### 1. Qualité multilingue et mesure formelle — priorité moyenne

B/C sont validés en français. Il reste à :

- vérifier B/C en anglais sur le même scénario ;
- documenter l’écart FR/EN observé sur A ;
- mesurer formellement les objectifs **≥ 90 % de faits corrects** et
  **< 60 s**, non certifiés aujourd’hui.

### 2. Livrables — priorité haute

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
- ❌ Orchestration des outils : le tool agent LangChain est intégré.
- ❌ Audit + quotas : implémentés en mémoire pour la démo locale.

## Bilan

Il reste **1 chantier technique** :

1. qualité multilingue et mesure formelle.

Et **1 chantier de livraison** :

2. captures, rapport, slides et push final.
