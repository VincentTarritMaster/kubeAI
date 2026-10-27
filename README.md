# P5-G17 | KubeAI

Projet collectif des **3 étudiants** du groupe P5-G17 : un assistant d'observabilité
Kubernetes qui répond à des questions de santé et d'incident à partir de
preuves datées. Le modèle technique exposé à Open WebUI est `kubeai`. Le backend
combine **FastAPI** et des outils **LangChain Core** : le modèle comprend la
conversation, choisit ses fonctions de lecture et rédige une réponse fondée sur
leurs résultats, dans la langue de la question.

## Démarrage — deux clusters séparés

Prérequis : Docker Desktop ouvert, Minikube, kubectl et Python 3. Pour Apertus,
préparer et démarrer le moteur sur le Mac selon [ce guide](llm/README.md).
Copier `.env.example` vers `.env` si nécessaire, puis configurer son fournisseur
et la topologie :

```dotenv
KUBEAI_SUPERVISION_CLUSTER=kubeai-supervision
KUBEAI_OBSERVED_CLUSTER=kubeai-observed
KUBERNETES_NAMESPACE=applications
```

Depuis la racine du dépôt sur Mac/Linux :

```bash
bash start.sh          # démarre les deux clusters et ouvre le tunnel WebUI
bash start-failure.sh  # lance la démo CPU/HTTP du 19 octobre dans applications
bash stop-failure.sh   # supprime les pods de démo et remet le cluster observé au propre
```

Sous Windows PowerShell, utiliser `./start.ps1`, `./start-failure.ps1` et
`./stop-failure.ps1`. `scripts/start.ps1` reste un alias du démarrage commun.
Apertus reste lancé séparément ; aucun modèle n'est téléchargé par `start`.

Ouvrir <http://127.0.0.1:8080>, créer son compte si nécessaire et sélectionner
le modèle **kubeai**. Après les quatre minutes du scénario, demander :

> Analyse le dernier incident.

Puis, dans le même chat :

> Pour ce même incident, corrèle les logs, le CPU et les erreurs HTTP.

