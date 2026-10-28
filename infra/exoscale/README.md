# Infrastructure Exoscale — préparation

Ce dossier décrit l’infrastructure **sans l’avoir créée**. Aucun appel Exoscale
en écriture n’a été effectué.

## Choix : Exoscale CLI plutôt que Terraform

Choix retenu pour cette échéance :

- le compte actuel est **lecture seule** ;
- Terraform n’est pas installé ;
- aucun backend Terraform chiffré n’est prêt ;
- le state Terraform pourrait contenir des informations sensibles ;
- deux clusters SKS et deux Nodepools restent simples à piloter avec `exo` ;
- les scripts `start/stop/status` doivent fonctionner même si Kubernetes ne
  répond plus, ce que la CLI Exoscale permet directement.

Terraform reste une évolution possible quand un compte de déploiement et un
backend sécurisé seront disponibles.

## Paramètres

Voir [`config.json`](config.json).

| Rôle | Cluster | Nodepool | Type | CPU | RAM |
|---|---|---|---|---|---|
| observé | `kubeai-observed` | `observed` | `standard.medium` | 2 | 4 Gio |
| supervision | `kubeai-supervision` | `supervision` | `standard.large` | 4 | 8 Gio |

- zone : `ch-gva-2` ;
- SKS **Starter** ;
- un nœud par cluster en fonctionnement ;
- réseau privé commun : `kubeai-private` ;
- CSI Exoscale activé ;
- CNI Calico ;
- aucun GPU ;
- aucun Load Balancer public prévu.

## Prérequis hors création

```powershell
exo config list
exo zone
exo limits
```

Le compte doit être configuré avec une clé **lecture seule** pour les tests de
préparation. La création nécessitera un second compte IAM de déploiement avec
des droits limités et une confirmation explicite.

## Commandes de création prévues — NON EXÉCUTÉES

Le bootstrap idempotent est :

```powershell
.\scripts\exoscale\bootstrap.ps1 -DryRun
.\scripts\exoscale\bootstrap.ps1 -Apply
```

Il crée, si absents : le réseau privé, le Security Group privé et les deux SKS.
Il **ne crée aucun Nodepool** : les Nodepools sont créés plus tard par
`start.ps1`, avec une autorisation explicite et des machines facturables.
Une seconde exécution ne crée pas de doublon. Il génère aussi des kubeconfigs
admin **hors du dépôt** pour les contexts SKS.

La plage DHCP vient de `config.json` :

```json
"privateNetworkStartIp": "10.20.0.2",
"privateNetworkEndIp": "10.20.0.200",
"privateNetworkNetmask": "255.255.255.0"
```

Le bootstrap refuse toute fin de plage `.254` ou `.255`, ainsi que les adresses
hors réseau. Il ne corrige ni ne supprime un réseau existant dont la plage
diffère : il s’arrête avec une erreur explicite.

### Comportement après échec partiel

- échec avant la création du réseau : aucun objet Cloud n’est créé ;
- échec après création du réseau : le second passage voit le réseau et continue
  avec le Security Group et les SKS manquants ;
- échec après création d’un cluster : le second passage saute le cluster et
  poursuit le contrôle des autres clusters ; aucun Nodepool n’est créé par le
  bootstrap ;
- aucun rollback automatique et aucune suppression ne sont effectués ;
- un réseau existant avec une mauvaise plage doit être corrigé manuellement
  (voir `infra/exoscale/iam/manual-cleanup.md`).

Les commandes manuelles ci-dessous restent la référence de ce que fait le
script. Elles ne doivent pas être lancées avec le compte lecture seule.

### Réseau privé

```powershell
exo compute private-network create kubeai-private --zone ch-gva-2 `
  --start-ip 10.20.0.2 --end-ip 10.20.0.200 --netmask 255.255.255.0
```

L’adresse `10.20.0.254` est réservée au DHCP par Exoscale et ne doit jamais
être utilisée comme fin de plage.

### Cluster observé

```powershell
exo compute sks create kubeai-observed `
  --zone ch-gva-2 `
  --service-level starter `
  --cni calico `
  --exoscale-csi `
  --nodepool-size 0 `
  --label kubeai.io/role=observed
```

### Cluster supervision

```powershell
exo compute sks create kubeai-supervision `
  --zone ch-gva-2 `
  --service-level starter `
  --cni calico `
  --exoscale-csi `
  --nodepool-size 0 `
  --label kubeai.io/role=supervision
```

Le bootstrap ne crée **aucun Nodepool**. `start.ps1 -Apply` crée les Nodepools
`observed` et `supervision` à taille 1, disque 20 Gio, réseau et SG existants.
Cette création est facturable et n’est jamais lancée automatiquement.

## Réseau interclusters

Les deux Nodepools partagent `kubeai-private`. Les nœuds peuvent se joindre par
leur adresse privée.

La solution prévue est :

- Mimir et Loki restent en `ClusterIP` dans le cluster de supervision ;
- un proxy mTLS Nginx expose uniquement `30009` et `30310` en NodePort ;
- Alloy envoie vers `https://<supervision-private-ip>:30009` et
  `https://<supervision-private-ip>:30310` avec certificat client ;
- aucun Service public pour Mimir, Loki ou FastAPI ;
- l’agent utilise le kubeconfig SKS observé, avec un ServiceAccount en lecture
  seule ;
- Open WebUI reste accessible par `kubectl port-forward`.

Les certificats sont générés hors Git par
`scripts/exoscale/generate-mtls-certs.ps1`. Les Security Groups limitent
l’accès au CIDR du réseau privé.

Le bootstrap ajoute aussi les règles internes SKS :

- TCP 10250 depuis le Security Group lui-même (kubelet) ;
- UDP 4789 depuis le Security Group lui-même (VXLAN Calico).

Il n’ajoute **aucune** règle `30000-32767` depuis `0.0.0.0/0`. Par défaut,
Exoscale refuse tout ingress non explicitement autorisé ; les NodePorts ne sont
donc joignables que depuis le réseau privé.

## Coûts

Les tarifs sont des **estimations à vérifier** dans la console Exoscale avant
tout démarrage :

```
standard.medium : environ 0,08 CHF/h
standard.large  : environ 0,16 CHF/h
```

À vérifier avec les tarifs réels de la zone `ch-gva-2` et les volumes SBS.

Un nœud actif par cluster, soit environ **0,24 CHF/h** selon ces hypothèses.
Le plan SKS **Starter est gratuit** (vérifié sur la page Exoscale SKS) ; les
nœuds restent facturés. Les volumes SBS et les Load Balancers le sont aussi.

## Nettoyage

- `stop.ps1` réduit les deux Nodepools à 0 ;
- aucun PVC n’est supprimé ;
- les volumes SBS restent facturables tant qu’ils existent ;
- les clusters SKS et le réseau privé restent aussi présents ;
- `status.ps1` liste les ressources encore facturables.
