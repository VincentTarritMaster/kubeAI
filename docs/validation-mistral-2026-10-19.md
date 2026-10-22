# kubeAI — contrôle de l'étape 3 : Mistral API

Projet collectif de Kevin, Vincent et Anass. Contrôle réalisé le 19 octobre 2026
sur Windows, avec 8 Go de RAM. **Aucun appel réel à Mistral, aucun cluster ni
moteur local n'a été lancé.**

> Rapport historique de l'étape 3. Des essais réels Mistral et des scénarios
> A/B/C ont ensuite été réalisés ; voir l'[état actuel](../README.md#état-du-projet-et-démonstrations).

## 1. Configuration et modèle retenus

La branche est `integration/kubeai`, au commit de départ
`23260c91b45ed202e6aef61576263d6cbd1cb615`. Mistral API est le fournisseur
principal (`LLM_PROVIDER=mistral`). La base est
`https://api.mistral.ai/v1`, le modèle `mistral-small-2603` et le timeout 60 s.
La [fiche officielle de Mistral Small 4](https://docs.mistral.ai/fr/models/mistral-small-4-0-26-03)
confirme l'identifiant et Chat Completions. L'accès du compte du groupe et la
disponibilité régionale n'ont pas été vérifiés avec une vraie clé.

Le contrat conserve les six routes demandées (`/health`, `/ready`,
`/cluster/status`, `/diagnose`, `/v1/models`, `/v1/chat/completions`) ainsi que
`/llm/status`. Le modèle virtuel par défaut est `kubeai` ; l'identifiant
historique `kubeai-apertus` reste accepté.

Avant modification, le diff Mimir attendu de l'étape 2 était présent : Alloy
envoie ses métriques à Mimir sur `/api/v1/push`, l'agent interroge l'API PromQL
sur `/prometheus/api/v1/query`, et le rendu Kustomize utilise Mimir à la place
du serveur Prometheus autonome. Les anciens manifests, PVC et rapports sont
conservés. `git diff --check` était propre et aucun fichier en conflit ou
commit récent local n'a été constaté. Aucun changement inattendu n'a été
identifié par rapport au relevé de fin d'étape 2.

## 2. Fichiers modifiés ou créés depuis la fin de l'étape 2

Un instantané SHA-256 de 89 fichiers suivis ou déjà présents a été conservé
avant l'édition. Les **21 fichiers préexistants modifiés pendant cette étape**
sont exactement :

```text
.env.example
README.md
agent/README.md
agent/app/api/chat.py
agent/app/config.py
agent/app/main.py
agent/app/models.py
agent/app/prompts/system_prompt.txt
agent/app/services/diagnosis.py
agent/app/services/health_answer.py
agent/app/services/llm_service.py
agent/tests/test_chat.py
docs/api-contract.md
docs/architecture.md
docs/decisions.md
k8s/agent-configmap.yaml
k8s/agent-deployment.yaml
k8s/open-webui.yaml
llm/README.md
scripts/start-local-ai.sh
start.sh
```

Les **5 nouveaux fichiers livrables** de cette étape sont :

```text
agent/app/services/privacy.py
agent/tests/conftest.py
agent/tests/test_mistral.py
docs/mistral-api.md
docs/validation-mistral-2026-10-19.md
```

Des artefacts locaux de contrôle, ignorés par Git, sont dans
`.cache/mistral-validation/` : `baseline.json`, 89 copies sous `before/`,
`rendered.yaml` et `check-static.py`. Ils servent uniquement à comparer les
étapes et le rendu Kustomize ; ils ne sont pas des livrables à commiter.

Les rapports des étapes 1 et 2, les manifests/configurations Mimir/Alloy/Loki,
les tests Mimir, le PDF d'origine et les autres fichiers protégés (21 fichiers
contrôlés par hash) sont identiques à l'état initial de cette étape. Aucun
conflit Git n'est présent. Le dépôt conserve les modifications non commitées
des étapes précédentes ; aucun nettoyage ni fusion n'a été effectué.

## 3. Code de Vincent réutilisé

Le backend FastAPI, les clients Kubernetes/Mimir/Loki, la préparation de
contexte, les routes OpenAI compatibles avec Open WebUI, le traitement SSE et
le client local `LLMService` ont été conservés et adaptés. La sélection explicite
`LLM_PROVIDER=apertus` lit toujours `LLM_BASE_URL`, `LLM_MODEL`,
`LLM_API_KEY_FILE` et le timeout historique ; les 15 tests historiques du chat
continuent de passer. Les scripts `scripts/local-llm.py` et
`scripts/configure-local-llm.py` sont conservés sans modification. `start.sh`
ne démarre plus le moteur selon la présence d'un cache et
`scripts/start-local-ai.sh` indique la procédure manuelle.

## 4. Variables d'environnement et Secret

`MISTRAL_API_KEY` est accepté par environnement privé ; à défaut, la clé est
lue dans `MISTRAL_API_KEY_FILE`. Le Deployment monte en lecture seule le
Secret facultatif `kubeai-mistral` à
`/var/run/secrets/kubeai-mistral/api-key`. Le ConfigMap ne contient que le
fournisseur, l'URL, le modèle, le chemin du fichier et le timeout. Aucun
Secret réel ni manifeste contenant une valeur de clé n'a été ajouté. Une
clé absente rend la génération indisponible avec un message explicite ;
`/cluster/status` reste utilisable. Le détail se trouve dans
[la configuration Mistral](mistral-api.md).

