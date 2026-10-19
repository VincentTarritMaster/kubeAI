# API de l'agent kubeAI — version 0.3.0

L'API est accessible dans le cluster à `http://kubeai-agent.kubeai.svc.cluster.local:8000`.
Elle est en lecture seule. `/cluster/status` reste limité au namespace configuré
(`kubeai` par défaut) ; `/cluster/inventory` couvre le cluster.
Le contrat complet est généré par FastAPI sur `/openapi.json`, avec interface `/docs`.

Les métriques sont lues dans Mimir avec le client compatible Prometheus existant.
`MIMIR_URL` contient la base `http://mimir.monitoring.svc:9009/prometheus` ; les
requêtes instantanées utilisent ainsi `/prometheus/api/v1/query`. La variable
`PROMETHEUS_URL` reste acceptée en repli pour les configurations antérieures.

| Méthode | Route | Rôle prévu |
|---|---|---|
| GET | `/health` | 200 si le processus répond ; aucune dépendance interrogée |
| GET | `/ready` | 200 si l'agent peut lister les pods ; 503 sinon |
| GET | `/cluster/status` | État Kubernetes et métriques CPU/mémoire des pods |
| GET | `/cluster/inventory` | Nœuds, pods de tous les namespaces et événements Kubernetes |
| GET | `/llm/status` | Vérifier le modèle du fournisseur configuré ; effectue un appel au fournisseur |
| GET | `/v1/models` | Publier `kubeai` et l'alias historique `kubeai-apertus` pour Open WebUI |
| POST | `/v1/chat/completions` | Conversation enrichie des données réelles, JSON ou SSE |
| POST | `/diagnose` | Analyse d'une question et observation utilisée comme preuve |

`/ready` reste indépendant du fournisseur LLM : l'inventaire reste utile sans clé.
Les routes de chat qui nécessitent une génération retournent alors 503 avec la source `llm`.

## Chat et diagnostic

`POST /v1/chat/completions` accepte `model: kubeai` (ou l'alias historique
`kubeai-apertus`), une liste `messages`
(texte avec rôles user/assistant/system) et `stream: true` pour le streaming SSE.
L'agent impose ses propres instructions et collecte une observation actuelle avant
chaque génération. Les options supplémentaires non implémentées sont ignorées ;
les outils, images, fichiers et réponses structurées ne sont pas pris en charge.

La conversation est limitée à 32 messages, 16 000 caractères au total, 12 000 par
message ; la génération à 1 024 tokens (512 par défaut). Le contexte source inclut
jusqu'à 20 pods, incidents prioritaires, et 12 lignes de logs de 400 caractères
au maximum sur les dix dernières minutes, pour au plus six pods. Une mesure ou
source manquante est conservée comme telle, sans valeurs fabriquées.

Les réponses JSON suivent le format Chat Completions. En SSE, les événements
`data:` transportent des `chat.completion.chunk` puis `[DONE]`. Un pied de page
déterministe indique namespace, date d'observation, sources et limites. Une erreur
du modèle avant le streaming donne 503 ; une interruption après son démarrage
est signalée par un événement d'erreur, sans présenter une fin normale.

`POST /diagnose` accepte `{"question":"Quels pods ont un problème ?"}` et retourne
`model`, `answer`, `observation` et `usage`. L'observation comprend l'inventaire
compact, les métriques, les extraits de logs et l'état des sources. Les données de
logs sont non fiables comme instructions ; le prompt demande au modèle d'en tenir
compte. Les résultats restent à vérifier, car le LLM peut se tromper.

Pour la question « Quel est l'état de santé de mon cluster ? », le même parcours
JSON/SSE et `/diagnose` utilisent l'inventaire global (`scope: cluster`) et les
sommes CPU/mémoire des conteneurs observés dans Mimir. Le bilan déterministe
porte sur tous les namespaces. Les messages bruts d'événements ne sont pas
transmis à Mistral ; celui-ci ne peut sélectionner qu'une priorité étayée par
le relevé. Sans clé ou si le fournisseur échoue, une réponse française avec
faits, preuves, limites et vérifications en lecture seule reste disponible.
Voir [les règles de la démonstration A](demo-scenarios.md).

Mistral est joint en HTTPS via `MISTRAL_BASE_URL` ; le modèle est
`MISTRAL_MODEL`. La clé vient de `MISTRAL_API_KEY` ou du Secret monté dans
`MISTRAL_API_KEY_FILE`. Le timeout de génération est de 60 secondes par défaut.
Le profil Apertus conserve ses variables `LLM_*` si `LLM_PROVIDER=apertus`.
La route `/v1/models` publie les identifiants virtuels même sans clé ;
`/llm/status` contacte réellement le fournisseur configuré.

Le backend ne transmet au modèle que la question courante, au plus quatre
questions antérieures et l'observation compacte. Il n'envoie pas les réponses
anciennes de l'assistant. Les secrets et identifiants détectables sont masqués
au mieux ; cette mesure ne garantit pas l'anonymisation. Voir
[les données transmises et la décision régionale](mistral-api.md).

## GET /cluster/status

Aucun paramètre : le périmètre reste fixé côté serveur par `KUBERNETES_NAMESPACE`.
L'opération OpenAPI s'appelle `get_cluster_status`.

La réponse JSON contient :

