# Déploiement KubeAI sur Exoscale SKS

Ce guide prépare un déploiement **sans l’avoir exécuté**. Aucune ressource
facturée n’a été créée.

## 1. Architecture

| Rôle | Cluster | Nodepool | Type | CPU | RAM |
|---|---|---|---|---|---|
| observé | `kubeai-observed` | `observed` | `standard.medium` | 2 | 4 Gio |
| supervision | `kubeai-supervision` | `supervision` | `standard.large` | 4 | 8 Gio |

- zone : `ch-gva-2` ;
- SKS Starter, un nœud par cluster ;
- réseau privé commun `kubeai-private` ;
- CSI Exoscale ;
- aucun GPU ;
- aucun Load Balancer public ;
- Open WebUI accessible uniquement par `kubectl port-forward`.

## 2. Prérequis

```powershell
exo config list
exo zone
exo limits
kubectl version --client
helm version
docker version
```

Le compte de préparation est en lecture seule. La création des SKS et le
déploiement nécessitent un **second compte IAM de déploiement**, avec des
permissions limitées et une validation explicite.

## 3. Infrastructure

Le descriptif est dans :

- [`infra/exoscale/config.json`](../../infra/exoscale/config.json)
- [`infra/exoscale/README.md`](../../infra/exoscale/README.md)

Les deux commandes de création sont documentaires et ne doivent pas être
lancées avec le compte lecture seule.

Bootstrap initial idempotent :

```powershell
.\scripts\exoscale\bootstrap.ps1 -DryRun
.\scripts\exoscale\bootstrap.ps1 -Apply
```

Il crée le réseau privé, le Security Group privé et les deux clusters, sans
Nodepool, puis génère des kubeconfigs admin **hors du dépôt**. Une seconde
exécution ne crée pas de doublons.

Les Nodepools ne sont pas créés par le bootstrap. `start.ps1` les crée à taille 1 lors de la première séance, puis ils sont pilotés par :

```powershell
.\scripts\exoscale\start.ps1 -DryRun
.\scripts\exoscale\start.ps1 -Apply
.\scripts\exoscale\status.ps1
.\scripts\exoscale\stop.ps1 -DryRun
.\scripts\exoscale\stop.ps1 -Apply
```

## 4. Réseau interclusters

Les Services Kubernetes d’un cluster SKS ne sont pas automatiquement joignables
depuis un autre cluster.

Solution retenue :

- les deux Nodepools sont attachés au même réseau privé Exoscale ;
- Mimir et Loki restent des Services `ClusterIP` privés au cluster de supervision ;
- un proxy Nginx mTLS (`observability-proxy`) expose uniquement les ports
  `30009` (Mimir) et `30310` (Loki) en NodePort ;
- Alloy se connecte en HTTPS avec un certificat client et valide le certificat
  serveur (CA dédiée, `server_name` fixé) ;
- l’agent FastAPI continue d’utiliser Mimir/Loki en HTTP interne au cluster ;
- aucun Service Mimir/Loki/FastAPI n’est exposé par Load Balancer public ;
- les Security Groups limitent le trafic entrant au CIDR du réseau privé.

Génération des certificats, hors Git :

```powershell
.\scripts\exoscale\generate-mtls-certs.ps1 -DryRun
.\scripts\exoscale\generate-mtls-certs.ps1 -Apply
```

Le script crée la CA, le certificat serveur (SAN `mimir.kubeai.svc` et
`loki.kubeai.svc`) et le certificat client `alloy`, puis les Secrets :

- `kubeai-observability-mtls` dans la supervision ;
- `kubeai-alloy-mtls-client` dans le cluster observé.

Les fichiers restent dans `%USERPROFILE%\.kube\kubeai-mtls` et ne doivent jamais
être commités.

Découverte de l’adresse après redémarrage :

```powershell
kubectl --context kubeai-supervision get nodes -o wide
kubectl --context kubeai-supervision get nodes -o jsonpath="{range .items[*]}{.status.addresses[?(@.type==\"InternalIP\")].address}{'\n'}{end}"
```

Ne jamais coder une IP fixe. Mettre à jour le ConfigMap
`kubeai-collector-targets` après chaque changement d’adresse, ou faire découvrir
l’IP par le script de déploiement.

Dans `k8s/overlays/exoscale/observed/kustomization.yaml`, remplacer :

```text
CHANGE_ME_SUPERVISION_PRIVATE_IP
```

par l’adresse privée réelle du nœud de supervision, puis appliquer l’overlay.

Vérifications :

