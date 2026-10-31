# P5-G17 | KubeAI

KubeAI est un assistant d'observabilité Kubernetes développé par le groupe
**P5-G17**. Il répond en français ou en anglais à des questions de santé, de
chronologie et de corrélation à partir de preuves datées provenant de
Kubernetes, Mimir et Loki. Les faits sont calculés par du code déterministe ;
le modèle ne peut ni inventer une ressource, ni modifier le cluster.

- Démonstration : <https://www.youtube.com/watch?v=EO6ep91FaQc> via https <https://kubeai.anassndri.ch> (exoscale coupé pour facturation)
- Modèle exposé dans Open WebUI : `kubeai`
- Rapport d'audit final : [docs/exoscale/evidence/adversarial-test-report.md](docs/exoscale/evidence/adversarial-test-report.md)

## Architecture livrée

Deux clusters SKS Exoscale séparés, dans `ch-gva-2` :

```text
kubeai-observed                         kubeai-supervision
┌──────────────────────┐                ┌──────────────────────────────┐
│ applications         │                │ supervision                  │
│ collecte             │  mTLS privé    │ Alloy / Mimir / Loki         │
│ Alloy                │ ─────────────▶ │ observability-proxy          │
│ kubeai-agent (lecture)│                │ FastAPI kubeai-agent         │
└──────────────────────┘                │ Open WebUI                   │
                                        │ Caddy + webui-edge (HTTPS)   │
                                        └──────────────────────────────┘
```

| Composant | Rôle |
|---|---|
| **SKS `kubeai-observed`** | Charge applicative et collecte : namespaces `applications` et `collecte`. |
| **SKS `kubeai-supervision`** | Mimir, Loki, Alloy, agent FastAPI, Open WebUI, Caddy. |
| **Alloy** | Collecte les métriques et logs du cluster observé. |
| **Mimir / Loki** | Stockage des métriques PromQL et des logs LogQL. |
| **FastAPI `kubeai-agent`** | Outils Kubernetes/Mimir/Loki en lecture seule, règles de santé et rendu A/B/C. |
| **Open WebUI** | Interface authentifiée ; `ENABLE_SIGNUP=false`. |
| **Caddy + webui-edge** | HTTPS public, certificat Let's Encrypt, adaptation du bloc de preuves dépliable. |
| **Mistral API** | Compréhension du chat générique et, éventuellement, sélection d'une priorité déjà calculée ; jamais une source de fait. |

Les détails d'architecture, de RBAC et de réseau sont dans
[docs/two-clusters.md](docs/two-clusters.md),
[docs/architecture.md](docs/architecture.md) et
[docs/exoscale/deployment.md](docs/exoscale/deployment.md).

## Technologies

- Python, FastAPI, Pydantic, httpx ;
- LangChain Core pour le protocole d'outils ;
- Open WebUI, Caddy, nginx (adaptateur UI) ;
- Kubernetes SKS Exoscale, Kustomize, Grafana Alloy, Mimir, Loki ;
- Mistral API, avec mode local Apertus documenté dans [llm/README.md](llm/README.md) ;
- tests `pytest`, tests PowerShell/Kustomize et validations API publiques.

## Démarrage Exoscale

Prérequis : `exo` configuré, `kubectl`, PowerShell 5.1+, Docker pour reconstruire
l'image, et les kubeconfigs administrateur **hors dépôt**.

```powershell
# Lecture seule : état des clusters, nœuds, volumes et coût estimé
.\scripts\exoscale\status.ps1

# Simulation, aucune ressource facturable
.\scripts\exoscale\start.ps1 -DryRun
.\scripts\exoscale\start.ps1 -Apply      # crée/scalie les Nodepools à 1

# Arrêt après les démonstrations : Nodepools à 0, PVC conservés
.\scripts\exoscale\stop.ps1 -DryRun
.\scripts\exoscale\stop.ps1 -Apply
```

`start.ps1 -Apply` et `stop.ps1 -Apply` sont des opérations **facturables** :
elles demandent confirmation et ne suppriment jamais les PVC, volumes SBS,
Secrets ou clusters. `bootstrap.ps1` est réservé à la création initiale de
l'infrastructure ; ne pas le relancer sans vérifier `-DryRun`.