## 5. Tests exécutés

Commande PowerShell exécutée depuis `agent/` :

```powershell
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD = '1'
..\.venv\Scripts\python.exe -B -m pytest tests/test_api.py tests/test_chat.py tests/test_mimir.py tests/test_mistral.py -q -p no:cacheprovider
```

**Résultat final : 64 réussis, 0 échoué, 0 ignoré dans cette sélection.**
Les cinq tests live nécessitant Kubernetes n'ont pas été exécutés et ne sont
pas comptés comme réussis. Un avertissement préexistant de dépréciation
Starlette/httpx apparaît. Les transports externes HTTP sont bloqués dans
les tests unitaires ; les réponses Mistral, Kubernetes, Mimir et Loki sont
simulées. Un contrôle syntaxique a compilé 27 fichiers Python. La syntaxe
de `start.sh` et `scripts/start-local-ai.sh` a été validée avec `bash -n`.

## 6. Chat JSON et SSE

Les tests simulés couvrent JSON normal, découverte du modèle, alias historique,
SSE progressif, pied de page ajouté avant `finish_reason` et `[DONE]`, et
masquage d'une clé découpée entre deux trames. Ils couvrent aussi clé absente,
401/403, 429, 500/503, timeout, erreur de connexion, JSON invalide, flux
malformé, vide ou interrompu, monitoring partiel et Kubernetes indisponible. Les
erreurs avant ouverture du flux retournent HTTP 503 ; une interruption après
ouverture est signalée par une trame SSE d'erreur, sans fausse fin normale.
Les réponses de santé déterministes utilisent cinq rubriques françaises sans
appel au fournisseur.

## 7. Compatibilité Open WebUI

Le rendu Kustomize conserve `OPENAI_API_BASE_URL` vers le backend FastAPI en
`/v1`. Le modèle par défaut de WebUI est `kubeai`, la locale `fr-FR`, et
les suggestions sont françaises. `/v1/models` publie l'identifiant courant
et l'alias historique. Les contrats JSON et SSE sont testés via `TestClient`.
Le rendu de **31 ressources** Kustomize a réussi, avec Secret facultatif
monté et sans clé dans le ConfigMap. L'interface dans un navigateur et le
déploiement réel n'ont pas été testés.

## 8. Confidentialité et sécurité des données

Le contexte externe comprend la question actuelle, au plus quatre anciennes
questions de l'utilisateur et une observation fraîche compacte : jusqu'à
20 pods, 12 lignes de logs de 400 caractères sur dix minutes et six pods.
Les anciennes réponses du modèle, UID/nœuds, autres namespaces et logs complets
ne sont pas transmis. Le filtre masque les clés et identifiants détectables
dans les observations, les questions et les réponses. L'API n'expose pas le
corps des erreurs fournisseur et ne journalise pas `MISTRAL_API_KEY`.

Le filtrage est une mesure de réduction, **pas une anonymisation garantie**.
Les noms de pods et le texte libre peuvent encore contenir des données
sensibles méconnues. Le point d'accès global ne garantit pas de région
d'inférence ; le choix régional et l'utilisation de données sensibles restent
à valider collectivement. Voir les [conditions régionales officielles](https://docs.mistral.ai/inference/regional-inference).

## 9. Git et vérifications statiques

`git status --short --branch` indique `integration/kubeai`, 29 fichiers suivis
modifiés cumulés depuis le commit de départ, les nouveaux fichiers des étapes
1 à 3 et aucun conflit. `git diff --stat` indique 29 fichiers suivis,
728 insertions et 341 suppressions **cumulées** depuis le commit de départ ;
il ne compte pas les nouveaux fichiers non suivis. Le contrôle SHA-256 distingue
les 21 modifications de cette étape. `git diff --check` est propre après
normalisation des fins de ligne Windows. Le rendu Kustomize et les références
Mistral/Open WebUI ont été contrôlés statiquement, sans cluster.

## 10. Limites, difficultés et arrêt

Le bac à sable Windows bloquait la socket locale utilisée par `TestClient` et
l'initialisation de Git Bash. Les mêmes tests et `bash -n` ont réussi avec
l'autorisation locale nécessaire ; aucun transport externe n'a été ouvert.
Le modèle est confirmé par la documentation, mais sa disponibilité pour le
compte et la région du groupe reste à vérifier. Les performances, coûts réels,
comportements linguistiques et qualité factuelle exigent un essai avec une
vraie clé et des données approuvées. Ni l'ingestion Mimir réelle, ni le
déploiement WebUI/Kubernetes, ni l'hébergement régional n'ont été validés.

**Arrêt à la fin de l'étape 3.** Aucun commit, push, événement Kubernetes,
scénario A/B/C, Grafana ou déploiement cloud. La prochaine décision du groupe
porte sur la région Mistral et les données permises pour un essai réel, puis
sur la validation en cluster des étapes 2 et 3.