```powershell
kubectl --context kubeai-supervision -n kubeai get svc observability-proxy mimir loki
kubectl --context kubeai-supervision -n kubeai get pods -l app=observability-proxy
kubectl --context kubeai-observed -n collecte get configmap kubeai-collector-targets -o yaml
```

Les URLs Alloy doivent être en `https://` et le montage
`/etc/alloy/mtls` doit contenir `ca.crt`, `client.crt`, `client.key`.

Ne pas exposer `30009`, `30310` ou `8000` sur Internet.

## 5. Images et secrets

### Image FastAPI

L’overlay utilise une référence volontairement non résolue, avec digest
placeholder :

```text
registry.example.invalid/kubeai-agent@sha256:0000000000000000000000000000000000000000000000000000000000000000
```

Avant déploiement :

1. publier l’image dans un registre OCI privé ;
2. remplacer le digest dans `k8s/overlays/exoscale/supervision/kustomization.yaml` ;
3. créer un `imagePullSecret` si le registre est privé ;
4. ne jamais mettre le mot de passe du registre dans Git.

Connexion au registre, hors Git :

```powershell
docker login ghcr.io
kubectl --context kubeai-supervision -n kubeai create secret docker-registry kubeai-registry `
  --docker-server=ghcr.io --docker-username=<user> --docker-password=<token>
```

Le secret `kubeai-registry` devra être référencé par `imagePullSecrets` dans
l’overlay. Aucun mot de passe ne doit être écrit dans un fichier du dépôt.

Script préparé :

```powershell
.\scripts\exoscale\build-image.ps1 -Registry ghcr.io/ORG/kubeai-agent -Tag 0.1.0 -DryRun
```

La publication réelle exige `-Push` et une autorisation explicite. Le test
local `/health` nécessite un kubeconfig de test :

```powershell
.\scripts\exoscale\build-image.ps1 `
  -Registry ghcr.io/ORG/kubeai-agent -Tag 0.1.0 `
  -SmokeKubeConfig "$HOME\.kube\config" -Push
```

Le script pousse l’image et affiche la référence immuable `repo@sha256:...`.
Remplacer le digest placeholder dans l’overlay par cette valeur. Vérifier :

```powershell
kubectl kustomize --load-restrictor=LoadRestrictionsNone k8s/overlays/exoscale/supervision |
  Select-String 'kubeai-agent@sha256'
```

### Mistral

Le Secret doit être créé localement, à partir du fichier externe :

```powershell
kubectl --context kubeai-supervision -n kubeai create secret generic kubeai-mistral `
  --from-file=api-key="$HOME\.config\kubeai\mistral.key" `
  --dry-run=client -o yaml | kubectl --context kubeai-supervision apply -f -
```

Ne pas coller la clé dans un terminal journalisé, un fichier YAML ou un rapport.

### Authentification de l’API KubeAI

```powershell
$apiKey = -join ((48..57) + (65..90) + (97..122) | Get-Random -Count 48 | ForEach-Object {[char]$_})
kubectl --context kubeai-supervision -n kubeai create secret generic kubeai-api-auth `
  --from-literal=api-key=$apiKey --dry-run=client -o yaml |
  kubectl --context kubeai-supervision apply -f -
Remove-Variable apiKey
```

Cette commande est présentée à titre d’exemple ; utiliser le mécanisme de
gestion de secrets retenu par l’équipe.

## 6. Kubeconfig observé et renouvellement

L’agent de supervision doit lire le cluster observé avec un ServiceAccount
lecture seule.

Procédure :

1. créer le ServiceAccount `kubeai-agent` et son RBAC dans le cluster observé ;
2. générer un jeton de durée limitée :

```powershell
$token = kubectl --context kubeai-observed -n kubeai create token kubeai-agent --duration=24h
```

3. construire un kubeconfig avec l’endpoint SKS observé et la CA ;
4. stocker ce kubeconfig dans le Secret `kubeai-observed-kubeconfig` du cluster
   de supervision, jamais dans Git ;
5. relancer l’agent après mise à jour.

Le jeton de 24 heures est une limite de sécurité volontaire. Il doit être
renouvelé avant expiration. Aucun privilège administrateur permanent n’est
accordé à l’agent.

Script de réémission contrôlée :

```powershell
.\scripts\exoscale\refresh-observed-token.ps1 -DryRun
.\scripts\exoscale\refresh-observed-token.ps1 -Apply
```

Il utilise le kubeconfig admin observé de l’opérateur, crée un jeton de 24 h,
met à jour le Secret de supervision puis redémarre l’agent. Il ne donne aucun
droit admin au pod agent et n’écrit rien dans le dépôt.

