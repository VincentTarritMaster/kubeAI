# KubeAI — audit avant déploiement Exoscale

> Note de version : ce document reflète l’audit 1B. Les ajouts mTLS,
> renouvellement automatique et digest sont détaillés dans
> [`final-readiness.md`](final-readiness.md), qui fait foi pour la décision 1C.

Date : 28 octobre 2026. Aucune ressource Exoscale n’a été créée. Aucun secret
n’est reproduit dans ce document.

## 1. Fichiers créés ou corrigés

### Infrastructure et Cloud

- `infra/exoscale/config.json`
- `infra/exoscale/README.md`
- `scripts/exoscale/bootstrap.ps1`
- `scripts/exoscale/common.ps1`
- `scripts/exoscale/start.ps1`
- `scripts/exoscale/stop.ps1`
- `scripts/exoscale/status.ps1`
- `scripts/exoscale/build-image.ps1`
- `scripts/exoscale/refresh-observed-token.ps1`
- `scripts/exoscale/tests/test_exoscale_scripts.py`

### Kubernetes

- `k8s/overlays/exoscale/observed/kustomization.yaml`
- `k8s/overlays/exoscale/observed/config.alloy`
- `k8s/overlays/exoscale/observed/namespace.yaml`
- `k8s/overlays/exoscale/supervision/kustomization.yaml`
- `k8s/overlays/exoscale/supervision/namespace.yaml`
- `k8s/overlays/exoscale/README.md`

### Documentation

- `docs/exoscale/deployment.md`
- `docs/exoscale/cost-and-cleanup.md`
- `docs/exoscale/validation.md`
- `docs/exoscale/pre-deployment-audit.md`
- `docs/exoscale-preparation-windows.md`

Un changement antérieur non commité subsiste dans
`scripts/start-two-clusters.py` ; aucun commit/push n’a été effectué.

## 2. Fonctionnement des scripts

### `bootstrap.ps1`

- `-DryRun` : décrit réseau, Security Group, clusters, Nodepools, kubeconfigs.
- `-Apply` : crée si absents le réseau privé, le Security Group, les deux SKS
  Starter et leurs Nodepools à taille 0.
- idempotent : vérifie les noms avant création ;
- génère des kubeconfigs admin hors dépôt ;
- n’est pas exécuté pendant cette mission.

### `start.ps1`

- `-DryRun` par défaut sans `-Apply` : aucune action ;
- vérifie les identifiants Exoscale ;
- vérifie la présence des clusters (ne les crée pas) ;
- estime le coût ;
- demande confirmation ;
- redimensionne les Nodepools de 0 à 1 ;
- attend les nœuds Ready ;
- met à jour les URLs Alloy depuis l’adresse privée découverte ;
- vérifie les PVC ;
- peut appliquer les overlays avec `-Deploy`.

### `stop.ps1`

- `-DryRun` par défaut ;
- vérifie les PVC avant réduction ;
- réduit les Nodepools à 0 ;
- attend la disparition des nœuds ;
- liste les ressources encore facturables ;
- ne supprime jamais PVC, volumes, secrets ou clusters.

### `status.ps1`

- lecture seule ;
- clusters et Nodepools ;
- instances Compute ;
- volumes SBS ;
- Load Balancers ;
- réseaux privés ;
- estimation horaire ;
- avertit si les clusters sont absents ou si des ressources facturables restent.

## 3. Réseau interclusters : risques et solutions

Solution prévue :

- réseau privé commun `kubeai-private` (10.20.0.0/24) ;
- Security Group `kubeai-private-sg` ;
- Mimir et Loki restent en ClusterIP, derrière le proxy mTLS NodePort 30009/30310 ;
- Alloy utilise l’adresse privée découverte du nœud de supervision ;
- aucune IP fixe codée en dur ;
- aucun Load Balancer public.

### Protection mTLS

Le proxy Nginx `observability-proxy` impose `ssl_verify_client on`, valide une
CA dédiée et expose Mimir/Loki en HTTPS. Alloy monte le certificat client et
valide le certificat serveur avec `server_name` fixé.

Les certificats sont générés hors Git par
`scripts/exoscale/generate-mtls-certs.ps1`. La génération locale et la
vérification OpenSSL réussissent, mais **le proxy et les Secrets mTLS ne sont
pas encore déployés ni testés sur SKS**.

### Découverte des adresses

Après chaque redémarrage :

```powershell
kubectl --context kubeai-supervision get nodes -o wide
kubectl --context kubeai-supervision get nodes -o jsonpath="{range .items[*]}{.status.addresses[?(@.type==\"InternalIP\")].address}{'\n'}{end}"
```

