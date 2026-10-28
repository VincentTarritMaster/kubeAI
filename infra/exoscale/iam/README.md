# IAM `kubeai-deployer` — analyse des scripts Exoscale

Cette fiche analyse uniquement les scripts déjà présents :

- `scripts/exoscale/bootstrap.ps1`
- `scripts/exoscale/start.ps1`
- `scripts/exoscale/stop.ps1`
- `scripts/exoscale/status.ps1`
- `scripts/exoscale/common.ps1`
- `scripts/exoscale/refresh-observed-token.ps1` (aucun appel Exoscale API)

Politique prête à copier :

[`kubeai-deployer-policy.json`](kubeai-deployer-policy.json)

## 1. Commandes réellement exécutées

| Script | Commande CLI | Opération API | Permission |
|---|---|---|---|
| `common.ps1` | `exo config list` | aucune, local | aucune |
| `common.ps1` / `bootstrap` | `exo compute sks list --zone ch-gva-2` | `list-sks-clusters` | lecture |
| `common.ps1` | `exo compute sks nodepool list --zone ch-gva-2` | lecture des clusters/nodepools | lecture |
| `common.ps1` | `exo compute sks nodepool show <cluster> <pool> --zone` | `get-sks-nodepool` | lecture |
| `bootstrap` | `exo compute private-network list --zone` | `list-private-networks` | lecture |
| `bootstrap` | `exo compute private-network create kubeai-private ...` | `create-private-network` | écriture |
| `bootstrap` | `exo compute security-group list` | `list-security-groups` | lecture |
| `bootstrap` | `exo compute security-group create kubeai-private-sg ...` | `create-security-group` | écriture |
| `bootstrap` | `exo compute security-group rule add ...` | `add-rule-to-security-group` | écriture |
| `bootstrap` | `exo compute sks create ...` | `create-sks-cluster` | écriture |
| `start` | `exo compute sks nodepool add ... --size 1` | `create-sks-nodepool` | écriture |
| `bootstrap` | `exo compute sks kubeconfig ...` | `generate-sks-cluster-kubeconfig` | lecture/secret |
| `start` | `exo compute sks nodepool scale ... 1` | `scale-sks-nodepool` | écriture |
| `stop` | `exo compute sks nodepool scale ... 0` | `scale-sks-nodepool` | écriture |
| `stop`/`status` | `exo compute instance list --zone` | `list-instances` | lecture |
| `stop`/`status` | `exo compute block-storage list --zone` | `list-block-storage-volumes` | lecture |
| `stop`/`status` | `exo compute load-balancer list --zone` | `list-load-balancers` | lecture |
| `stop`/`status` | `exo compute private-network list --zone` | `list-private-networks` | lecture |
| `status` | `exo limits` | `list-quotas` | lecture |
| `stop`/`status` | `exo compute sks list` | `list-sks-clusters` | lecture |

`refresh-observed-token.ps1` ne fait **aucun appel Exoscale** : il utilise
`kubectl` avec un kubeconfig admin observé pour créer un jeton de ServiceAccount.

### Vérification du nom `add-rule-to-security-group`

La référence API et le binaire `exo 1.103.2` utilisent :

```text
add-rule-to-security-group
POST /security-group/{id}/rules
```

La page « Policy Examples » mentionne `add-security-group-rule`, mais la
référence IAM/API actuelle utilise `add-rule-to-security-group`. La politique
suit la référence API actuelle. Point à confirmer par l’audit trail lors du
premier bootstrap.

### Opérations de lecture complémentaires

Le CLI ajoute des appels de découverte qui ne sont pas visibles dans le code
PowerShell :

- `list-zones` ;
- `get-operation` (suivi des opérations asynchrones) ;
- `list-instance-types` (résolution de `standard.medium`/`standard.large`) ;
- `list-sks-cluster-versions` (version SKS par défaut).

Elles sont autorisées en lecture uniquement. `list-templates` n’est pas
autorisé : si un prochain bootstrap échoue sur cette opération, l’ajouter après
audit, pas par défaut.

## 2. Politique IAM

La politique :

- `default-service-strategy: deny` ;
- uniquement le service `compute` ;
- zone `ch-gva-2` pour toutes les opérations zonales ;
- noms de clusters, Nodepools, réseau et Security Group limités ;
- création de Nodepool strictement liée au couple cluster/nom/type d’instance ;
- taille 1 et disque 20 Gio à la création par `start.ps1` ;
- scale limité à 0 ou 1 ;
- **aucune** permission de suppression (`delete-*`) ;
- **aucune** permission IAM, SOS, DBaaS, DNS, Load Balancer, volume SBS.

### Nettoyage manuel

Le nettoyage n’est **pas** délégué au rôle de déploiement. Il doit être exécuté
manuellement avec un compte administrateur, après autorisation :

Voir [`manual-cleanup.md`](manual-cleanup.md).

Les volumes SBS ne doivent jamais être supprimés automatiquement : ils peuvent
contenir les données Mimir/Loki/WebUI.

### Mapping strict des Nodepools

| Cluster | Nodepool | Type | ID |
|---|---|---|---|
| `kubeai-observed` | `observed` | `standard.medium` | `b6e9d1e8-89fc-4db3-aaa4-9b4c5b1d0844` |
| `kubeai-supervision` | `supervision` | `standard.large` | `c6f99499-7f59-4138-9427-a09db13af2bc` |

La référence IAM `create-sks-nodepool` expose `parameters.instance_type`.
L’API `POST /sks-cluster/{id}/nodepool` définit `instance-type` comme un objet
contenant un `id` UUID. L’expression CEL utilise donc :

```text
parameters.instance_type.id
```

