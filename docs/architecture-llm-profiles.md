# kubeAI — architecture commune et profils LLM

Le projet collectif de Kevin, Vincent et Anass utilise une seule application
FastAPI, un seul Deployment `kubeai-agent` et le même contrat OpenAI-compatible
pour Open WebUI. Le profil se choisit avec `LLM_PROVIDER` :

| Profil | Valeur | Modèle de référence | URL de base | Authentification |
|---|---|---|---|---|
| Mistral API | `mistral-api` (défaut) | `mistral-small-2603` | `MISTRAL_BASE_URL`, HTTPS | `MISTRAL_API_KEY` ou Secret monté |
| Serveur local | `local` | `apertus-8b` | `LLM_BASE_URL`, HTTP(S) | facultative, selon le serveur |

Les anciens noms `mistral` et `apertus` sont normalisés respectivement vers
`mistral-api` et `local`. Le modèle virtuel présenté à Open WebUI reste `kubeai`
(l'identifiant historique `kubeai-apertus` reste accepté). Les réponses JSON,
SSE, les scénarios A/B/C et les clients Kubernetes, Mimir et Loki restent dans le
même backend. Le profil LLM ne modifie ni la collecte ni les droits Kubernetes.
L'indicateur `hosting=configured_endpoint` confirme seulement qu'un endpoint
local configurable est utilisé ; il ne prouve pas que son hôte est sous le
contrôle du groupe.

```text
Open WebUI ── kubeai ── FastAPI (un seul Deployment)
                           ├── Kubernetes en lecture seule
                           ├── Mimir (PromQL) et Loki (LogQL)
                           └── profil choisi à l'exécution
                                ├── Mistral API (sortie HTTPS)
                                └── serveur compatible OpenAI (local/réseau privé)
Alloy ── métriques ──> Mimir       Alloy ── logs ──> Loki
```

## Configuration sur Windows et macOS

Le backend charge le `.env` à la racine du dépôt uniquement si
`KUBEAI_ENV=development` est défini explicitement avant le démarrage. Les
variables déjà présentes dans l'environnement gardent la priorité. Kubernetes
n'active pas ce mode et continue d'utiliser ses variables et fichiers Secret.
Les fichiers exemples ne contiennent aucune clé. Ne pas enregistrer une vraie
clé dans Git ou dans une ConfigMap.

### Tests légers sur Windows (8 Go)

Depuis PowerShell, à la racine du dépôt :

```powershell
Set-Location .\agent
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD = '1'
..\.venv\Scripts\python.exe -B -m pytest tests --ignore=tests/integration -q -p no:cacheprovider
```

Ces tests utilisent des réponses HTTP simulées. Ils ne nécessitent ni Docker,
ni Kubernetes, ni une clé Mistral, ni un serveur ou téléchargement de LLM.
Pour préparer un environnement Python, utiliser Python 3.12 et les dépendances
déjà prévues dans `agent/requirements.txt`; aucune dépendance GPU n'est requise.

Un endpoint compatible OpenAI déjà disponible sur une machine autorisée peut
être sélectionné sans reconstruire l'image. Dans le terminal du backend :

```powershell
$env:LLM_PROVIDER = 'local'
$env:LLM_BASE_URL = 'http://127.0.0.1:11435/v1'
$env:LLM_MODEL = 'apertus-8b'
$env:LLM_TIMEOUT_SECONDS = '180'
Set-Location .\agent
..\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Cette adresse loopback ne convient que si le serveur LLM est joignable depuis le
même environnement. Dans un pod Kubernetes, `127.0.0.1` désigne le pod FastAPI ;
il faut configurer `LLM_BASE_URL` vers un endpoint réseau privé joignable. Pour
le serveur Apertus prévu par Vincent, le Mac héberge llama.cpp, et Minikube le
joint via `host.minikube.internal:11435`. Les scripts existants de préparation
et de lancement Apertus ciblent macOS Apple Silicon et téléchargent environ
5,06 Go de poids : ils ne doivent pas être lancés sur le PC Windows de 8 Go.

Pour démarrer FastAPI sous Windows en développement local, depuis la racine du
dépôt :

```powershell
$env:KUBEAI_ENV = 'development'
$env:LLM_PROVIDER = 'mistral-api'
Set-Location .\agent
..\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Dans un autre PowerShell, depuis `agent/`, vérifier le profil et la présence
d'une clé sans en afficher la valeur :

```powershell
$env:KUBEAI_ENV = 'development'
$env:LLM_PROVIDER = 'mistral-api'
..\.venv\Scripts\python.exe -c "from app.config import Settings; s=Settings.from_env(); print(f'profile={s.llm_provider} model={s.llm_model} key_configured={bool(s.llm_key())}')"
```

Cette commande ne contacte pas Mistral. `/llm/status`, en revanche, appelle
réellement `GET /v1/models` et ne doit être utilisé qu'après autorisation.

Pour un premier essai réel isolé, sans Kubernetes ni routes de diagnostic, le
client `LLMService` existant peut envoyer une seule question synthétique. Cette
commande effectue une génération potentiellement facturable ; l'exécuter
uniquement après autorisation :

```powershell
Set-Location (Resolve-Path .\agent)
$env:KUBEAI_ENV = 'development'
$env:LLM_PROVIDER = 'mistral-api'
$script = @'
import asyncio
import httpx
from app.config import Settings
from app.services.llm_service import LLMService

async def main():
    settings = Settings.from_env()
    async with httpx.AsyncClient(
        base_url=settings.llm_base_url.rstrip("/") + "/",
        timeout=settings.llm_timeout,
        trust_env=False,
    ) as client:
        result = await LLMService(client, settings).complete({
            "messages": [{"role": "user", "content": (
                "Scénario synthétique : un pod de test est marqué Failed. "
                "Quelles observations vérifier avant d'avancer une cause ?"
            )}],
            "temperature": 0.2,
            "max_tokens": 120,
        })
        print(result["choices"][0]["message"]["content"])

asyncio.run(main())
'@
$script | ..\.venv\Scripts\python.exe -
```

Après un essai isolé autorisé, le backend FastAPI peut aussi être vérifié via
Open WebUI avec les données d'observabilité disponibles. Une requête par cette
voie peut nécessiter Kubernetes, Mimir et Loki. `GET /llm/status` reste un appel
réel distinct à `GET /v1/models`.

```powershell
$headers = @{ Authorization = "Bearer $env:KUBEAI_API_KEY" }
$models = Invoke-RestMethod http://127.0.0.1:8000/v1/models -Headers $headers
$models.data | Select-Object id,name
Invoke-RestMethod http://127.0.0.1:8000/llm/status -Headers $headers
$body = @{ model='kubeai'; messages=@(@{role='user';content='Scénario synthétique : un pod de test est signalé Failed. Quelles observations confirmerais-tu avant de proposer une cause ?'}) } | ConvertTo-Json -Depth 5
Invoke-RestMethod http://127.0.0.1:8000/v1/chat/completions -Method Post -Headers $headers -ContentType 'application/json' -Body $body
```

Pour vérifier le streaming après cette première réponse, construire une requête
`stream = $true` puis utiliser `curl.exe -N` :

```powershell
$streamBody = @{ model='kubeai'; stream=$true; messages=@(@{role='user';content='Scénario synthétique : un pod de test est signalé Failed. Quelles observations confirmerais-tu avant de proposer une cause ?'}) } | ConvertTo-Json -Depth 5 -Compress
curl.exe -N http://127.0.0.1:8000/v1/chat/completions -H "Authorization: Bearer $env:KUBEAI_API_KEY" -H 'Content-Type: application/json' --data-binary $streamBody
```

Arrêter Uvicorn avec `Ctrl+C` dans le PowerShell A, puis effacer la variable
secrète dans cette même session :

```powershell
Remove-Item Env:MISTRAL_API_KEY -ErrorAction SilentlyContinue
Remove-Item Env:MISTRAL_API_KEY_FILE -ErrorAction SilentlyContinue
Remove-Item Env:LLM_PROVIDER -ErrorAction SilentlyContinue
```

### Mac Apple Silicon — profil Apertus existant

Sur un Mac adapté, les commandes actuelles de préparation du modèle sont
documentées dans [`../llm/README.md`](../llm/README.md). Elles sont manuelles,
téléchargent les poids puis démarrent llama.cpp. Après démarrage explicite du
serveur, configurer le backend avec `LLM_PROVIDER=local`,
`LLM_BASE_URL=http://host.minikube.internal:11435/v1`, `LLM_MODEL=apertus-8b`,
`LLM_API_KEY_FILE=/var/run/secrets/kubeai-llm/api-key` et un délai de 180 secondes.
Le secret local est conservé dans le Secret `kubeai-llm`; il n'est jamais stocké
dans la ConfigMap. D'autres serveurs locaux sans authentification n'ont pas
besoin de ce fichier.

Open WebUI continue d'appeler `http://kubeai-agent.kubeai.svc.cluster.local:8000/v1`
et sélectionne `kubeai` quel que soit le profil. Le changement Kubernetes se
fait dans la ConfigMap `kubeai-agent-config`, puis par redémarrage de
`deployment/kubeai-agent` dans `kubeai`; l'image FastAPI ne change pas. Prévoir
un endpoint accessible par le pod et appliquer le manifeste depuis la racine :

```powershell
kubectl --context minikube -n kubeai edit configmap kubeai-agent-config
kubectl --context minikube -n kubeai rollout restart deployment/kubeai-agent
kubectl --context minikube -n kubeai rollout status deployment/kubeai-agent
kubectl --context minikube -n kubeai port-forward service/kubeai-agent 8000:8000
$headers = @{ Authorization = "Bearer $env:KUBEAI_API_KEY" }
Invoke-RestMethod http://127.0.0.1:8000/v1/models -Headers $headers
```

Pour activer le local dans cette ConfigMap, régler `LLM_PROVIDER=local`,
`LLM_BASE_URL`, `LLM_MODEL`, `LLM_TIMEOUT_SECONDS` et, seulement si le serveur
demande une clé, `LLM_API_KEY_FILE`. Pour revenir au profil par défaut, mettre
`LLM_PROVIDER=mistral-api`; les valeurs `MISTRAL_*` sont déjà présentes.

## Données et confidentialité

Avec `mistral-api`, le backend transmet par HTTPS le prompt, la question, une
partie bornée de l'historique utilisateur et les observations filtrées au
fournisseur externe. Le filtre masque plusieurs secrets et données détectables,
mais ne constitue pas une anonymisation. Confirmer collectivement la région,
les conditions de conservation et les données autorisées avant tout appel réel.

Avec `local`, la génération part vers l'URL configurée. Les données restent sur
la machine uniquement si l'endpoint est réellement auto-hébergé et que son
réseau, ses journaux et son stockage sont sous contrôle du groupe. Un serveur
compatible hébergé ailleurs peut recevoir les mêmes données qu'une API externe.
Open WebUI conserve les conversations sur son PVC dans les deux profils.
Les clés sont fournies par environnement privé ou Secret Kubernetes, jamais
dans Git ou ConfigMap. Les tests simulés bloquent les appels réseau réels.

## Capacité configurée et limites de validation

Valeurs déclarées dans les manifests actifs Kustomize (elles ne sont pas des
mesures de consommation) :

| Charge | CPU demandé / limite | Mémoire demandée / limite | Stockage persistant |
|---|---:|---:|---:|
| Agent FastAPI | 50m / 500m | 64 Mi / 256 Mi | — |
| Mimir | 500m / 2 CPU | 1 Gi / 2 Gi | PVC 10 Gi |
| Loki | 100m / 1 CPU | 256 Mi / 768 Mi | PVC 2 Gi |
| Alloy | 100m / 1 CPU | 192 Mi / 512 Mi | PVC 1 Gi |
| kube-state-metrics | 50m / 250m | 64 Mi / 128 Mi | — |
| Open WebUI | non configurés | non configurées | PVC 1 Gi |
| **Totaux déclarés** | **800m / 4,75 CPU** | **1 600 Mi (1,56 Gi) / 3 712 Mi (3,63 Gi)** | **14 Gi** |

Les totaux mémoire incluent les requêtes et limites des cinq conteneurs qui les
déclarent. Open WebUI n'a aucune requête/limite mémoire configurée ; le total des
limites n'est donc pas un plafond pour l'ensemble du projet. Kubernetes, Docker,
Minikube, le système hôte et la mémoire des volumes s'ajoutent à ces valeurs.
Le Mimir actif réserve à lui seul 1 Gi et est limité à 2 Gi. Le manifeste
Prometheus historique est exclu de Kustomize actif; Grafana et Tempo ne sont
pas déployés. Aucun profil local d'inférence n'est déployé dans Kubernetes.

Le profil Apertus documente une consommation estimée de 7 à 10 Go selon le
contexte et les buffers, en sus de l'environnement de conteneurs. Cela dépasse
le budget du PC Windows 8 Go. Le Mac 32 Go est la machine prévue pour cet essai,
mais la consommation réelle doit être mesurée sur cette machine. Aucun chiffre
de consommation n'a été collecté dans cette étape.

## Évolution des profils dans l'architecture kubeAI

- Le PDF reste inchangé et reste une référence de conception. L'application
  livrée utilise un FastAPI commun ; LangChain n'est pas intégré.
- La pile de collecte active est Alloy → Mimir + Loki. Prometheus autonome est
  conservé hors du rendu Kustomize; Grafana et Tempo restent à faire.
- L'agent utilise Kubernetes, Mimir et Loki par clients directs. Il ne fournit
  pas de remédiation, d'exécution distante ni d'accès au contenu des Secrets.
- Le défaut est Mistral API ; l'option Apertus conserve l'apport local de Vincent.
  L'emplacement effectif de traitement détermine si les observations quittent
  l'infrastructure de l'équipe.
- Les démonstrations A/B/C sont disponibles avec repli déterministe; leur
  fonctionnement de bout en bout sur cluster doit encore être validé.

## Vérifications réelles encore à faire

1. Sur Mac 32 Go, mesurer le démarrage et la mémoire du serveur Apertus sans
   lancer simultanément le déploiement complet si la mémoire est insuffisante.
2. Valider la connectivité du pod FastAPI vers l'endpoint local, `/llm/status`,
   une réponse JSON et SSE, puis basculer sur `mistral-api` sans reconstruire
   l'image. Le contrôle de Mistral doit être explicitement autorisé.
3. Sur un cluster adapté, valider les PVC, l'ingestion Alloy vers Mimir/Loki,
   les données Kubernetes, Open WebUI, et les parcours A/B/C avec données
   synthétiques. Vérifier au préalable la région et les données sortantes.
4. Relever l'utilisation réelle CPU/RAM/disque avant de fixer des ressources
   de production. La validation statique Kustomize et les tests simulés ne
   prouvent pas le comportement d'un cluster.
