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

KubeAI est limité au monitoring Kubernetes. Une question hors périmètre (météo,
sport, cuisine, etc.) reçoit HTTP 200 avec un message français expliquant ce rôle,
en JSON comme en SSE. Aucun appel aux sources ou au modèle n'est effectué pour
ce refus. Les suivis courts d'un diagnostic restent acceptés.

Pour chaque requête, l'agent réduit automatiquement l'historique reçu à 32 messages
et 16 000 caractères : il conserve la dernière question, puis les tours récents
complets tant qu'ils tiennent dans cette limite. Une ancienne réponse trop longue
peut être écartée en gardant sa question, afin de conserver les paramètres d'un
incident repris plus tard. Cette réduction ne modifie pas la conversation enregistrée
dans Open WebUI. Chaque message retenu reste limité à 12 000 caractères ; si la
dernière question dépasse cette taille, l'API retourne HTTP 413 avec une consigne
lisible pour raccourcir cette question. La génération est limitée à 1 024 tokens
(512 par défaut). Le contexte source inclut
jusqu'à 20 pods, incidents prioritaires, et 12 lignes de logs de 400 caractères
au maximum sur les dix dernières minutes, pour au plus six pods. Une mesure ou
source manquante est conservée comme telle, sans valeurs fabriquées.

Avant cette réduction, le serveur replie les questions utilisateur en un état
local à la requête : dernière investigation et au plus quatre précisions récentes.
Cet état privé ne vient pas d'un champ fourni par le client et n'est pas partagé
entre utilisateurs. Il conserve le sujet B au-delà de 32 messages sans réinjecter
les anciens diagnostics comme preuves. Les réponses telles que « dans le namespace
kubeai », une date seule ou « le second » complètent l'investigation ; les choix
de ressources sont revérifiés dans les sources autorisées.

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

Le profil par défaut `mistral-api` est joint en HTTPS via `MISTRAL_BASE_URL` ; le modèle est
`MISTRAL_MODEL`. La clé vient de `MISTRAL_API_KEY` ou du Secret monté dans
`MISTRAL_API_KEY_FILE`. Le timeout de génération est de 60 secondes par défaut.
Le profil `local` utilise `LLM_BASE_URL`, `LLM_MODEL` et accepte une clé locale
facultative. Les anciens noms `mistral` et `apertus` sont acceptés comme alias.
Les routes `/v1/models`, `/v1/chat/completions`, `/diagnose` et `/llm/status`
exigent `Authorization: Bearer <KUBEAI_API_KEY>`. La clé doit être configurée
dans l'environnement du backend et n'est jamais renvoyée par l'API. `/v1/models`
publie uniquement les identifiants virtuels ; `/llm/status` contacte réellement
le fournisseur configuré.

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

## Investigation d'incident via le chat et /diagnose

Une question contenant « incident », « chronologie » ou « redémarré » emprunte
le parcours historique du chat JSON, du flux SSE et de `/diagnose`. Un nom de pod
et une date explicite suffisent. Si l'année est omise, l'agent prend la dernière
occurrence passée de ce jour en UTC. Si le namespace manque, il cherche le pod
dans l'inventaire et les événements Kubernetes accessibles en lecture seule ;
plusieurs correspondances donnent une seule question ciblée.
Une question de santé actuelle du cluster garde le parcours A même si elle demande
les redémarrages et les alertes. Sans date pour un pod identifié, B recherche la
dernière heure UTC et annonce ce choix. « Hier » et « aujourd'hui » sont acceptés.

Avec une date sans heure, l'agent balaie le jour UTC en quatre requêtes de six
heures au maximum par source. Un événement d'avertissement, un log d'erreur ou
une variation métrique mesurée peut servir à choisir une fenêtre de moins de
trente minutes pour l'analyse détaillée, avec un pas métrique de 30 secondes.
Les marqueurs normaux et de récupération ne sont pas des signaux d'anomalie.
Cette fenêtre est annoncée comme une fenêtre de
recherche, jamais comme l'heure supposée de l'incident. En l'absence de signal,
le rapport indique la couverture de la journée et les données manquantes.
Chaque requête Loki garde un namespace exact et filtre le pod lorsqu'il est identifié.

Une fenêtre complète reste acceptée : `début=2026-10-19T14:00:00+02:00
fin=2026-10-19T14:30:00+02:00`. Une heure seule, « ce matin », une fenêtre
sans fuseau, inversée ou de plus de six heures demandent une précision.

L'observation de `/diagnose` contient `scope: incident`, les bornes normalisées
en UTC, `timezone` fourni, `namespace`, `pod`, `timeline`, `current_pods`,
`sources` (dont événements et pods Kubernetes), `missing`, `warnings` et
`data_status`. Chaque entrée de `timeline`
porte `timestamp`, `type` (`événement`, `log`, `métrique`), `source`, `resource`,
`observation` et `reference`. Les entrées sont triées chronologiquement et
dédoublonnées. `current_pods` est un relevé actuel distinct : son nombre de
redémarrages cumulé ne date pas le redémarrage.