Le 3 novembre, vérifier l'adresse publique et le DNS **avant** la soutenance :

```powershell
.\scripts\exoscale\status.ps1
Resolve-DnsName kubeai.anassndri.ch -Server 1.1.1.1
curl.exe -I https://kubeai.anassndri.ch/
```

Un redémarrage de Nodepool peut recréer le nœud public et changer son adresse
IPv4. Si l'adresse change, mettre à jour l'enregistrement DNS `A` chez
Infomaniak ; ne pas modifier les autres enregistrements. Voir
[docs/exoscale/public-access.md](docs/exoscale/public-access.md).

### Accès WebUI

Ouvrir <https://kubeai.anassndri.ch>, se connecter avec le compte local du
groupe, puis sélectionner le modèle **kubeai**. L'inscription publique est
désactivée. Les identifiants et kubeconfigs restent hors Git.

## Utilisation et démonstrations

Les questions ci-dessous sont les questions de référence. Elles fonctionnent
sans ajouter « Answer in English » : la langue de la dernière question est
détectée automatiquement.

### A — santé globale

```text
Quel est l'état de santé de mon cluster ?
```

```text
What is the current health status of my Kubernetes cluster?
Check nodes, pods, restarts and recent alerts.
```

### B — chronologie d'incident

```text
Analyse l'incident du pod kubeai-demo-incident dans le namespace applications,
le 29 octobre 2026 entre 09:08 et 09:13 UTC. Reconstitue la chronologie à partir
des logs et métriques réellement disponibles.
```

```text
Analyze the incident of pod kubeai-demo-incident in namespace applications on
October 29, 2026 between 09:08 and 09:13 UTC. Reconstruct the chronology from
available logs and metrics.
```

### C — corrélation

```text
Pour l'incident du pod kubeai-demo-incident dans le namespace applications, le
29 octobre 2026 entre 09:08 et 09:13 UTC, corrèle les logs, le CPU, la latence
et les erreurs HTTP. Propose des hypothèses de cause, classe-les par
plausibilité et précise les preuves disponibles ainsi que ce qui reste à
vérifier.
```

```text
For the incident of pod kubeai-demo-incident in namespace applications on
October 29, 2026 between 09:08 and 09:13 UTC, correlate logs, CPU, latency and
HTTP errors. Propose hypotheses, rank them by plausibility, and state the
available evidence and what remains to verify.
```

Conseils de démonstration :

- ne pas redémarrer l'agent entre A, B et C : snapshots A `120 s`, B/C `300 s` ;
- limite API : 30 requêtes/minute, burst 10 ; attendre en cas de HTTP 429 ;
- A peut afficher `DEGRADED` à cause de deux pods Pending réels dans
  `kube-system` ; ne pas masquer ce fait ;
- les détails techniques sont fermés par défaut, puis ouverts par clic ;
- F5 conserve le diagnostic mais referme le bloc de preuves.

## Captures de démonstration

Les 12 captures sont dans [docs/capture](docs/capture/) :

| Fichier | Contenu |
|---|---|
| [00-public-https-login.png](docs/capture/00-public-https-login.png) | HTTPS public et authentification |
| [01-exoscale-clusters.png](docs/capture/01-exoscale-clusters.png) | Deux clusters SKS |
| [02-kubernetes-nodes-ready.png](docs/capture/02-kubernetes-nodes-ready.png) | Nœuds Ready |
| [03-supervision-pods-running.png](docs/capture/03-supervision-pods-running.png) | Pile de supervision |
| [04-kubernetes-pvc-bound.png](docs/capture/04-kubernetes-pvc-bound.png) | PVC liés |
| [05-demo-a-health.png](docs/capture/05-demo-a-health.png) | A — santé |
| [05-demo-a-health-with-detail.png](docs/capture/05-demo-a-health-with-detail.png) | A — preuves ouvertes |
| [06-demo-b-timeline.png](docs/capture/06-demo-b-timeline.png) | B — chronologie |
| [06-demo-b-timeline-with-detail.png](docs/capture/06-demo-b-timeline-with-detail.png) | B — preuves ouvertes |
| [07-demo-c-correlation.png](docs/capture/07-demo-c-correlation.png) | C — corrélation |
| [07-demo-c-correlation-with-detail.png](docs/capture/07-demo-c-correlation-with-detail.png) | C — preuves ouvertes |
| [08-demo-english.png](docs/capture/08-demo-english.png) | Réponse anglaise |