La référence IAM ne détaille pas explicitement le binding imbriqué `.id` ; ce
point doit être confirmé au premier appel API.

### Limites de nommage

Les règles `resources.sks_cluster.name` et `resources.sks_nodepool.name`
supposent que les ressources sont chargées par l’API. C’est documenté pour
`get` et `scale`, mais la création de Nodepool doit être validée par un premier
bootstrap réel.

## 3. Audit du bootstrap

| Point | Constat |
|---|---|
| Deux SKS Starter | oui, `kubeai-observed`, `kubeai-supervision` |
| Nodepools initiaux | aucun (le bootstrap ne crée pas de Nodepool ; `start.ps1` crée à 1) |
| Disque local | 20 Gio (minimum API SKS = 20) |
| Réseau privé | `kubeai-private`, CIDR `10.20.0.0/24` |
| Security Group | `kubeai-private-sg` |
| CSI | `--exoscale-csi` |
| CCM | CCM Exoscale par défaut, pas de `--no-exoscale-ccm` |
| Load Balancer | aucun |
| NodePort public | aucune règle `30000-32767` depuis `0.0.0.0/0` |
| Idempotence | vérification par noms ; correction du filtrage Nodepool |
| Kubeconfig | généré hors dépôt, TTL admin 12 h |

### Correction réalisée

`Get-ExoNodePools` passait auparavant le nom du cluster à
`exo compute sks nodepool list`, alors que cette commande ne prend pas de
cluster en argument et liste les Nodepools de la zone. Le code filtre désormais
sur `cluster` :

```powershell
$pools = @(... 'nodepool', 'list', '--zone', ...)
$pools | Where-Object { $_.cluster -eq $Cluster -or $_.cluster.name -eq $Cluster }
```

Sans ce filtre, une collision de nom entre deux clusters pouvait fausser
l’idempotence.

## 4. Procédure portail

Le JSON local `create-sks-nodepool` est déjà proposé avec `size == 1`. Le rôle
IAM réel sur Exoscale n’a **pas** été modifié par cette mission. Il doit être
mis à jour dans le portail avant le premier `start.ps1 -Apply`, sinon la
création des Nodepools sera refusée.

1. Se connecter au portail Exoscale avec un compte autorisé à gérer IAM.
2. Aller dans **IAM → Roles**, créer `kubeai-deployer`.
3. Coller le contenu de `kubeai-deployer-policy.json` dans l’éditeur de
   politique JSON.
4. Vérifier la stratégie par défaut `deny`, le service `compute` et la zone
   `ch-gva-2`.
5. Enregistrer.
6. Aller dans **IAM → API Keys**, créer une clé dédiée `kubeai-deployer`.
7. Sélectionner le rôle `kubeai-deployer`.
8. Copier la clé et le secret **une seule fois** dans un gestionnaire de
   secrets. Ne jamais les coller dans le dépôt, un ticket ou ce chat.
9. Configurer un profil CLI distinct hors dépôt :

```powershell
exo config add
# account name : kubeai-deployer
# zone          : ch-gva-2
```

10. Vérifier que le profil est sélectionné, puis exécuter les contrôles
    non destructifs ci-dessous.

Références officielles :

- [Writing IAM policies](https://community.exoscale.com/product/security/iam/how-to/policy-guide/)
- [Policy examples](https://community.exoscale.com/product/security/iam/how-to/policy-examples/)
- [Manage IAM roles](https://community.exoscale.com/product/security/iam/how-to/role-mgmt/)

## 5. Validation non destructive

```powershell
exo config list
exo iam role show kubeai-deployer --policy --output-format json
exo zone
exo limits
exo compute sks list --zone ch-gva-2
exo compute instance list --zone ch-gva-2
exo compute block-storage list --zone ch-gva-2
exo compute load-balancer list --zone ch-gva-2
exo compute private-network list --zone ch-gva-2
exo compute security-group list
```

Vérification JSON locale :

```powershell
Get-Content infra/exoscale/iam/kubeai-deployer-policy.json -Raw |
  ConvertFrom-Json | Out-Null
"Déployer policy JSON: OK"
```

### Limites des vérifications en lecture seule

- elles prouvent que la clé peut **lire**, pas qu’elle peut créer/scaler ;
- elles ne prouvent pas que les règles CEL `resources.*` sont chargées pour
  chaque opération ;
- il n’existe pas de simulateur IAM destructif sûr dans les scripts ;
- ne jamais tester une permission d’écriture par `exo compute sks delete` ou
  une suppression de volume ;
- le premier bootstrap réel est le vrai test d’autorisation ; il doit être
  confirmé et supervisé ;
- `exo iam role show --policy` vérifie la politique attachée, pas son effet.

## 6. Permissions incertaines à valider

| Élément | Incertitude |
|---|---|
| `list-sks-nodepools` | opération absente de la référence IAM actuelle ; le CLI `nodepool list` ne prend pas de cluster et semble lire les clusters. Non ajoutée pour ne pas inventer. |
| `get-sks-cluster` | ajoutée car le CLI peut l’utiliser pour lister/afficher les Nodepools. |
| `list-instance-types`, `list-sks-cluster-versions` | appels probables de découverte CLI ; autorisés en lecture. |
| `parameters.level` sur `create-sks-cluster` | champ `level` documenté dans l’API ; à confirmer avec l’audit trail. |
| `parameters.disk_size` sur `create-sks-nodepool` | champ normalisé `disk_size` dans IAM ; à confirmer. |
| `add-rule-to-security-group` | nom actuel API/IAM ; la doc Policy Examples utilise l’ancien alias. |
| `get-security-group` | ajouté en lecture pour validation ; non utilisé directement par les scripts. |

Aucun secret, aucune ressource Cloud, aucun commit/push n’a été effectué.