Mimir utilise `api/v1/query_range` sous le préfixe `/prometheus`, avec des
requêtes PromQL CPU `rate(...[2m])` et mémoire `container_memory_working_set_bytes`
agrégées par namespace et pod. Le pas est au moins 30 secondes, avec 20 séries
et 200 points au plus par mesure. Loki utilise `/loki/api/v1/query_range`,
un sélecteur de namespace et éventuellement de pod, les deux bornes en
nanosecondes, `direction=forward` et 50 lignes au plus. Les événements et pods
proviennent de l'inventaire Kubernetes borné existant. Toute limite atteinte
est signalée. Un `403`, timeout ou manque d'échantillons est indiqué sans
remplacer les faits absents par des zéros ou une cause supposée.

La réponse distingue incident, chronologie, constats, hypothèses, données
manquantes et vérifications manuelles en lecture seule. Mistral peut seulement
sélectionner une source déjà appuyée par la chronologie ; son texte libre n'est
pas publié. Sans clé, le rendu déterministe reste disponible.

Les réponses A, B et C affichent les dates en français dans le fuseau défini par
`KUBEAI_DISPLAY_TIMEZONE` (`Europe/Zurich` par défaut). La bibliothèque IANA
`tzdata` gère les changements d'heure. B groupe les événements significatifs
par date locale avec heures courtes et mention de l'offset ; une heure répétée
lors du passage à l'heure d'hiver porte son offset propre. C présente la
ressource, le problème observé, la corrélation, une hypothèse prudente et des
vérifications en lecture seule. Les horodatages UTC, requêtes, références et
limites restent dans « Détails techniques / Preuves » et dans l'observation
structurée de `/diagnose`. Le rendu ne modifie jamais les instants collectés.
La section secondaire utilise l'élément Markdown/HTML `<details>` ; son état
replié dépend du moteur d'affichage d'Open WebUI.

## Essai local

## Corrélation des preuves via le chat et /diagnose

Une question de lenteur, latence, corrélation, cause ou hypothèse emprunte le
parcours C avant l'investigation B. Les références « cet incident », « l'incident
précédent », « ce pod », « ses logs » et les suivis courts reprennent les paramètres
de la question d'incident précédente, même si une question A s'intercale et même
si la réponse B était une demande de précision. Une demande encore ambiguë renvoie
`scope: correlation`, `status: clarification_required` avec une question ciblée.
Le chat JSON, SSE et `/diagnose` utilisent le même service.

L'observation contient la chronologie bornée de B et `hypotheses`,
`correlation_limits`, `conclusion` (`hypothèses non confirmées` ou
`cause indéterminée`) et `data_status`. Chaque hypothèse expose `id`,
`description`, `supporting_observations` avec références, `resources`,
`interval`, `confidence` qualitative, `alternatives`, `contradictions` et
`recommended_checks`. Les faits et les hypothèses sont calculés par le backend.
Mistral ne peut que prioriser un identifiant déjà présent ; aucune prose libre
du modèle n'est ajoutée comme fait.

Les sélections A/B/C utilisent température 0 et un budget de 256 tokens. Mistral
reçoit un schéma JSON contraint ; le modèle local garde les paramètres compatibles
standards. Le backend vérifie les clés et valeurs permises et conserve son rapport
déterministe si le fournisseur échoue. Plusieurs cycles d'incident ne sont pas
mélangés : une hypothèse CPU doit posséder sa propre base, son pic et ses preuves.
La sélection des 100 observations conserve les phases, extrema et voisins des
phases nécessaires au diagnostic, y compris des logs tardifs.

Pour C, Mimir reçoit en plus des requêtes historiques PromQL sur
`kube_pod_container_status_restarts_total` et
`kube_pod_container_resource_limits` (CPU en cœurs et mémoire en octets).
Ces séries sont utilisées seulement si Mimir les retourne avec le namespace et
le pod demandés. Les cinq familles de requêtes restent dans une fenêtre de six
heures, avec au plus 20 séries et 400 points au total ; le pas augmente pour
respecter cette borne. Loki garde 50 lignes au plus, avec le filtre exact de
namespace et pod. Les listes Kubernetes conservent leurs plafonds de l'inventaire.

La configuration active ne collecte **aucune latence p95 ni erreur HTTP NestJS**.
Une hausse CPU seule ne démontre ni saturation ni cause d'une lenteur. Une
instrumentation applicative HTTP, son scrape et la vérification de ses labels
sont nécessaires avant d'exploiter la règle CPU + latence en situation réelle.
Les règles qualitatives et le protocole de démonstration figurent dans
`docs/demo-scenarios.md`.

## Essai local

```bash
kubectl --context minikube port-forward -n kubeai service/kubeai-agent 8000:8000
```

Puis dans un autre terminal :

```bash
curl http://127.0.0.1:8000/cluster/status
```
