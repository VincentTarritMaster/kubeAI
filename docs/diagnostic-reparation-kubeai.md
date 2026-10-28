# Diagnostic et réparation KubeAI — 28 octobre 2026

Rapport de diagnostic réel. Aucun secret, token, kubeconfig ou contenu de clé
n'est reproduit dans ce document.

## 1. Cause racine

### 1.1 Le cluster observé était arrêté

Preuves initiales :

```text
minikube profile list
kubeai-observed    Stopped
kubeai-supervision OK
```

```text
docker inspect kubeai-observed
ExitCode=130
OOMKilled=false
```

Le code de sortie `130` n'est pas un `OOMKilled` (137). Le nœud avait été arrêté
ou interrompu, et non tué par le noyau pour manque de mémoire.

### 1.2 Le réseau privé interclusters était vide

Avant réparation :

```text
docker network inspect kubeai-private
Containers = {}
```

Les deux nœuds n'étaient donc plus attachés à `192.168.79.0/24`.

### 1.3 Le kubeconfig de l'agent pointait vers une API absente

Le Secret `kubeai-observed-auth` contenait :

```text
server = https://192.168.79.3:8443
CA présent = true
jeton présent et non expiré
```

Le serveur `192.168.79.3:8443` était injoignable tant que
`kubeai-observed` était arrêté et détaché du réseau privé.

Conséquence :

- `/health` répondait 200 ;
- `/ready` interroge `/api/v1/pods` sur le cluster observé ;
- cette requête échouait ;
- `/ready` renvoyait **503** ;
- la readiness probe échouait ;
- le nouveau pod n'était jamais prêt.

### 1.4 Pourquoi deux pods et un rollout bloqué

Le Deployment utilise la stratégie `RollingUpdate` avec une réplique.

Tant que le nouveau pod n'était pas `Ready`, Kubernetes conservait l'ancien pod
pour ne pas interrompre le service. Cela produisait :

```text
1 old replicas are pending termination
0 of 1 updated replicas are available
```

Ce n'était donc pas un pod orphelin ni un ReplicaSet bloqué indépendamment :
l'ancien pod restait volontairement tant que le nouveau ne passait pas sa
readiness.

Après rétablissement du cluster observé et de `/ready`, l'ancien ReplicaSet a
été réduit à zéro et le rollout s'est terminé.

### 1.5 Mistral : la clé est probablement invalide ou révoquée

Le Secret `kubeai-mistral` existe et est monté dans le pod :

```text
/var/run/secrets/kubeai-mistral/api-key
mounted = true
nonempty = true
```

Le fichier externe et le Secret Kubernetes ont la **même empreinte SHA-256**.
La clé est donc bien celle du fichier externe.

Mais l'appel direct à Mistral :

```text
GET https://api.mistral.ai/v1/models
```

renvoie **HTTP 401**, et l'agent renvoie :

```json
{
  "source": "llm",
  "message": "Authentification Mistral API refusée (HTTP 401) ; vérifiez la clé et les droits"
}
```

La cause est donc **la clé elle-même** (invalide, révoquée, mal copiée ou liée à
un compte sans accès), pas :

- le montage du Secret ;
- le chemin du fichier ;
- le fournisseur local ;
- la configuration `.env` ;
- le réseau du cluster.

### 1.6 Le démarrage s'arrêtait sur `/llm/status`

`start-two-clusters.py` traitait `/llm/status` comme un contrôle obligatoire.
Quand Mistral répondait 503, tout le démarrage échouait après avoir pourtant
rendu l'infrastructure opérationnelle.

Correction appliquée : `/ready` et `/cluster/inventory` restent obligatoires ;
`/llm/status` devient un avertissement explicite. La supervision et WebUI
peuvent démarrer même si la clé LLM est invalide.

### 1.7 Erreur Mimir transitoire

Après le redémarrage, Alloy a reçu une fois :

```text
HTTP 500 : send data to ingesters: context deadline exceeded
```

Les logs Mimir ont confirmé une erreur d'ingestion ponctuelle pendant la
reprise. Aucune nouvelle erreur n'a été observée dans les deux minutes
suivantes. Les données et PVC n'ont pas été supprimés.

### 1.8 Mémoire

```text
RAM physique : 7,35 Gio
RAM libre initiale : environ 0,8 Gio
Mémoire Docker : 5,29 Gio
supervision : 1,593 Gio utilisés / 4 Gio
observé : 1,077 Gio utilisés / 2 Gio
```

Les deux clusters tiennent avec les limites actuelles. Il n'y a pas eu
d'`OOMKilled`. Aucun ajustement mémoire n'a donc été appliqué.

## 2. Corrections réalisées

### Infrastructure

- redémarrage de `kubeai-observed` sans suppression de cluster ;
- reconstruction de l'attachement au réseau `kubeai-private` ;
- supervision en `192.168.79.2`, observé en `192.168.79.3` ;
- contextes `kubectl` restaurés et nœuds `Ready` ;
- redémarrage du collecteur Alloy et de l'agent ;
- PVC, volumes et données conservés.

### Code

Fichier modifié :

```text
scripts/start-two-clusters.py
```

Changement : le contrôle `/llm/status` ne bloque plus le démarrage. Il affiche :

```text
AVERTISSEMENT : /llm/status indisponible ; la supervision reste démarrée.
```

Aucun autre comportement n'a été changé.

### Non corrigé volontairement

- aucun renouvellement autonome du jeton observé ;
- aucune modification du Secret Mistral ;
- aucun privilège administrateur permanent accordé à l'agent ;
- aucune suppression de PVC ou de cluster.

