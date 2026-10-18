# API de l'agent KubeAI — version 0.2.0

L'API est accessible dans le cluster à `http://kubeai-agent.kubeai.svc.cluster.local:8000`.
Elle est en lecture seule et limitée au namespace configuré (`kubeai` par défaut).
Le contrat complet est généré par FastAPI sur `/openapi.json`, avec interface `/docs`.

| Méthode | Route | Rôle prévu |
|---|---|---|
| GET | `/health` | 200 si le processus répond ; aucune dépendance interrogée |
| GET | `/ready` | 200 si l'agent peut lister les pods ; 503 sinon |
| GET | `/cluster/status` | État Kubernetes et métriques CPU/mémoire des pods |
| GET | `/llm/status` | Vérifier la disponibilité du modèle Apertus auto-hébergé |
| GET | `/v1/models` | Publier le modèle virtuel `kubeai-apertus` pour Open WebUI |
| POST | `/v1/chat/completions` | Conversation enrichie des données réelles, JSON ou SSE |
| POST | `/diagnose` | Analyse d'une question et observation utilisée comme preuve |

`/ready` reste indépendant du moteur LLM : l'inventaire reste utile si le modèle
est arrêté. Les routes de chat retournent alors 503 avec la source `llm`.

## Chat et diagnostic

`POST /v1/chat/completions` accepte `model: kubeai-apertus`, une liste `messages`
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

Le moteur local est joint via `LLM_BASE_URL`, avec une clé lue dans
`LLM_API_KEY_FILE`. Le timeout de génération est de 180 secondes par défaut.
La route `/v1/models` publie le modèle configuré même si le moteur est arrêté ;
`/llm/status` teste effectivement sa disponibilité.

## GET /cluster/status

Aucun paramètre : le périmètre est fixé côté serveur par `KUBERNETES_NAMESPACE`
et son Role Kubernetes. L'opération OpenAPI s'appelle `get_cluster_status`.

La réponse JSON contient :

| Champ | Sens |
|---|---|
| `observed_at` | Date UTC de début de l'observation |
| `scope`, `namespace` | Toujours `namespace`, puis le namespace réellement observé |
| `health` | `healthy`, `degraded`, `unknown` ou `empty` selon l'état des pods |
| `data_status` | `complete` ou `partial`, indépendamment de la santé des pods |
| `summary` | Total, Ready, malsains, terminés avec succès, en suppression et redémarrages |
| `sources` | Disponibilité de Kubernetes et de Prometheus, avec messages éventuels |
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
- Prometheus indisponible ou réponse invalide : HTTP 200 avec inventaire Kubernetes,
  `data_status: partial`, source Prometheus `unavailable` et mesures `null`.
- Mesures incomplètes ou avertissements Prometheus : réponse partielle explicite.
- `/ready` reste disponible pendant une panne de Prometheus, car l'inventaire
  Kubernetes reste utile. `/health` est indépendant des deux services.

Les appels sortants ont un timeout de 5 secondes par défaut. Aucun endpoint ne
permet d'exécuter une requête PromQL arbitraire ou de modifier le cluster.

## Essai local

```bash
kubectl --context minikube port-forward -n kubeai service/kubeai-agent 8000:8000
```

Puis dans un autre terminal :

```bash
curl http://127.0.0.1:8000/cluster/status
```