Renouvellement périodique sur le poste opérateur :

```powershell
.\scripts\exoscale\install-token-renewal.ps1 -DryRun
.\scripts\exoscale\install-token-renewal.ps1 -Apply
```

La tâche planifiée s’exécute toutes les 12 heures sous l’utilisateur courant ;
elle nécessite que le kubeconfig admin hors dépôt soit accessible. `start.ps1
-Apply` réémet aussi le jeton automatiquement, sauf avec `-SkipTokenRefresh`.

Le ServiceAccount `kubeai-agent` conserve uniquement `list` sur les nœuds, pods
et événements. Il n’a aucun accès aux Secrets, aucune permission de création,
modification ou suppression de pods.

## 7. Déploiement des applications

Les overlays sont indépendants des manifests Minikube :

```text
k8s/overlays/exoscale/observed
k8s/overlays/exoscale/supervision
```

Construction :

```powershell
kubectl kustomize --load-restrictor=LoadRestrictionsNone k8s/overlays/exoscale/observed
kubectl kustomize --load-restrictor=LoadRestrictionsNone k8s/overlays/exoscale/supervision
```

Application, après remplacement de l’image et des URLs privées :

```powershell
kubectl kustomize --load-restrictor=LoadRestrictionsNone k8s/overlays/exoscale/observed |
  kubectl --context kubeai-observed apply -f -

kubectl kustomize --load-restrictor=LoadRestrictionsNone k8s/overlays/exoscale/supervision |
  kubectl --context kubeai-supervision apply -f -
```

Le `LoadRestrictionsNone` est nécessaire car les overlays réutilisent les
manifests communs situés dans `k8s/monitoring`. Il ne faut pas déplacer les
fichiers de base dans le dossier overlay sans raison.

## 7 bis. Persistance et redémarrage

Les overlays forcent la classe de stockage :

```text
exoscale-sbs
```

PVC concernés :

- `alloy-data` (observé) ;
- `mimir-data`, `loki-data`, `open-webui-data` (supervision).

Vérifier avant déploiement :

```powershell
kubectl --context kubeai-supervision get storageclass exoscale-sbs
```

`stop.ps1 -Apply` réduit uniquement les Nodepools à 0. Il ne supprime jamais un
PVC, un volume SBS ou un snapshot. Les PVC restent dans les clusters et sont
réattachés au prochain démarrage.

Test réel de persistance à exécuter après autorisation :

1. écrire un marqueur dans Mimir/Loki et une conversation WebUI ;
2. arrêter les Nodepools avec `stop.ps1 -Apply` ;
3. vérifier que les volumes SBS sont toujours présents ;
4. redémarrer avec `start.ps1 -Apply` ;
5. vérifier le même marqueur et la même conversation.

## 8. Vérifications après déploiement

```powershell
kubectl --context kubeai-observed get nodes
kubectl --context kubeai-supervision get nodes
kubectl --context kubeai-observed -n collecte get pods
kubectl --context kubeai-supervision -n kubeai get pods
kubectl --context kubeai-supervision -n kubeai get pvc
```

Open WebUI :

```powershell
kubectl --context kubeai-supervision -n kubeai port-forward svc/open-webui 8080:80
```

Puis ouvrir `http://127.0.0.1:8080`.

Aucun Load Balancer public n’est nécessaire pour les démonstrations.

## 9. Sécurité

- inscriptions Open WebUI désactivées (`ENABLE_SIGNUP=False`) ;
- pas de Service public pour Mimir, Loki ou FastAPI ;
- proxy mTLS devant Mimir/Loki, validation cliente obligatoire ;
- aucune règle Security Group `30000-32767` depuis `0.0.0.0/0` ;
- règles SKS limitées au trafic privé et aux flux kubelet/Calico internes ;
- agent en lecture seule ;
- RBAC observé limité aux nœuds, pods et événements ;
- secrets et certificats hors Git ;
- TLS conservé ;
- pas de compte administrateur permanent dans le pod agent ;
- pas de clé Exoscale dans Terraform/YAML/Git.

## 10. Ce qui n’est pas encore validé

- création réelle des clusters SKS ;
- publication de l’image OCI ;
- connectivité réelle NodePort entre deux clusters SKS ;
- validation réelle du proxy mTLS et des certificats ;
- Security Groups réels ;
- renouvellement automatique du jeton ;
- démonstrations A/B/C sur Exoscale ;
- coûts réels.

Ces points nécessitent un compte de déploiement et une autorisation explicite.