## Tests et validation

```text
441 tests agent passés
0 test agent échoué
```

Commandes :

```powershell
Set-Location .\agent
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD = '1'
..\.venv\Scripts\python.exe -B -m pytest tests --ignore=tests/integration -q -p no:cacheprovider
```

Validation des manifests :

```powershell
kubectl kustomize --load-restrictor=LoadRestrictionsNone k8s/overlays/exoscale/observed
kubectl kustomize --load-restrictor=LoadRestrictionsNone k8s/overlays/exoscale/supervision
```

Le rapport adversarial contient **62 PASS, 21 NOT_TESTED, 1 BLOCKED, 0 FAIL**
sur les 84 scénarios. Ce n'est pas une certification des 84 scénarios : les
tests non exécutés et T17 sont documentés honnêtement. La validation publique
couvre HTTPS, authentification, A/B/C français et anglais, refus des écritures,
streaming et composant dépliable.

## Sécurité

- L'agent Kubernetes utilise uniquement les verbes de lecture nécessaires
  (`list` sur nœuds, pods et événements) ;
- aucun outil d'écriture, de suppression, de scale ou de redémarrage n'est exposé ;
- les demandes de modification sont refusées en français et en anglais ;
- l'API et Open WebUI sont authentifiés ; l'inscription publique est fermée ;
- aucun Secret, kubeconfig ou identifiant réel n'est versionné ; les fichiers
  locaux restent sous `%USERPROFILE%\.kube\` et sont ignorés par Git ;
- l'alerte Gitleaks `generic-api-key` du commit `5ce37c9` est un faux positif :
  il s'agit de constantes de test `unit-secret-…` / `fake-…` sans valeur
  d'authentification. Aucune vraie clé n'est présente dans le dépôt.

## Limites et risques

- Les fonctions d'alerte dédiée ne sont pas configurées : les avertissements
  récents sont des **événements Kubernetes**, pas des notifications Alertmanager ;
- l'inventaire des pods peut être `partial` ; les champs absents ne sont jamais
  assimilés à un état sain ;
- Mimir conserve les métriques sans purge par âge configurée, Loki 30 jours ;
  préserver les PVC pour les captures ;
- 21 scénarios de robustesse n'ont pas été exécutés et T17 (arrêt complet réel)
  reste bloqué ; voir le rapport ;
- un seul nœud par cluster : pas de haute disponibilité ;
- l'IP publique et le DNS peuvent changer après recréation d'un nœud ;
- l'adaptateur de preuves dépliables dépend de la version Open WebUI 0.12.5
  (rendu CodeMirror) ; toute mise à jour doit être revalidée dans le navigateur ;
- les tests automatisés utilisent des transports simulés ; ils ne remplacent pas
  les démonstrations réelles.

## Documentation utile

- [Déploiement Exoscale](docs/exoscale/deployment.md)
- [Accès public, DNS et Security Groups](docs/exoscale/public-access.md)
- [Coûts et nettoyage](docs/exoscale/cost-and-cleanup.md)
- [Scénarios de démonstration](docs/demo-scenarios.md)
- [Architecture deux clusters](docs/two-clusters.md)
- [Rapport adversarial final](docs/exoscale/evidence/adversarial-test-report.md)
- [Health déterministe](docs/exoscale/evidence/deterministic-health.md)

## Équipe

Travail collectif des **3 étudiants du groupe P5-G17**. Le dépôt `kubeAI` est
la livraison finale ; les contributions de chaque étudiant restent identifiables
dans l'historique Git.