Les formulations libres et l'anglais sont acceptés, par exemple
« Could you investigate what went wrong most recently? ».
Voir [le fonctionnement de l'agent et ses outils](docs/agent-tools.md).

Le cluster observé contient les namespaces `applications` et `collecte` ; le
cluster de supervision contient `kubeai` et `supervision`. Les logs et métriques
portent le label du cluster observé et l'agent filtre ses lectures sur ce label.
Le [graphique et les détails de la topologie](docs/two-clusters.md) expliquent
la hiérarchie cluster → namespace → pod → conteneur et les connexions.

Le premier lancement arrête l'ancien profil `minikube` s'il héberge l'ancienne
pile kubeAI, pour libérer ses ressources. Il ne touche pas aux autres projets.
Les nouveaux profils réservent 4 Gio et 2 Gio ; l'inférence Apertus s'ajoute sur
le Mac. Le jeton de lecture expire après 24 heures et est renouvelé à chaque
`start`. Les paramètres `.env` sont appliqués au démarrage ; les commandes de
démo et de nettoyage utilisent la dernière topologie démarrée.

## Architecture

Deux flux se rejoignent dans le backend :

```text
Kubernetes ──> Grafana Alloy ── métriques (KSM, cAdvisor, pods annotés) ──> Mimir (PromQL) ──┐
                                  logs ─────────────────────────────────────> Loki (LogQL) ────┼──> FastAPI
Kubernetes API (lecture seule : nœuds, pods, événements) ─────────────────────────────────────┘       │
                                                                                                      ├──> Mistral API
Open WebUI ── modèle virtuel kubeai ─────────────────────────────────────────────────────────────────┘     ou Apertus local
```

| Bloc | Responsabilité |
|---|---|
| Kubernetes | Exécute la pile et les scénarios de démonstration. |
| Alloy | Collecte les métriques de kube-state-metrics, de cAdvisor (via le proxy de l'API Kubernetes) et des pods annotés du namespace observé (`applications` par défaut), puis les logs de ce namespace. |
| Mimir | Stocke les métriques et répond aux requêtes PromQL historiques. |
| Loki | Stocke les logs consultés par LogQL. |
| FastAPI | Exécute et valide les appels d'outils en lecture seule et expose l'API compatible OpenAI. |
| LangChain Core | Décrit les outils et leurs arguments typés ; le modèle choisit les appels. |
| Mistral API / Apertus local | Comprend la demande, choisit les fonctions nécessaires et synthétise leurs preuves. Le fournisseur doit prendre en charge les appels de fonctions ou le protocole JSON typé. |
| Open WebUI | Interface conversationnelle authentifiée qui appelle FastAPI avec le modèle `kubeai`. |

Les manifests actifs se trouvent dans [`k8s/`](k8s/). Les configurations
spécifiques sont décrites dans [`docs/architecture.md`](docs/architecture.md),
[`docs/architecture-llm-profiles.md`](docs/architecture-llm-profiles.md),
[`docs/langchain.md`](docs/langchain.md),
[`docs/mistral-api.md`](docs/mistral-api.md) et
[`k8s/monitoring/README.md`](k8s/monitoring/README.md).
Le document d'architecture initial fourni avec le sujet est archivé dans
[`docs/archive/Group-17-Architecture-initiale.pdf`](docs/archive/Group-17-Architecture-initiale.pdf).
Il décrit la conception de départ, pas l’état livré ; l’architecture actuelle
est dans [`docs/architecture.md`](docs/architecture.md).

## État du projet et démonstrations

La [validation du 27 octobre 2026](docs/validation-two-clusters-2026-10-27.md)
confirme le démarrage des deux clusters sur Mac, la collecte distante, les deux
questions B/C via Apertus et le nettoyage de la démo. Les 417 tests de l'agent
et les 31 tests des scripts passent. Les résultats suivants décrivent la
validation historique sur Docker Desktop.

L'équipe a validé les démonstrations A/B/C depuis Open WebUI sur le cluster
Docker Desktop : santé globale, chronologie d'incident avec 52 à 80 observations
historiques Mimir/Loki selon la fenêtre, puis corrélation entre les marqueurs de
scénario et les métriques CPU/HTTP du pod applicatif. Consultez
[`docs/demo-scenarios.md`](docs/demo-scenarios.md) pour les questions, preuves,
limites et étapes de reproduction. Les 360 tests automatisés et les 6 tests
HTTP loopback passent ; les tests emploient des sources simulées et ne remplacent
pas une validation du cluster. La page
[`docs/metrics.md`](docs/metrics.md) détaille ce qui est réellement collecté ;
la validation du 22 octobre est dans
[`docs/validation-demo-http-2026-10-22.md`](docs/validation-demo-http-2026-10-22.md).

## Sécurité et limites

- FastAPI lit les nœuds, pods et événements avec des permissions Kubernetes
  limitées aux verbes de lecture nécessaires (`list`). Il n'accède pas aux
  Secrets via l'API et ne modifie aucune ressource.
- Alloy scrape cAdvisor via le proxy de l'API Kubernetes. Cela nécessite
  `list/watch` sur les nœuds et `get` sur `nodes/proxy` pour son ServiceAccount :
  ce droit élargit la surface RBAC par rapport à la version précédente ; le
  scrape n'utilise toutefois que `/metrics/cadvisor`. Les métriques
  CPU/mémoire des conteneurs sont donc collectées ; `/cluster/status` peut
  renseigner `cpu_cores` et `memory_working_set_bytes`. Voir
  [`docs/metrics.md`](docs/metrics.md) et
  [`k8s/monitoring/rbac-cadvisor.yaml`](k8s/monitoring/rbac-cadvisor.yaml).
- Les logs sont lus dans Loki ; les métriques proviennent de Mimir. Les séries
  applicatives sont collectées par opt-in via annotations Prometheus dans
  `kubeai`. Sans instrumentation, la latence HTTP et les erreurs applicatives
  restent inconnues. Les traces distribuées et Tempo sont hors périmètre.
- `process_cpu_seconds_total` décrit le temps CPU consommé par le processus.
  Ce n'est ni le CPU total du conteneur ni un pourcentage de sa limite.
- A/B/C distinguent les faits horodatés, les hypothèses et les données absentes.
  Une proximité temporelle ne prouve pas une cause. L'application ne réalise
  aucune remédiation automatique.
- Mistral est un service externe : le filtrage des données réduit certains
  risques, mais ne constitue pas une anonymisation. N'envoyez que des données
  autorisées par le groupe. Les tests n'effectuent aucun appel Mistral.
- Le poste de développement dispose de 8 Go de RAM. Les mesures réellement
  observées et les plafonds déclarés ne sont pas équivalents ; ne démarrez pas
  de composants supplémentaires si le cluster ou Windows est sous pression.
- Les événements Kubernetes ont une rétention limitée et les séries Mimir
  dépendent de leur durée de rétention. Une absence de données n'est jamais
  interprétée comme une valeur nulle ou un état sain.
- Le déploiement cloud, les alertes et Grafana ne sont pas encore implémentés.
  Les traces distribuées et Tempo sont hors périmètre. Voir
  [`CONFORMITE-EXIGENCES.md`](CONFORMITE-EXIGENCES.md).

## Vérifier l'installation

Le lanceur `start` rend les manifests communs, les répartit entre les deux
clusters, construit l'image d'agent, crée les Secrets, vérifie les services et
ouvre le tunnel vers WebUI. Ne pas appliquer directement `kubectl apply -k k8s`
pour cette topologie : cela installerait tous les composants dans un seul cluster.

```bash
kubectl --context kubeai-observed get pods -n applications
kubectl --context kubeai-observed get pods -n collecte
kubectl --context kubeai-supervision get pods -n kubeai
kubectl --context kubeai-supervision get pods -n supervision
python3 scripts/verify-demo-metrics.py
```

La dernière commande s'utilise après la démo. Elle lit le pod dans le cluster
observé et interroge Mimir/Loki dans le cluster de supervision. Pour des noms
personnalisés, elle utilise la dernière topologie démarrée.

## Secrets et authentification

Ne pas versionner `.env`, les clés API ou les kubeconfigs privés. `start` crée
une clé Bearer commune à FastAPI et WebUI dans `kubeai-api-auth`, puis la conserve
aux lancements suivants. La clé du fournisseur est transférée séparément dans
`kubeai-llm` ou `kubeai-mistral`.

Le compte `kubeai-agent` du cluster observé peut uniquement lister les nœuds,
pods et événements. Son jeton de 24 heures est renouvelé au démarrage et monté
dans l'agent du cluster de supervision avec la CA du cluster observé. Aucun
kubeconfig administrateur n'est copié. Voir [les détails réseau et RBAC](docs/two-clusters.md).

FastAPI tourne dans le cluster de supervision. Open WebUI lui parle via
`http://kubeai-agent.kubeai.svc.cluster.local:8000/v1`. Le tunnel WebUI est limité
à `127.0.0.1:8080` ; son journal est `.cache/two-clusters/webui-forward.log`.

## Tests du backend

Les tests automatisés utilisent des transports simulés ; aucun cluster,
Secret ni appel Mistral réel n'est nécessaire. Les démonstrations A/B/C ont,
elles, été exercées sur le cluster Docker Desktop avec le vrai fournisseur.

```powershell
Set-Location .\agent
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD = '1'
..\.venv\Scripts\python.exe -B -m pytest tests --ignore=tests/integration -q -p no:cacheprovider
..\.venv\Scripts\python.exe -m compileall -q app tests
```

Depuis la racine, `pytest` fonctionne aussi grâce au fichier [pytest.ini](pytest.ini)
(`pythonpath = agent`) : `pytest agent/tests --ignore=agent/tests/integration`.
La commande depuis `agent/` reste la référence.

Les tests ignorés ou exclus ne doivent pas être déclarés réussis sans exécution.
Pour les vérifications réelles des démonstrations, suivre le protocole de
[`docs/demo-scenarios.md`](docs/demo-scenarios.md) dans un cluster autorisé.

## Équipe et livrables

Le dépôt `kubeAI` est un travail collectif des **3 étudiants du groupe P5-G17**.
La conception initiale est archivée dans
[`docs/archive/Group-17-Architecture-initiale.pdf`](docs/archive/Group-17-Architecture-initiale.pdf) ;
l’architecture livrée et validée est décrite dans
[`docs/architecture.md`](docs/architecture.md). Le suivi des exigences du sujet
et des points restants est dans
[`CONFORMITE-EXIGENCES.md`](CONFORMITE-EXIGENCES.md).
