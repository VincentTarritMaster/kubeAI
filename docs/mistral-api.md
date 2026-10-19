# kubeAI — configuration Mistral API

Le projet collectif de Kevin, Vincent et Anass utilise Mistral API comme
fournisseur principal du chat. Le chemin est Open WebUI → FastAPI → observations
Kubernetes/Mimir/Loki → Mistral API → réponse. Le modèle n'a aucun accès direct
au cluster ni à des outils d'écriture.

## Modèle et paramètres

La configuration propose `mistral-small-2603` (Mistral Small 4). La
[fiche officielle du modèle](https://docs.mistral.ai/fr/models/mistral-small-4-0-26-03)
indique cet identifiant et la prise en charge de Chat Completions. Le backend
appelle l'[API `POST /v1/chat/completions`](https://docs.mistral.ai/api) avec
`messages`, `model`, `temperature`, `max_tokens` et éventuellement `stream: true`.
Cette disponibilité documentaire ne prouve ni l'accès du compte du groupe au
modèle ni sa disponibilité sur une région donnée : cela reste à vérifier après
autorisation d'utiliser une vraie clé.

| Variable | Valeur de référence | Usage |
|---|---|---|
| `LLM_PROVIDER` | `mistral-api` | Sélection du profil ; `local` sélectionne l'endpoint compatible OpenAI |
| `MISTRAL_BASE_URL` | `https://api.mistral.ai/v1` | Base HTTPS, sans identifiants dans l'URL |
| `MISTRAL_MODEL` | `mistral-small-2603` | Modèle demandé à l'API |
| `MISTRAL_API_KEY` | vide | Clé reçue par environnement privé, prioritaire sur le fichier |
| `MISTRAL_API_KEY_FILE` | `/var/run/secrets/kubeai-mistral/api-key` | Fichier Secret Kubernetes monté en lecture seule |
| `LLM_TIMEOUT_SECONDS` | `60` | Délai de l'appel au fournisseur |

`.env.example` contient une valeur manifestement fictive. En développement
local, le backend charge le `.env` racine seulement avec `KUBEAI_ENV=development` ;
les variables déjà définies dans le processus restent prioritaires. Kubernetes
n'active pas ce mode. Aucune clé réelle ne doit être écrite dans Git, une ConfigMap,
la documentation ou les logs. Le Deployment monte le Secret
facultatif `kubeai-mistral` ; s'il manque, une demande de génération renvoie
une erreur explicite, alors que `/health`, `/ready` et `/cluster/status`
restent utilisables. La route `/llm/status` effectue un véritable appel à
`GET /v1/models` avec la clé configurée ; elle n'est pas invoquée au démarrage.

Après accord du groupe pour un essai réel, préparer une clé dans un fichier
**hors du dépôt**, puis créer le Secret depuis une machine autorisée. Exemple
PowerShell sans placer la valeur dans l'historique des commandes :

```powershell
$KeyFile = 'C:\chemin-prive\mistral-api-key.txt'
kubectl --context minikube -n kubeai create secret generic kubeai-mistral --from-file="api-key=$KeyFile"
```

Le namespace doit déjà exister. En cas de rotation, mettre à jour le Secret et
redémarrer l'agent ; le fichier monté est relu à chaque requête. Les tests
unitaires utilisent `MockTransport` et des clés fictives ; ils n'utilisent pas
ce Secret et ne font aucun appel réseau au fournisseur.

## Données transmises et limites du filtrage

Pour une question nécessitant le modèle, le backend envoie uniquement son prompt
de règles, la question actuelle, au plus quatre anciennes questions de l'utilisateur
et une observation fraîche du namespace autorisé. Les réponses antérieures de
l'assistant, les autres namespaces, les UID et nœuds des pods, les logs complets
et les Secrets Kubernetes ne sont pas inclus. L'observation contient l'heure du
relevé, l'état et le résumé des pods, l'état des sources, les avertissements,
au plus 20 pods avec les incidents prioritaires, leurs états et métriques
CPU/mémoire, puis au plus 12 lignes de 400 caractères sur dix minutes et six
pods. Une source manquante reste marquée indisponible et une mesure absente reste
`null`.

Le filtre masque les valeurs connues de la clé, champs structurés sensibles,
Bearer/JWT/clés privées détectables, mots de passe explicites, courriels, adresses
IPv4 et identifiants explicites. Les sorties du modèle et l'observation de
`/diagnose` passent aussi par ce filtre. Les noms de pods et les logs peuvent
toutefois contenir des données personnelles ou des secrets sous une forme non
reconnue. Ce filtrage est une réduction de risque, **pas une anonymisation**.
Open WebUI persiste également les conversations sur son PVC local.

Le [point d'accès global](https://docs.mistral.ai/inference/regional-inference)
`api.mistral.ai` ne garantit pas une géographie d'inférence. Un point d'accès
européen `api.eu.mistral.ai` existe, avec disponibilité de modèles variable et
surcoût ; l'inférence régionale ne régionalise pas nécessairement les données
du plan de contrôle. Le groupe doit choisir la région, vérifier l'accès au
modèle, les conditions contractuelles et les catégories de données autorisées.
Avant cette décision, les essais réels doivent se limiter à des données
synthétiques ou explicitement autorisées. La [rétention zéro](https://docs.mistral.ai/admin/monitor-comply/zero-data-retention)
est un contrôle distinct, soumis à éligibilité et activation ; il n'est pas
présumé actif pour ce projet.

## Contrat du chat

Open WebUI garde son URL interne vers FastAPI et sélectionne le modèle virtuel
`kubeai`. L'identifiant historique `kubeai-apertus` est encore accepté pour
les conversations sauvegardées ; il désigne le fournisseur actuellement actif.
Le serveur conserve `/v1/models`, `/v1/chat/completions` JSON et SSE ainsi que
`/diagnose`. Les réponses déterministes de santé sont produites sans appel LLM.
Les erreurs 401/403, 429, 5xx, timeout, connexion et réponse invalide sont
converties en messages français sans corps fournisseur ni clé. Aucune relance
automatique ne risque de multiplier les appels facturables.

Le prompt demande cinq rubriques françaises : état, preuves horodatées et
sources, hypothèses, informations manquantes, vérifications. Les réponses LLM
doivent être contrôlées sur des données réelles approuvées : un prompt ne
garantit ni l'exactitude, ni l'exhaustivité, ni le respect parfait du format.
Les événements Kubernetes, Grafana, les démonstrations et le cloud sont hors
du périmètre de cette étape.

## Profil Apertus facultatif

Le code local de Vincent reste disponible avec `LLM_PROVIDER=local` (l'ancien
alias `apertus` est encore accepté). Dans ce profil, `LLM_BASE_URL`, `LLM_MODEL`,
`LLM_API_KEY_FILE` et `LLM_TIMEOUT_SECONDS` configurent un endpoint compatible
OpenAI. `start.sh` ne télécharge ni
ne démarre le moteur. Les commandes manuelles figurent dans
[le profil Apertus](../llm/README.md).
