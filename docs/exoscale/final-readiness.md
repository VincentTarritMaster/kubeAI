# KubeAI — final readiness avant Exoscale

Date : 28 octobre 2026. Aucune ressource Exoscale n’a été créée. Aucune image
n’a été publiée. Aucun secret n’est présent dans ce document.

## 1. Modifications réalisées

### Communication interclusters mTLS

- Ajout de `k8s/overlays/exoscale/supervision/observability-proxy.yaml` :
  proxy Nginx mTLS, deux serveurs TLS, `ssl_verify_client on`, upstreams
  `mimir.kubeai.svc:9009` et `loki.kubeai.svc:3100`.
- Ajout de `k8s/overlays/exoscale/supervision/nginx.conf`.
- Ajout du Service NodePort `observability-proxy` :
  - 30009 → Mimir ;
  - 30310 → Loki.
- Mimir et Loki ne sont plus exposés directement en NodePort ; ils restent en
  `ClusterIP`.
- Ajout du certificat client et de la CA dans Alloy :
  `k8s/overlays/exoscale/observed/config.alloy`.
- Ajout du volume `kubeai-alloy-mtls-client` et du montage
  `/etc/alloy/mtls` dans Alloy.
- Ajout de `scripts/exoscale/generate-mtls-certs.ps1` :
  CA, certificat serveur, certificat client, Secrets Kubernetes hors Git.
- Ajout du contrôle d’accès réseau : voir section 3.

### Jeton observé

- `scripts/exoscale/refresh-observed-token.ps1` réémet un jeton de 24 h et met
  à jour `kubeai-observed-kubeconfig`.
- `scripts/exoscale/install-token-renewal.ps1` prépare une tâche planifiée
  toutes les 12 h.
- `start.ps1` réémet automatiquement le jeton après le démarrage des nœuds,
  sauf `-SkipTokenRefresh`.
- Le ServiceAccount `kubeai-agent` reste limité à `list` sur nœuds, pods et
  événements. Aucun accès Secrets, aucune création/modification/suppression.

### Image FastAPI

- `scripts/exoscale/build-image.ps1` :
  build, test `/health` avec kubeconfig optionnel, publication `-Push`,
  affichage du digest.
- L’overlay utilise désormais une référence par digest placeholder :
  `registry.example.invalid/kubeai-agent@sha256:000...`.
- `imagePullSecrets: kubeai-registry` est prévu dans l’overlay agent.
- Aucune image publiée pendant cette mission.

### Bootstrap et persistance

- `bootstrap.ps1` :
  - réseau privé `kubeai-private`, CIDR `10.20.0.0/24` ;
  - Security Group `kubeai-private-sg` ;
  - règles privées, kubelet 10250 et Calico VXLAN 4789 depuis le SG lui-même ;
  - aucun NodePort public ;
  - deux SKS Starter ;
  - deux Nodepools à 0, disque 20 GiB ;
  - CSI Exoscale activé ;
  - attente de l’état `Running` avant ajout du Nodepool ;
  - idempotence par vérification des noms existants ;
  - kubeconfigs admin hors dépôt.
- Tous les PVC Mimir/Loki/Alloy/WebUI forcent `storageClassName: exoscale-sbs`.
- `stop.ps1` ne supprime aucun PVC/volume.
- `start.ps1` redécouvre l’adresse privée, met à jour les URLs Alloy et
  redémarre Alloy.

### Audit du bootstrap

| Point | Constat |
|---|---|
| Deux SKS Starter | oui, `kubeai-observed` et `kubeai-supervision` |
| Nodepools initiaux | `observed` et `supervision`, taille 0 |
| Réseau privé partagé | oui, `kubeai-private` |
| Security Group | oui, règles privées + kubelet/Calico, aucun NodePort public |
| CSI | `--exoscale-csi` à la création |
| Load Balancer public | aucun |
| Idempotence | vérification par nom avant création ; règles dupliquées tolérées |
| Apply réel | non exécuté pendant cette mission |
| Test SG réel | impossible sans cluster |

### Documentation

- `docs/exoscale/deployment.md`
- `docs/exoscale/cost-and-cleanup.md`
- `docs/exoscale/validation.md`
- `docs/exoscale/pre-deployment-audit.md`
- `docs/exoscale/final-readiness.md`