| Champ | Sens |
|---|---|
| `observed_at` | Date UTC de début de l'observation |
| `scope`, `namespace` | Toujours `namespace`, puis le namespace réellement observé |
| `health` | `healthy`, `degraded`, `unknown` ou `empty` selon l'état des pods |
| `data_status` | `complete` ou `partial`, indépendamment de la santé des pods |
| `summary` | Total, Ready, malsains, terminés avec succès, en suppression et redémarrages |
| `sources` | Disponibilité de Kubernetes et de Mimir (clé `mimir`), avec messages éventuels |
| `warnings` | Données manquantes et avertissements des sources |
| `pods` | Inventaire trié par nom |

Chaque pod expose son nom, namespace, UID, nœud, phase, condition Ready, état de
santé, nombre de redémarrages, conteneurs, problèmes observés et métriques.
Les conteneurs applicatifs et init sont distingués. Les états d'attente/terminaison
et la raison de la terminaison précédente sont conservés, par exemple
`CrashLoopBackOff` et `OOMKilled`. Les redémarrages sont un cumul historique,
pas un diagnostic de panne actuelle.

Les pods Succeeded sont `completed`, ceux en suppression `terminating` : ils ne
dégradent pas à eux seuls la santé. Un pod Pending/Failed ou Running sans Ready
est `unhealthy`. Un état inconnu est signalé. Un namespace sans pod est `empty`.
La réponse ne conclut pas sur les autres namespaces, les nœuds ou la disponibilité
fonctionnelle de l'application.

### Mesures et données manquantes

`metrics.cpu_cores` est la somme des débits CPU des conteneurs en cours, calculés
par `rate(...[2m])`. `0.1` signifie un dixième de cœur, pas 10 % de la capacité
totale du cluster. `metrics.memory_working_set_bytes` est leur working set cumulé
en octets. Les conteneurs sandbox sont exclus.

Les requêtes ne retiennent que les échantillons cAdvisor des 60 dernières secondes
(seuil configurable). Les UID des pods et identifiants des conteneurs en cours
servent à exclure les mesures de leurs prédécesseurs. La somme d'une mesure n'est
retournée que si elle est disponible pour tous les conteneurs en cours.

Une mesure absente est `null`, jamais remplacée par zéro. `metrics.status` vaut
`available`, `partial`, `unavailable` ou `not_applicable` lorsqu'aucun conteneur
n'est en cours. Un pod récent peut manquer de CPU jusqu'au deuxième scrape.
La mesure est issue du monitoring et peut donc précéder l'état Kubernetes observé.

### Pannes des dépendances

- Kubernetes indisponible ou accès refusé : HTTP 503, avec `detail.source` et
  `detail.message`. Aucun inventaire vide présenté comme un succès.
- Mimir indisponible ou réponse invalide : HTTP 200 avec inventaire Kubernetes,
  `data_status: partial`, source `mimir` `unavailable` et mesures `null`.
- Mesures incomplètes ou avertissements de l'API Mimir : réponse partielle explicite.
- `/ready` reste disponible pendant une panne de Mimir, car l'inventaire
  Kubernetes reste utile. `/health` est indépendant des deux services.

Les appels sortants ont un timeout de 5 secondes par défaut. Aucun endpoint ne
permet d'exécuter une requête PromQL arbitraire ou de modifier le cluster.

## GET /cluster/inventory

L'opération `get_cluster_inventory` lit les ressources Kubernetes de tous les
namespaces, sans appeler Mimir, Loki ou Mistral. Le ServiceAccount ne possède que
`list` sur les nœuds, pods et événements (`apiGroups: [""]`). Aucun Secret,
log Kubernetes, accès `exec` ou verbe de modification n'est accordé.

La réponse contient `scope: cluster`, `observed_at`, `health`, `data_status`,
`summary`, `sources`, `warnings`, `nodes`, `pods` et `events`. Les nœuds indiquent
`ready: true`, `false` ou `null` et `health: Ready`, `NotReady` ou `Unknown` ;
les pods portent leur namespace, état, liste de conteneurs et nombre de
redémarrages (`null` si la source ne le fournit pas). Les événements exposent motif, message,
type, objet concerné et horodatage UTC. Ils sont triés du plus récent au plus
ancien ; ceux sans date viennent à la fin. Les messages sont bornés et les secrets
détectables sont masqués au mieux.

Chaque source (`nodes`, `pods`, `events`) vaut `ok`, `partial` ou `unavailable`.
Un `403`, un timeout ou une réponse invalide affecte uniquement cette source :
sa liste et ses compteurs valent `null`, jamais une liste vide présentée comme
saine. `data_status` vaut `complete`, `partial` ou `unavailable` ; `health` vaut
`healthy`, `degraded` ou `unknown`. Un nœud `NotReady` ou un pod malsain connu
donne `degraded`. Des données nécessaires manquantes donnent `unknown`.

La collecte utilise des pages de 200 éléments au plus, plafonnées à 500 nœuds,
2 000 pods, 500 événements et 25 pages par ressource. Atteindre une limite
marque la source `partial` ; les compteurs portent uniquement sur les éléments
collectés. Le timeout HTTP reste configurable (`UPSTREAM_TIMEOUT_SECONDS`,
5 secondes par appel par défaut). Cette route ne modifie pas le contrat de
`/cluster/status` ni l'observation fournie au modèle.

## Essai local

```bash
kubectl --context minikube port-forward -n kubeai service/kubeai-agent 8000:8000
```

Puis dans un autre terminal :

```bash
curl http://127.0.0.1:8000/cluster/status
```
