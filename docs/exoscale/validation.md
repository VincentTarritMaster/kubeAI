# Validation Exoscale — préparation hors Cloud

Tous les tests ci-dessous ont été exécutés **sans créer de ressource Exoscale**.
Aucun cluster SKS, VM, volume, Load Balancer ou réseau privé n’a été créé.

## 1. Outils

| Outil | Version | État |
|---|---|---|
| PowerShell | Windows PowerShell | OK |
| Scoop | 0.6.0 | installé |
| Exoscale CLI | 1.103.2 | installé |
| Git | 2.45.1 | présent |
| Docker | 29.8.2 | présent |
| kubectl | 1.36.1 | présent |
| Minikube | 1.39.0 | présent |
| Helm | 4.3.0 | présent |
| Terraform | absent | volontairement non installé |

## 2. Validation Kustomize

```powershell
kubectl kustomize --load-restrictor=LoadRestrictionsNone k8s/overlays/exoscale/observed
kubectl kustomize --load-restrictor=LoadRestrictionsNone k8s/overlays/exoscale/supervision
```

Résultat : **OK** pour les deux overlays.

Points contrôlés dans le rendu :

- Alloy/KSM dans `collecte` ;
- Alloy reçoit `MIMIR_WRITE_URL`, `LOKI_WRITE_URL`, `OBSERVED_CLUSTER` ;
- Mimir et Loki restent en `ClusterIP` ;
- proxy mTLS NodePort 30009/30310 avec `ssl_verify_client on` ;
- Alloy monte `kubeai-alloy-mtls-client` et valide la CA ;
- agent en `KUBERNETES_ACCESS_MODE=kubeconfig` ;
- Secret `kubeai-observed-kubeconfig` monté ;
- Open WebUI avec `ENABLE_SIGNUP=False` ;
- PVC avec `storageClassName: exoscale-sbs` ;
- image agent non résolue, référence par digest placeholder.

## 3. Scripts PowerShell

Parsing syntaxique :

```text
PowerShell parse OK
```

Modes simulation :

```powershell
.\scripts\exoscale\bootstrap.ps1 -DryRun
.\scripts\exoscale\start.ps1 -DryRun
.\scripts\exoscale\stop.ps1 -DryRun
```

Résultat : OK, affichage du plan et du coût estimé, aucune action Cloud.

Tests offline avec faux `exo.cmd` et génération de certificats :

```text
scripts/exoscale/tests/test_exoscale_scripts.py .......... 10 passed
```

Les tests couvrent :

- `start.ps1` et `stop.ps1` en DryRun ;
- `bootstrap.ps1` en DryRun ;
- `status.ps1` avec réponses `exo` simulées ;
- génération locale CA/serveur/client, vérification OpenSSL et EKU ;
- renouvellement de jeton et tâche planifiée en DryRun ;
- absence de NodePort public dans les overlays ;
- RBAC agent sans Secrets ni verbes d’écriture ;
- absence de clé privée dans les livrables.

La génération de certificats est un test local, pas un test Cloud.

## 4. Tests du projet

```text
420 tests agent passés
20 tests scripts Python passés
10 tests demo launcher échouent sous le sandbox DSH
```

Les 10 échecs `scripts/test_run_demo_incident.py` viennent de Git Bash/MSYS qui
ne peut pas créer son signal pipe sous le sandbox Windows
(`Win32 error 5`). Ils ne concernent pas le code Exoscale et ne sont pas des
tests Cloud.

Le build Docker local (`build-image.ps1 -BuildOnly`) n’a pas pu être exécuté :
Docker Desktop/DSH refuse l’accès à `~/.docker/buildx/.lock` puis au named pipe
Docker. Le script et le Dockerfile restent préparés ; aucun build local ni push
n’a été effectué.

## 5. Exoscale CLI

```text
exo version
exo 1.103.2 63db060a (egoscale v3.1.60)
```

Profils détectés :

```text
kubeai-windows-cli
kubeai_windows_cli  (défaut)
```

Le profil `kubeai-windows-cli` renvoyait `Forbidden: Invalid key or request
signature`. Le profil `kubeai_windows_cli` a été sélectionné avec
`exo config set kubeai_windows_cli`.

Vérifications lecture seule :

```text
exo zone                                  : ch-gva-2
exo compute sks list --zone ch-gva-2      : aucun cluster
exo compute instance list --zone ch-gva-2 : aucune instance
exo compute block-storage list            : aucun volume
exo compute load-balancer list            : aucun Load Balancer
exo compute private-network list          : aucun réseau
exo limits                                : OK
```

Quotas observés :

```text
SKS clusters       0 / 3
Compute instances  0 / 20
Load Balancers     0 / 5
Private networks   0 / 32
Volumes SBS        0 / 1000
```

Aucune commande de création n’a été tentée. Aucune ressource facturable n’est
présente.

## 6. Ce qui n’est pas validé

- création réelle des deux clusters SKS ;
- Nodepools à 1 et nœuds Ready sur Exoscale ;
- connectivité NodePort privée réelle ;
- Security Groups réels ;
- déploiement de l’image OCI ;
- secrets réels ;
- renouvellement du jeton observé ;
- A/B/C sur Exoscale ;
- coûts réels.

Ces éléments nécessitent un compte de déploiement, les identifiants personnels
et une autorisation explicite.
