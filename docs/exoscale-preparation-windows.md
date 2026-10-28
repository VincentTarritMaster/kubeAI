# Préparation Exoscale sous Windows — 28 octobre 2026

Rapport d’installation et de préparation. Aucune clé API, aucun secret et aucun
token n’est reproduit ici.

## 1. État initial

| Élément | État |
|---|---|
| Windows | Windows 11 Home |
| PowerShell | session PowerShell Windows |
| Politique d’exécution | `CurrentUser = RemoteSigned`, `LocalMachine = RemoteSigned` |
| Git | installé, `2.45.1.windows.1` |
| Docker | installé, `29.8.2` |
| kubectl | installé, client `v1.36.1` |
| Minikube | installé, `v1.39.0` |
| Helm | installé, `v4.3.0` |
| Python | installé, `3.12.1` |
| Scoop | absent |
| Exoscale CLI | absent |
| Terraform | absent |

## 2. Installation réalisée

### Scoop

Scoop a été installé dans le profil utilisateur avec l’installateur officiel :

```powershell
Invoke-RestMethod -Uri https://get.scoop.sh | Invoke-Expression
```

Résultat :

```text
Scoop was installed successfully!
Version : v0.6.0
```

Le répertoire `~\scoop\shims` a été ajouté au PATH utilisateur.

### Exoscale CLI

```powershell
scoop bucket add exoscale-cli https://github.com/exoscale/cli
scoop install exoscale-cli
```

Résultat :

```text
exoscale-cli 1.103.2 installé
Shim exo créé
```

Vérification :

```text
exo 1.103.2 63db060a (egoscale v3.1.60)
```

Aucune source non officielle n’a été utilisée.

### Outils volontairement non installés

**Terraform n’a pas été installé.**

Justification :

- aucun manifest Terraform n’existe encore dans KubeAI ;
- le déploiement SKS n’est pas commencé ;
- les scripts actuels utilisent `kubectl`, Helm et les manifests Kubernetes ;
- l’installer maintenant ajouterait un outil sans usage immédiat.

Il faudra le réévaluer si l’infrastructure des deux clusters SKS est décrite en
Terraform.

## 3. Modification de Windows

- installation de Scoop dans `%USERPROFILE%\scoop` ;
- ajout de `%USERPROFILE%\scoop\shims` au PATH utilisateur ;
- aucun changement de la politique d’exécution globale ;
- aucun changement des clusters Minikube ;
- aucun conteneur Docker arrêté ;
- aucune modification des PVC, manifests, secrets ou de l’agent FastAPI.

Les contextes Kubernetes existants sont préservés :

```text
docker-desktop
kubeai-observed
kubeai-supervision
```

## 4. Connexion Exoscale

### État actuel

La CLI est installée et configurée avec deux profils locaux :

```text
kubeai-windows-cli
kubeai_windows_cli  (compte par défaut)
```

Le profil `kubeai-windows-cli` renvoyait `Forbidden: Invalid key or request
signature`. Le profil `kubeai_windows_cli` a été sélectionné comme défaut avec :

```powershell
exo config set kubeai_windows_cli
```

Aucune clé API n’est affichée dans ce rapport.

### Vérifications lecture seule

```text
exo config list  : profils présents
exo zone         : ch-gva-2 accessible
exo limits       : OK
exo compute sks list --zone ch-gva-2 : aucun cluster
exo compute instance list --zone ch-gva-2 : aucune instance
exo compute block-storage list --zone ch-gva-2 : aucun volume
exo compute load-balancer list --zone ch-gva-2 : aucun Load Balancer
exo compute private-network list --zone ch-gva-2 : aucun réseau privé
```

### Quotas observés

| Ressource | Utilisé | Maximum |
|---|---:|---:|
| SKS clusters | 0 | 3 |
| Compute instances | 0 | 20 |
| Network Load Balancers | 0 | 5 |
| Private networks | 0 | 32 |
| Block Storage Volumes | 0 | 1000 |
| Block Storage cumulé | 0 GiB | 20480 GiB |
| Elastic IP | 0 | 5 |

Aucune ressource facturable n’est actuellement présente.

### Permissions

Le compte `kubeai_windows_cli` est utilisé uniquement en lecture. Aucune
tentative de création n’a été faite. Un second compte IAM de déploiement, avec
des permissions minimales, reste nécessaire pour créer les SKS.

### Permissions minimales recommandées

Créer une clé API IAM dédiée avec un rôle ou une politique **lecture seule**,
limitée aux ressources nécessaires :

- lecture SKS ;
- lecture Compute ;
- aucune permission de création, modification ou suppression ;
- aucune permission IAM ;
- aucune permission de création de clés.

Si l’interface Exoscale impose un rôle prédéfini, choisir le rôle le plus
restrictif disponible et vérifier les opérations effectivement autorisées. Une
clé temporaire peut être supprimée après la préparation.

## 5. Tests exécutés

| Test | Résultat |
|---|---|
| `scoop --version` | ✅ `v0.6.0` |
| `exo version` | ✅ `1.103.2` |
| `exo config list` | ✅ profils présents |
| `exo zone` | ✅ `ch-gva-2` |
| `exo limits` | ✅ lecture seule |
| `exo compute sks list --zone ch-gva-2` | ✅ aucun cluster |
| `exo compute instance list --zone ch-gva-2` | ✅ aucune instance |
| `exo compute block-storage list --zone ch-gva-2` | ✅ aucun volume |
| `exo compute load-balancer list --zone ch-gva-2` | ✅ aucun Load Balancer |
| `exo compute private-network list --zone ch-gva-2` | ✅ aucun réseau |
| PATH utilisateur contient `scoop\shims` | ✅ |
| Contextes Minikube préservés | ✅ |
| Clé API exposée | ❌ aucune |
| Cluster/VM/Load Balancer créé | ❌ aucun |
| Dépense Cloud | ❌ aucune |
| Secret dans le dépôt | ❌ aucun |

## 6. Ce qui reste à valider

Le compte lecture seule fonctionne. Il reste à :

- créer un second compte IAM de déploiement avec permissions minimales ;
- valider les tarifs réels dans la console ;
- créer les deux SKS et le réseau privé ;
- générer les kubeconfigs SKS ;
- publier l’image OCI ;
- créer les Secrets hors dépôt ;
- exécuter `start.ps1` et tester A/B/C sur Exoscale.

Ces opérations nécessitent une autorisation explicite et ne font pas partie de
cette préparation.

## 7. État de sécurité

- aucune clé API affichée dans ce rapport ;
- aucune clé écrite dans le dépôt ;
- configuration Exoscale stockée dans
  `%APPDATA%\exoscale\exoscale.toml` ;
- ACL du fichier : utilisateur courant, Administrateurs et Système en contrôle
  total ; le groupe de sandbox de la session dispose d’un accès en lecture ;
- aucune ressource cloud provisionnée ;
- aucune tentative de création effectuée.

L’ACL de la configuration Exoscale doit être vérifiée avant de considérer le
poste comme multi-utilisateur.

## 8. Prochaines étapes

1. Créer un second compte IAM de déploiement avec permissions minimales.
2. Vérifier les tarifs réels dans la console.
3. Créer les deux clusters SKS et le réseau privé après autorisation.
4. Générer les kubeconfigs SKS hors dépôt.
5. Publier l’image OCI FastAPI.
6. Créer les Secrets Kubernetes hors dépôt.
7. Déployer les overlays et tester A/B/C.
