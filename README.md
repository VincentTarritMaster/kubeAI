# P5-G17 | KubeAI

Projet collectif des quatre étudiants du groupe P5-G17 : un assistant d'observabilité
Kubernetes qui répond à des questions de santé et d'incident à partir de
preuves datées. Le modèle technique exposé à Open WebUI est `kubeai`.

## Pour les collègues qui ont déjà cloné le projet

Depuis la racine du dépôt, mettre sa branche à jour avec `main` (committer ou
mettre de côté ses changements locaux avant le merge) :

```powershell
git fetch origin
git switch nom-de-votre-branche
git merge origin/main
```

Si vous travaillez directement sur `main`, utilisez plutôt `git switch main`
puis `git pull --ff-only origin main`. Ensuite, dans le même terminal :

```powershell
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
notepad .env
.\scripts\stop.ps1
.\scripts\start.ps1
```

Dans `.env`, garder `LLM_PROVIDER=mistral-api` et remplacer
`MISTRAL_API_KEY=REMPLACER_PAR_SA_CLE` par **sa propre clé**. Ne pas committer
`.env`. Quand le démarrage affiche « KubeAI est prêt », ouvrir
<http://127.0.0.1:8080>, se connecter à Open WebUI et choisir le modèle `kubeai`.
Au premier accès, créer son compte Open WebUI.

## Installation et démarrage rapide — Windows / Docker Desktop

1. **Mettre à jour sa branche sans perdre son travail.** Vérifier `git status`.
   Si des fichiers sont modifiés, les committer sur sa branche ou les mettre
   temporairement à l'abri avec `git stash push -u`. Puis :

   ```powershell
   git switch nom-de-sa-branche
   git fetch origin
   git merge origin/main
   # En cas de stash : git stash pop, puis résoudre les conflits éventuels.
   ```

   Remplacer `origin/main` par la branche commune convenue par le groupe si
   celle-ci diffère. Ne jamais réécrire l'historique d'un autre membre.
   En cas de conflit, éditer les fichiers marqués, faire `git add` puis
   `git commit`. Ne pas utiliser `git reset --hard`.

2. **Prérequis.** Windows PowerShell 5.1 ou PowerShell 7, Docker Desktop avec
   Kubernetes activé (contexte `docker-desktop`), `kubectl`, Python 3.12+,
   ports 9009/3100/8000/8080 libres et au moins 0,5 Gio de RAM libre au
   lancement de WebUI. Voir [le guide détaillé](docs/INSTALLATION.md).

3. **Configurer son environnement personnel.** Si `.env` n'existe pas, exécuter
   `Copy-Item .env.example .env`, puis éditer `.env`. Pour Mistral, garder
   `LLM_PROVIDER=mistral-api` et renseigner sa propre `MISTRAL_API_KEY`.
   Pour un moteur local compatible OpenAI, choisir `LLM_PROVIDER=local`,
   `LLM_BASE_URL` et `LLM_MODEL` selon le moteur et le modèle déjà installés.
   Aucun modèle local n'est lancé automatiquement.
   L'affichage utilise `Europe/Zurich` par défaut ; l'option
   `KUBEAI_DISPLAY_TIMEZONE` dans `.env` accepte un autre fuseau IANA.

4. **Démarrer avec une seule commande**, aussi au premier lancement :

   ```powershell
   .\scripts\start.ps1
   ```

   Les dépendances et ressources manquantes sont initialisées, puis le navigateur
   s'ouvre sur <http://127.0.0.1:8080> après les contrôles de disponibilité.
   Sur une installation neuve, créer le premier compte administrateur WebUI.

5. **Arrêter la session** :

   ```powershell
   .\scripts\stop.ps1
   ```

