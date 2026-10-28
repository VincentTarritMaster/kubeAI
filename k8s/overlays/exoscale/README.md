# Overlays KubeAI Exoscale

Deux overlays indépendants de Minikube :

- `observed` : Alloy, kube-state-metrics et RBAC de collecte, namespace
  `collecte` ;
- `supervision` : Mimir, Loki, agent FastAPI et Open WebUI, namespace
  `kubeai`.

Ils réutilisent les manifests de `k8s/` pour éviter la duplication.

## Placeholders à remplacer avant tout déploiement

- digest placeholder `registry.example.invalid/kubeai-agent@sha256:000...` dans l’overlay supervision ;
- `CHANGE_ME_SUPERVISION_PRIVATE_IP` dans l’overlay observé ;
- Secret `kubeai-observed-kubeconfig` à créer hors Git ;
- Secret `kubeai-mistral` à créer hors Git ;
- Secret `kubeai-api-auth` à créer hors Git ;
- Secrets mTLS `kubeai-observability-mtls` et `kubeai-alloy-mtls-client` via
  `scripts/exoscale/generate-mtls-certs.ps1` ;
- Secret `kubeai-registry` pour le pull de l’image privée.

## Validation

```powershell
kubectl kustomize --load-restrictor=LoadRestrictionsNone k8s/overlays/exoscale/observed
kubectl kustomize --load-restrictor=LoadRestrictionsNone k8s/overlays/exoscale/supervision
```

`LoadRestrictionsNone` est nécessaire car les overlays lisent les fichiers
communs dans `k8s/monitoring`.

## Sécurité

- Mimir/Loki en ClusterIP derrière un proxy mTLS NodePort ;
- certificats clients obligatoires, CA dédiée, validation serveur ;
- pas de Load Balancer public ;
- agent en lecture seule ;
- inscription Open WebUI désactivée ;
- aucun secret ni clé privée dans ces fichiers.
