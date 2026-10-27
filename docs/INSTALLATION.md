# Installation Windows / Docker Desktop

Le démarrage actuel est `./start.ps1` : il prépare deux profils Minikube, avec
FastAPI dans le cluster de supervision. Voir le [guide des deux clusters](two-clusters.md).
Le contenu ci-dessous décrit l'ancien mode FastAPI sur Windows.

Ce guide décrit le mode **FastAPI sur Windows, Open WebUI dans Kubernetes**.
Les manifests existants conservent le mode entièrement déployé dans le cluster.
Lancez les commandes depuis la racine du dépôt dans PowerShell 5.1 ou 7.

## Prérequis

- Windows et Docker Desktop installé, avec Kubernetes activé et le contexte
  `docker-desktop` disponible. Le script lance Docker Desktop s'il est arrêté
  et attend que le nœud soit `Ready`.
- `kubectl` et Python **3.12 ou plus récent** dans le `PATH`.
- Ressources suffisantes pour Mimir (jusqu'à 2 Gio), Loki (768 Mio), Alloy,
  kube-state-metrics et Open WebUI (768 Mio). Le script bloque WebUI sous
  **0,5 Gio de RAM libre** avant et après son déploiement.
- Ports locaux 9009, 3100, 8000 et 8080 libres.
- cAdvisor est scrapé par Alloy via le proxy de l’API Kubernetes ; le
  ServiceAccount `alloy` reçoit `list/watch` sur les nœuds et `get` sur
  `nodes/proxy` (voir `k8s/monitoring/rbac-cadvisor.yaml`).
- Pour Mistral : clé API personnelle. Pour le profil local : serveur déjà
  installé et lancé avec une API compatible OpenAI et un modèle listé par `/v1/models`.
  Aucun modèle n'est téléchargé ou lancé automatiquement.

## Configuration personnelle

```powershell
Copy-Item .env.example .env
notepad .env
```

Pour Mistral, choisir `LLM_PROVIDER=mistral-api` et renseigner
`MISTRAL_API_KEY` dans `.env`, ou utiliser un fichier privé via
`MISTRAL_API_KEY_FILE`. Pour Ollama ou un autre moteur compatible OpenAI,
choisir `LLM_PROVIDER=local`, `LLM_BASE_URL=http://127.0.0.1:11434/v1`
(adapter l'URL) et le nom exact du modèle installé dans `LLM_MODEL`.
Ollama [expose ces routes compatibles OpenAI](https://github.com/ollama/ollama/blob/main/docs/api/openai-compatibility.mdx),
mais ce projet n'a pas été testé avec un moteur Ollama réel.
Le fuseau d'affichage est `Europe/Zurich` par défaut. Pour un autre fuseau IANA,
définir `KUBEAI_DISPLAY_TIMEZONE` dans `.env` ; les données collectées et les
preuves restent horodatées en UTC.
Le moteur local reste à préparer manuellement ; le profil Apertus documenté
dans [`../llm/README.md`](../llm/README.md) cible macOS et n'est pas lancé sur
Windows. Sur un poste de 8 Go, éviter un modèle gourmand en mémoire.

Le fichier `.env` est ignoré par Git. Le backend lit ce fichier en mode
`KUBEAI_ENV=development`. Le script fixe lui-même l'URL de Mimir et de Loki,
le chemin du kubeconfig de lecture seule et la clé Bearer interne.

## Première installation et usage quotidien

```powershell
.\scripts\start.ps1
```

Si PowerShell refuse le script non signé, lancer uniquement ce processus avec :

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\start.ps1
```

Ne pas désactiver la politique pour toute la machine.

Pour contrôler la collecte après le démarrage :

```powershell
python scripts\verify-observability.py --context docker-desktop --require-container-metrics
Invoke-RestMethod http://127.0.0.1:8000/capabilities
```

`cadvisor` doit être `available` et le rapport doit afficher
`container_metrics: available` avec un CPU/mémoire non nuls.

Pour le pod de démonstration, lancer `scripts\run-demo-incident.ps1`, puis
`python scripts\verify-demo-metrics.py --context docker-desktop`. Le détail des
signaux est dans [`metrics.md`](metrics.md).

Au premier lancement, `setup.ps1` crée `.venv`, installe
`agent/requirements.txt`, applique les manifests de monitoring (dont
`rbac-cadvisor.yaml`), le RBAC de l'agent, attend les déploiements et crée
`kubeai-api-auth` seulement s'il manque.
Le script ne réinstalle les dépendances que si le fichier de dépendances change.
Il ne lance pas le Deployment `kubeai-agent` : l'agent utilisé ici est FastAPI
sur Windows. Le script démarre les tunnels Mimir/Loki, renouvelle le jeton du
ServiceAccount `kubeai-agent`, démarre FastAPI, contrôle le fournisseur IA,
configure WebUI vers `http://host.docker.internal:8000/v1`, vérifie cette route
depuis le pod, ouvre un tunnel local et le navigateur.

Le kubeconfig privé est `~/.kube/kubeai-readonly.yaml`. Son contexte
`kubeai-readonly` utilise uniquement le ServiceAccount défini dans
`k8s/agent-rbac.yaml`, avec les verbes de lecture déclarés dans ce manifeste.
Le script renouvelle son jeton à chaque démarrage et limite les ACL du fichier
à l'utilisateur Windows courant. Aucun jeton n'est imprimé.

L'interface s'ouvre sur <http://127.0.0.1:8080>. La configuration WebUI
persistée dans son PVC peut prévaloir sur `OPENAI_API_BASE_URL` : si le modèle
`kubeai` ne répond pas, vérifier l'URL de la connexion dans l'administration
Open WebUI. Le script vérifie depuis le pod l'accès authentifié à `/v1/models`.
Sur un PVC neuf, le premier utilisateur crée le compte administrateur dans le
navigateur. [Open WebUI désactive ensuite les nouvelles inscriptions](https://docs.openwebui.com/features/authentication-access/rbac/roles/). Les comptes
et réglages déjà présents dans un PVC restent conservés.

Pour arrêter la session :

```powershell
.\scripts\stop.ps1
```

Cette commande arrête seulement les processus créés et suivis par `start.ps1`,
puis met Open WebUI à zéro réplica. Elle ne supprime aucun volume, Secret ni
composant de monitoring. Les journaux locaux se trouvent dans
`.cache/windows-start/*.log`, ignoré par Git.

## Dépannage

| Symptôme | Action |
|---|---|
| Docker ne répond pas ou contexte absent | `start.ps1` lance Docker Desktop s'il est installé et attend cinq minutes. Si le contexte manque encore, activer Kubernetes dans ses paramètres puis relancer. |
| RAM libre sous 0,5 Gio | Fermer d'autres applications ou augmenter la mémoire disponible avant de relancer. |
| Port occupé | Identifier le processus propriétaire avec `Get-NetTCPConnection -LocalPort 8000 -State Listen` (adapter le port), puis libérer le port sans tuer une autre application par erreur. |
| Secret ou jeton impossible à créer | Vérifier les droits du contexte `docker-desktop` et la présence du ServiceAccount `kubeai-agent` ; relancer `start.ps1`. |
| Mistral indisponible | Vérifier la clé personnelle, le modèle et l'accès réseau. `/llm/status` utilise `/models`, sans génération. |
| Modèle local indisponible | Démarrer le moteur séparément ; vérifier l'URL `/v1/models` et le nom exact du modèle. |
| Pod en erreur | `kubectl --context docker-desktop -n monitoring get pods` ou `-n kubeai get pods`, puis `kubectl describe pod` et `kubectl logs`. |
| cAdvisor indisponible | Vérifier `kubectl -n monitoring logs deployment/alloy --tail=80`, le ClusterRole `kubeai-alloy-cadvisor` et `kubectl auth can-i get nodes/proxy --as=system:serviceaccount:monitoring:alloy`. |
| WebUI ne joint pas FastAPI | Vérifier `http://127.0.0.1:8000/ready`, la passerelle `host.docker.internal`, le pare-feu Windows et la connexion WebUI persistée. |

Les tests automatisés restent simulés ; les scénarios réels et leurs limites
sont décrits dans [`demo-scenarios.md`](demo-scenarios.md).