Le script démarre Docker Desktop s'il est arrêté et attend Kubernetes ; si le
contexte manque, activer Kubernetes dans ses paramètres. Si un port est occupé,
libérer le port indiqué. Sous 0,5 Gio de
RAM libre, WebUI est arrêté. Une clé Mistral absente, un modèle local non
joignable, un Secret/jeton impossible à créer ou un pod en erreur produisent un
message explicite ; consulter les journaux dans `.cache/windows-start/` et le
[guide d'installation](docs/INSTALLATION.md). Si WebUI ne voit pas FastAPI,
vérifier sa connexion persistée et la passerelle `host.docker.internal`.

## Architecture

Deux flux se rejoignent dans le backend :

```text
Kubernetes ──> Grafana Alloy ── métriques ──> Mimir (PromQL) ──┐
                                  logs ──────> Loki (LogQL) ────┼──> FastAPI
Kubernetes API (lecture seule) ────────────────────────────────┘       │
                                                                       ├──> Mistral API
Open WebUI ── modèle virtuel kubeai ──────────────────────────────────┘     ou Apertus local
```

| Bloc | Responsabilité |
|---|---|
| Kubernetes | Exécute la pile et les scénarios de démonstration. |
| Alloy | Collecte les métriques de kube-state-metrics et des pods annotés du namespace `kubeai`, puis les logs de ce namespace. |
| Mimir | Stocke les métriques et répond aux requêtes PromQL historiques. |
| Loki | Stocke les logs consultés par LogQL. |
| FastAPI | Orchestre l'inventaire en lecture seule, les requêtes Mimir/Loki, les diagnostics A/B/C et l'API compatible OpenAI. |
| Mistral API / Apertus local | Fournit une analyse facultative et contrainte par les preuves calculées. Le profil est choisi par configuration. |
| Open WebUI | Interface conversationnelle authentifiée qui appelle FastAPI avec le modèle `kubeai`. |

Les manifests actifs se trouvent dans [`k8s/`](k8s/). Les configurations
spécifiques sont décrites dans [`docs/architecture.md`](docs/architecture.md),
[`docs/architecture-llm-profiles.md`](docs/architecture-llm-profiles.md),
[`docs/mistral-api.md`](docs/mistral-api.md) et
[`k8s/monitoring/README.md`](k8s/monitoring/README.md).
Le document d'architecture fourni avec le sujet,
[`docs/Group-17-Architecture.pdf`](docs/Group-17-Architecture.pdf), est conservé
comme référence initiale.

## État du projet et démonstrations

L'équipe a validé les démonstrations A/B/C depuis Open WebUI sur le cluster
Docker Desktop : santé globale, chronologie d'incident avec 52 observations
historiques Mimir/Loki, puis corrélation entre les marqueurs de scénario et la
métrique CPU du processus applicatif. Consultez
[`docs/demo-scenarios.md`](docs/demo-scenarios.md) pour les questions, preuves,
limites et étapes de reproduction. Les tests automatisés emploient des sources
simulées et ne remplacent pas une validation du cluster.

## Sécurité et limites

- FastAPI lit les nœuds, pods et événements avec des permissions Kubernetes
  limitées aux verbes de lecture nécessaires (`list`). Il n'accède pas aux
  Secrets via l'API et ne modifie aucune ressource.
- Alloy conserve des droits restreints. Aucun accès `nodes/proxy` ni scraping
  cAdvisor n'est requis par la configuration actuelle.
- Les logs sont lus dans Loki ; les métriques proviennent de Mimir. Les séries
  applicatives sont collectées par opt-in via annotations Prometheus dans
  `kubeai`. Sans instrumentation, la latence HTTP et les erreurs applicatives
  restent inconnues.
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

## Préparation de l'environnement

Prérequis : Windows PowerShell, Python 3.12, `kubectl`, Kustomize (via
`kubectl kustomize`), un cluster Kubernetes déjà opérationnel et les dépendances
Python du dossier `agent/`. Aucun modèle local n'est téléchargé par ces étapes.

Depuis la racine du dépôt, contrôler le contexte explicitement choisi et rendre
les manifests sans les appliquer :

```powershell
$Context = 'docker-desktop'
kubectl --context $Context config current-context
kubectl --context $Context get nodes
kubectl kustomize .\k8s\
kubectl kustomize .\k8s\monitoring\
```

Le rendu Kustomize vérifie l'assemblage des ressources ; il ne démarre pas le
cluster et ne valide pas la disponibilité des images, le stockage ou la syntaxe
interne de chaque configuration de service. Pour un premier déploiement sur une
machine adaptée, consulter les instructions complètes du [monitoring](k8s/monitoring/README.md)
et de l'[agent FastAPI](agent/README.md). Ne pas utiliser `--prune` et ne
supprimer aucun PVC pendant une mise à jour.

Une fois le cluster choisi, ses prérequis satisfaits et l'image d'agent
disponible dans son registre/runtime, le déploiement commun s'applique depuis
la racine :

```powershell
$Context = 'docker-desktop'
kubectl --context $Context apply -k .\k8s\
kubectl --context $Context -n monitoring rollout status deployment/mimir --timeout=5m
kubectl --context $Context -n monitoring rollout status deployment/loki --timeout=5m
kubectl --context $Context -n monitoring rollout status deployment/alloy --timeout=5m
kubectl --context $Context -n monitoring rollout status deployment/kube-state-metrics --timeout=5m
kubectl --context $Context -n kubeai rollout status deployment/kubeai-agent --timeout=5m
kubectl --context $Context -n kubeai rollout status deployment/open-webui --timeout=5m
```

`kubectl apply -k` suppose que les Secrets référencés existent et que les
images sont accessibles. Sur Minikube, la construction locale de l'image est
documentée dans [`agent/README.md`](agent/README.md). N'exécuter ces commandes
que sur un cluster autorisé et avec les ressources nécessaires.

## Secrets et authentification

Ne versionnez jamais `.env`, une clé Mistral, un token kubeconfig ou un Secret.
Le fichier `.env.example` ne contient que des valeurs factices. En local,
FastAPI ne charge le `.env` racine que lorsque `KUBEAI_ENV=development` est
défini avant son démarrage ; les variables déjà présentes gardent la priorité.
Kubernetes n'utilise pas ce chargement local.

Pour un FastAPI déployé dans Kubernetes, l'API Bearer est fournie par le Secret
`kubeai-api-auth` (clé `api-key`) à FastAPI et Open WebUI. Les deux valeurs
doivent être créées à partir du même fichier privé, hors du dépôt. La clé
Mistral est indépendante et peut être montée depuis le Secret `kubeai-mistral` :

```powershell
$Context = 'docker-desktop'
$ApiKeyFile = 'C:\chemin-prive\kubeai-api-key.txt'
$MistralKeyFile = 'C:\chemin-prive\mistral-api-key.txt'
kubectl --context $Context -n kubeai create secret generic kubeai-api-auth --from-file="api-key=$ApiKeyFile"
kubectl --context $Context -n kubeai create secret generic kubeai-mistral --from-file="api-key=$MistralKeyFile"
```

Exécuter ces commandes uniquement si les Secrets ne sont pas déjà présents ;
elles ne remplacent pas un Secret existant. Garder les fichiers hors du dépôt,
restreindre leurs ACL Windows et ne pas afficher leur contenu. Les instructions
sur les droits et la configuration LLM sont dans [`agent/README.md`](agent/README.md)
et [`docs/mistral-api.md`](docs/mistral-api.md).

## Démarrage Windows — procédure manuelle de diagnostic

La procédure quotidienne recommandée est `scripts/start.ps1` ci-dessus. Les
commandes suivantes restent utiles pour diagnostiquer un composant isolé.

Pour exécuter FastAPI directement sur Windows, utiliser un kubeconfig privé
contenant le contexte `kubeai-readonly`. Ce compte doit seulement lire les
ressources requises par l'agent. Vérifier les permissions sans imprimer les
credentials :

```powershell
$KubeConfig = "$env:USERPROFILE\.kube\kubeai-readonly"
kubectl --kubeconfig $KubeConfig --context kubeai-readonly auth can-i list nodes
kubectl --kubeconfig $KubeConfig --context kubeai-readonly auth can-i list pods --all-namespaces
kubectl --kubeconfig $KubeConfig --context kubeai-readonly auth can-i list events --all-namespaces
```

Dans deux terminaux PowerShell séparés, ouvrir des tunnels locaux vers Mimir et
Loki. Utiliser un contexte qui dispose des droits de port-forward ; il peut être
différent du contexte limité utilisé par FastAPI :

```powershell
$TunnelContext = 'docker-desktop'
kubectl --context $TunnelContext -n monitoring port-forward --address 127.0.0.1 service/mimir 9009:9009
```

```powershell
$TunnelContext = 'docker-desktop'
kubectl --context $TunnelContext -n monitoring port-forward --address 127.0.0.1 service/loki 3100:3100
```

Dans un troisième terminal, depuis la racine du dépôt, démarrer le backend :

```powershell
$env:KUBEAI_ENV = 'development'
$env:KUBERNETES_ACCESS_MODE = 'kubeconfig'
$env:KUBERNETES_CONTEXT = 'kubeai-readonly'
$env:KUBERNETES_KUBECONFIG_FILE = "$env:USERPROFILE\.kube\kubeai-readonly"
$ApiKeyFile = 'C:\chemin-prive\kubeai-api-key.txt'
$env:KUBEAI_API_KEY = (Get-Content -Raw $ApiKeyFile).Trim()
$env:MIMIR_URL = 'http://127.0.0.1:9009/prometheus'
$env:LOKI_URL = 'http://127.0.0.1:3100'
Set-Location .\agent
..\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

La clé API Mistral privée est lue depuis `.env` en mode développement. La clé
Bearer de l'API FastAPI (`KUBEAI_API_KEY`) doit être configurée dans
l'environnement privé du backend et dans Open WebUI. Vérifier `/health`,
`/ready`, `/cluster/inventory` puis `/cluster/status` avant de lancer une
question. Ces contrôles n'appellent pas Mistral.

Pour l'architecture complète, Open WebUI et FastAPI communiquent par le Service
interne `http://kubeai-agent.kubeai.svc.cluster.local:8000/v1`. Si Open WebUI
tourne dans Kubernetes et FastAPI sur Windows, `127.0.0.1` dans le pod désigne
le pod lui-même : utiliser uniquement le chemin de passerelle Docker Desktop
restreint déjà validé pour l'environnement, puis vérifier la connectivité depuis
Open WebUI. Ne liez pas FastAPI à une interface publique, ne désactivez pas le
pare-feu et n'ouvrez pas un port au réseau général. Si ce chemin local n'est pas
disponible, déployer FastAPI dans le cluster et conserver l'URL Service interne.

Open WebUI est authentifié, utilise l'ID de modèle `kubeai` et affiche le nom
`P5-G17 | KubeAI`. Pour l'ouvrir via un tunnel limité à Windows localhost :

```powershell
$TunnelContext = 'docker-desktop'
kubectl --context $TunnelContext -n kubeai port-forward --address 127.0.0.1 service/open-webui 8080:80
```

Ouvrir <http://127.0.0.1:8080>. La configuration persistée dans Open WebUI peut
prévaloir sur les valeurs d'environnement du manifeste ; vérifier le nom et la
connexion API dans l'interface d'administration.

## Tests du backend

Les tests unitaires et HTTP utilisent des transports simulés ; aucun cluster,
Secret ni appel Mistral réel n'est nécessaire :

```powershell
Set-Location .\agent
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD = '1'
..\.venv\Scripts\python.exe -B -m pytest tests --ignore=tests/integration -q -p no:cacheprovider
..\.venv\Scripts\python.exe -m compileall -q app tests
```

Les tests ignorés ou exclus ne doivent pas être déclarés réussis sans exécution.
Pour les vérifications réelles des démonstrations, suivre le protocole de
[`docs/demo-scenarios.md`](docs/demo-scenarios.md) dans un cluster autorisé.

## Équipe et livrables

Le dépôt `kubeAI` est un travail collectif des **quatre étudiants du groupe P5-G17**.
La description de la conception de départ est conservée dans le PDF fourni ;
la documentation de livraison décrit l'architecture et les comportements
effectivement présents dans le dépôt.