## 2. Tests réussis et échoués

### Réussis

| Test | Résultat |
|---|---|
| Parsing PowerShell de tous les scripts Exoscale | ✅ OK |
| Kustomize overlay observé | ✅ OK |
| Kustomize overlay supervision | ✅ OK |
| `bootstrap.ps1 -DryRun` | ✅ OK |
| `start.ps1 -DryRun` | ✅ OK |
| `stop.ps1 -DryRun` | ✅ OK |
| Tests offline Exoscale | ✅ 10 passed |
| Génération locale CA/serveur/client | ✅ OpenSSL verify OK |
| EKU serveur/client | ✅ serverAuth/clientAuth |
| Tests agent | ✅ 420 passed |
| Tests scripts Python (hors démo Bash) | ✅ 20 passed |
| `status.ps1` réel lecture seule | ✅ OK |
| `exo zone` / `exo limits` | ✅ ch-gva-2 / quotas lus |
| `exo compute sks list` | ✅ aucun cluster |
| Instances / volumes / LB / réseaux | ✅ aucun |
| Contextes Minikube préservés | ✅ |
| Secret ou clé privée dans Git | ✅ aucun trouvé |
| Ressource Cloud créée / dépense | ❌ aucune |

### Échoués ou non exécutés

| Test | Statut |
|---|---|
| `scripts/test_run_demo_incident.py` | ❌ 10 tests échouent sous le sandbox DSH : Git Bash/MSYS ne peut pas créer son signal pipe (`Win32 error 5`). Ce n’est pas une régression KubeAI. |
| Build Docker local | ❌ bloqué par l’ACL Docker Desktop/DSH (`~/.docker/buildx/.lock`, puis npipe Docker inaccessible). Le Dockerfile et le script sont prêts ; aucun build local validé dans cette session. |
| `bootstrap.ps1 -Apply` | ❌ non exécuté, par contrainte de mission |
| `start.ps1 -Apply` | ❌ non exécuté, aucun cluster |
| `stop.ps1 -Apply` | ❌ non exécuté, aucun cluster |
| mTLS réel sur SKS | ❌ impossible sans cluster |
| Persistance réelle arrêt/redémarrage | ❌ impossible sans cluster |
| Test réseau public/privé réel | ❌ impossible sans cluster |
| Coût réel | ❌ non mesuré |

Les tests simulés ne sont pas des tests Cloud.

## 3. Architecture sécurisée

```text
kubeai-observed                         kubeai-supervision
┌─────────────────────┐                 ┌────────────────────────────┐
│ Alloy               │  HTTPS mTLS     │ observability-proxy        │
│ client cert         │ ──────────────▶ │ Nginx :30009 / :30310      │
│ CA + server_name    │  private IP     │ ssl_verify_client on       │
└─────────────────────┘                 │       │        │           │
                                        │       ▼        ▼           │
                                        │   mimir:9009  loki:3100   │
                                        │       ClusterIP            │
                                        │                            │
                                        │ FastAPI ── HTTP interne ──▶│
                                        │ Open WebUI port-forward    │
                                        └────────────────────────────┘
```

Garanties :

- TLS 1.2/1.3, validation de chaîne et nom serveur ;
- certificat client obligatoire ;
- CA dédiée, clés hors Git ;
- Mimir/Loki non exposés directement ;
- NodePort uniquement sur le proxy ;
- Security Group : ingress privé uniquement, aucune règle NodePort
  `30000-32767` depuis `0.0.0.0/0` ;
- FastAPI continue d’utiliser `mimir.kubeai.svc:9009` et
  `loki.kubeai.svc:3100` en HTTP interne ;
- pas de Load Balancer public ;
- Open WebUI via `kubectl port-forward`.

Découverte d’adresse : `start.ps1` lit `InternalIP` du nœud de supervision et
patche `kubeai-collector-targets`, puis redémarre Alloy. Aucune IP fixe n’est
codée dans les manifestes.

## 4. Budget prévisionnel

### Vérifié dans les sources officielles