Le `start.ps1` tente de patcher automatiquement
`kubeai-collector-targets` avec l’adresse `InternalIP`. Cette valeur devra être
vérifiée sur SKS : selon le réseau et le CNI, l’adresse privée peut être exposée
différemment. Un test réel est obligatoire.

## 4. Coûts prévisionnels

### Hypothèses non vérifiées

```text
standard.medium : 0,08 CHF/h
standard.large  : 0,16 CHF/h
deux nœuds      : 0,24 CHF/h
```

Aucun tarif ferme n’a été extrait de la page tarifaire dynamique. Les disques
locaux de 20 GiB, les volumes SBS, le trafic et le contrôleur SKS doivent être
vérifiés dans la console.

Avec 20 CHF de crédits et 5 CHF de réserve :

```text
budget mobilisable : 15 CHF
environ 62 h de nœuds à 0,24 CHF/h
```

Une session de 2 h coûte environ 0,48 CHF de nœuds, hors stockage.

Aucune dépense n’a été engagée.

## 5. Tests réellement exécutés

| Test | Résultat |
|---|---|
| Parsing PowerShell de tous les scripts Exoscale | ✅ OK |
| Kustomize observed | ✅ OK |
| Kustomize supervision | ✅ OK |
| `bootstrap.ps1 -DryRun` | ✅ OK |
| `start.ps1 -DryRun` | ✅ OK |
| `stop.ps1 -DryRun` | ✅ OK |
| Tests offline Exoscale | ✅ 10 passed |
| Génération locale certificats mTLS | ✅ OpenSSL verify OK |
| Renouvellement et tâche planifiée en DryRun | ✅ OK |
| `status.ps1` réel en lecture seule | ✅ OK |
| `exo config list` | ✅ profils présents |
| `exo zone` | ✅ ch-gva-2 |
| `exo limits` | ✅ OK |
| `exo compute sks list --zone ch-gva-2` | ✅ aucun cluster |
| `exo compute instance list` | ✅ aucune instance |
| `exo compute block-storage list` | ✅ aucun volume |
| `exo compute load-balancer list` | ✅ aucun LB |
| `exo compute private-network list` | ✅ aucun réseau |
| Tests agent | ✅ 420 passed |
| Tests scripts projet | ✅ 20 passed |
| Contextes Minikube préservés | ✅ |
| Secret exposé | ❌ aucun |
| Ressource Cloud créée | ❌ aucune |
| Dépense Cloud | ❌ aucune |

### Quotas lus

```text
SKS clusters       0 / 3
Compute instances  0 / 20
Load Balancers     0 / 5
Private networks   0 / 32
Volumes SBS        0 / 1000
```

## 6. Prérequis IAM pour provisionner

Le compte `kubeai_windows_cli` est en lecture seule. Il faut un **second compte
IAM de déploiement** avec :

- création/lecture SKS ;
- création/lecture Nodepools ;
- création/lecture réseaux privés et Security Groups ;
- création/lecture volumes SBS via CSI ;
- aucune permission de suppression large ;
- aucune permission IAM globale ;
- clé stockée hors dépôt et révocable.

Le compte lecture seule ne doit jamais être utilisé pour une création.

## 7. Opérations nécessitant une autorisation

1. créer le compte IAM de déploiement ;
2. créer le réseau privé et les Security Groups ;
3. créer les deux clusters SKS ;
4. créer les Nodepools à 0 puis les démarrer ;
5. publier l’image OCI ;
6. créer les Secrets Kubernetes ;
7. générer les kubeconfigs admin ;
8. exécuter le test réseau et le test de persistance ;
9. engager les coûts de session.

## 8. Blocages restants

- **aucun cluster SKS créé** ;
- **image OCI non publiée** ;
- **aucun Secret Kubernetes cloud créé** ;
- **mTLS implémenté dans les manifestes mais non testé sur SKS** ;
- **connectivité NodePort privée non testée** ;
- **persistance réelle non testée** ;
- **renouvellement du jeton automatisé côté poste, non testé en Cloud** ;
- **tarifs réels non vérifiés** ;
- **compte de déploiement IAM absent**.

## 9. Conclusion

**READY FOR CLOUD DEPLOYMENT : NO**

Le socle de préparation est prêt : bootstrap idempotent, overlays, scripts
start/stop/status, quotas, découverte d’adresse, persistance `exoscale-sbs`,
tests hors Cloud. Mais le déploiement réel ne peut pas être déclaré prêt tant
que les points suivants ne sont pas levés :

1. compte IAM de déploiement et autorisation de coût ;
2. publication de l’image et déploiement par digest ;
3. test réel du proxy mTLS et des certificats ;
4. création des SKS et test réseau réel ;
5. secrets hors dépôt et kubeconfigs ;
6. test réel de persistance après arrêt/redémarrage ;
7. vérification tarifaire dans la console.

Aucun commit ni push n’a été effectué.