Le jeton observé est valable 24 heures et est renouvelé par `start.ps1`. Pour une
session plus longue, relancer `start.ps1` avant expiration. Un renouvellement
automatique nécessiterait un contrôleur disposant d'un accès administrateur au
cluster observé, ce qui élargirait inutilement les privilèges pour cette démo.

## 3. Tests exécutés

### Infrastructure

| Test | Résultat |
|---|---|
| `kubeai-observed` nœud | ✅ Ready |
| `kubeai-supervision` nœud | ✅ Ready |
| Réseau `kubeai-private` | ✅ les deux nœuds attachés |
| Agent `/health` | ✅ HTTP 200 |
| Agent `/ready` | ✅ HTTP 200 |
| Agent `/cluster/inventory` | ✅ HTTP 200, inventaire non vide |
| ReplicaSet agent | ✅ un seul ReplicaSet à 1, les anciens à 0 |
| Rollout agent | ✅ successfully rolled out |
| WebUI `/health` | ✅ HTTP 200 |
| Open WebUI → agent `/v1/models` | ✅ `kubeai`, `kubeai-apertus` |
| Mimir `up{job="kubernetes-cadvisor"}` | ✅ 1 série à 1 |
| Loki audit `job="kubeai-audit"` | ✅ requête réussie |
| Alloy remote write | ✅ reprise après redémarrage, une erreur transitoire |
| `OOMKilled` | ✅ false |

### RBAC réel de l'agent

Exécuté depuis le pod agent avec son kubeconfig observé :

```text
list pods all namespaces : 5 pods lus
list Secrets             : 403 Forbidden
delete pod               : 403 Forbidden
```

L'agent est donc bien en lecture seule pour les pods et ne peut pas lire les
Secrets ni supprimer des ressources.

### Mistral

| Test | Résultat |
|---|---|
| Secret `kubeai-mistral` présent | ✅ |
| Secret monté et non vide | ✅ |
| Empreinte fichier externe = Secret | ✅ |
| `GET /v1/models` direct Mistral | ❌ HTTP 401 |
| `/llm/status` via l'agent | ❌ HTTP 503, authentification Mistral refusée |

### Tests automatisés

Commande :

```powershell
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD='1'
$env:PYTHONPATH='agent'
.\.venv\Scripts\python.exe -B -m pytest agent\tests --ignore=agent\tests\integration -q -p no:cacheprovider
```

Résultat :

```text
420 passed
```

Tests scripts :

```text
20 passed
```

Les tests shell/Bash ne sont pas exécutables dans cet environnement Windows
(accès WSL/Bash refusé).

### Démonstrations A/B/C

**Non exécutées** : Mistral refuse l'authentification, donc aucune réponse LLM
réelle ne peut être produite. Les tests A/B/C ne doivent pas être déclarés
réussis tant que la clé n'est pas remplacée.

## 4. État final

| Composant | État |
|---|---|
| `kubeai-observed` | Running, node Ready |
| `kubeai-supervision` | Running, node Ready |
| Réseau privé | Opérationnel |
| Alloy | 1/1 Running |
| kube-state-metrics | 1/1 Running |
| Mimir | 1/1 Running |
| Loki | 1/1 Running |
| Agent FastAPI | 1/1 Ready |
| WebUI | 1/1 Running, HTTP 200 |
| Mistral | ❌ HTTP 401 avec la clé actuelle |
| A/B/C | Non exécutés à cause de Mistral |

## 5. Sécurité

- aucun secret, token ou contenu de clé n'a été affiché dans ce rapport ;
- le fichier de clé reste hors du dépôt ;
- `.env` est ignoré par Git et référence un fichier externe ;
- le Secret `kubeai-mistral` n'a pas été modifié ;
- l'agent conserve un kubeconfig observé en lecture seule ;
- l'agent ne peut pas lire les Secrets ni supprimer des pods ;
- aucune élévation de privilèges n'a été accordée ;
- aucun commit ni push n'a été effectué.

## 6. Risques restants

1. **Mistral est inutilisable** tant qu'une clé valide n'est pas fournie.
2. Le jeton observé expire après 24 heures ; `start.ps1` le renouvelle au
   démarrage, mais une session plus longue nécessite un redémarrage.
3. Minikube reste lent sur 8 Go ; les deux clusters consomment environ 2,7 Gio
   réels et la RAM libre est faible.
4. Une erreur Mimir d'ingestion transitoire a été observée après redémarrage ;
   elle ne s'est pas reproduite.
5. Les tests A/B/C et FR/EN n'ont pas pu être rejoués.
6. Le démarrage reste dépendant de la disponibilité de l'API Kubernetes
   observée ; le contrôle `/ready` est maintenant explicite.

## 7. Prochaines étapes

1. Créer une nouvelle clé Mistral valide dans la console Mistral.
2. La placer dans le fichier externe déjà configuré :

```text
~/.config/kubeai/mistral.key
```

3. Vérifier la configuration sans afficher la clé :

```powershell
python scripts/configure-runtime.py --check
```

4. Appliquer la configuration :

```powershell
python scripts/configure-runtime.py --context kubeai-supervision
```

5. Redémarrer l'agent :

```powershell
kubectl --context kubeai-supervision -n kubeai rollout restart deployment/kubeai-agent
kubectl --context kubeai-supervision -n kubeai rollout status deployment/kubeai-agent --timeout=180s
```

6. Vérifier Mistral :

```powershell
# via l'API de l'agent, avec la clé KubeAI
# /llm/status doit répondre 200
```

7. Rejouer A/B/C en français et en anglais.

Pour Exoscale : ne pas réutiliser le jeton observé local comme secret permanent ;
prévoir un Secret/Workload Identity, des NetworkPolicies et un renouvellement
de jeton géré par la plateforme.