- SKS **Starter Cluster : FREE**, sans SLA, selon la page officielle
  [Exoscale SKS](https://www.exoscale.com/sks/).
- Trafic entrant et inter-nœuds annoncé gratuit sur la page
  [Compute](https://www.exoscale.com/compute/).

### Hypothèses non vérifiées

```text
standard.medium : 0,08 CHF/h
standard.large  : 0,16 CHF/h
deux nœuds      : 0,24 CHF/h
```

Les prix Compute/SBS de la page
[Pricing](https://www.exoscale.com/pricing/) sont injectés dynamiquement ; aucun
montant ferme n’a pu être extrait sans la console ou le calculateur. Les
disques locaux de 20 GiB, les volumes SBS et le trafic sortant restent à
vérifier.

Budget :

```text
20 CHF de crédits
5 CHF de réserve
15 CHF mobilisables
≈ 62 h de nœuds à 0,24 CHF/h
≈ 0,48 CHF de nœuds pour une session de 2 h
```

Aucune dépense engagée.

## 5. Prérequis IAM de déploiement

Compte lecture seule actuel : `kubeai_windows_cli`. Il ne doit pas être utilisé
pour créer.

Compte de déploiement requis :

- SKS : create/read/update/delete limité aux clusters KubeAI ;
- Nodepools : create/read/update/delete ;
- Private Networks : create/read ;
- Security Groups : create/read/rules ;
- Block Storage/CSI : create/read/attach/detach ;
- aucune administration IAM globale ;
- clé dédiée, révocable, hors Git ;
- validation explicite du budget avant `-Apply`.

## 6. Risques testables uniquement en Cloud

- comportement réel des règles Security Group SKS (kubelet 10250, Calico 4789,
  contrôle plane) ;
- exposition ou non des NodePorts sur l’IP publique des nœuds ;
- valeur réelle de `InternalIP` sur SKS avec réseau privé et Calico ;
- fonctionnement de Nginx mTLS avec les certificats réels ;
- rejet d’un client sans certificat et acceptation d’Alloy ;
- montage CSI `exoscale-sbs` et réattachement après scale down/up ;
- disponibilité de l’image digest dans le registre privé ;
- expiration/renouvellement du jeton sur 24 h ;
- tarifs réels et coût des volumes ;
- comportement FastAPI avec Mimir/Loki après redémarrage.

## 7. Plan précis du premier provisionnement

1. Créer le compte IAM de déploiement et l’authentifier hors dépôt.
2. Vérifier `exo config list`, `exo zone`, `exo limits`.
3. Lancer `bootstrap.ps1 -DryRun`, relire le plan.
4. Lancer `bootstrap.ps1 -Apply` avec confirmation de coût.
5. Vérifier deux clusters Starter, deux Nodepools à 0, le réseau privé, le SG.
6. Générer et protéger les kubeconfigs admin hors dépôt.
7. Exporter `KUBECONFIG` pour la session ; ne pas modifier la configuration
   Minikube.
8. Lancer `start.ps1 -DryRun`, puis `start.ps1 -Apply`.
9. Vérifier nœuds Ready, PVC, absence de Load Balancer public.
10. Générer les certificats mTLS et les Secrets.
11. Publier l’image, récupérer le digest, remplacer le placeholder.
12. Créer les Secrets Mistral, API KubeAI, registry et kubeconfig observé.
13. Appliquer les overlays observé et supervision.
14. Tester mTLS positif/négatif, accès public refusé, FastAPI vers Mimir/Loki.
15. Tester persistance : marqueur, `stop.ps1 -Apply`, volumes, `start.ps1
    -Apply`, vérification.
16. Contrôler `status.ps1` et arrêter les Nodepools après les démonstrations.

## 8. Conclusion

**READY FOR CONTROLLED CLOUD BOOTSTRAP : YES**

Le bootstrap contrôlé peut être lancé après création du compte IAM de
déploiement et autorisation explicite. Il crée uniquement le réseau, le Security
Group et les deux clusters à Nodepools 0.

**READY FOR FULL APPLICATION DEPLOYMENT : NO**

Le déploiement applicatif complet reste bloqué par : image OCI non publiée,
Secrets non créés, mTLS non testé sur SKS, persistance non testée et tarifs
Compute/SBS non confirmés.

Aucun commit ni push n’a été effectué.
