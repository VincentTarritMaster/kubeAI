# Nettoyage manuel Exoscale

Ce document est **hors de la politique `kubeai-deployer`**. La politique de
déploiement ne contient volontairement aucune permission `delete-*`.

Le nettoyage doit être exécuté par un compte administrateur/propriétaire, après
sauvegarde et autorisation explicite. Aucun script ne l’exécute automatiquement.

## Préparation

1. Sauvegarder les données Mimir/Loki/WebUI nécessaires.
2. Vérifier `scripts/exoscale/status.ps1`.
3. Vérifier les volumes :

```powershell
exo compute block-storage list --zone ch-gva-2
```

4. Ne pas supprimer les PVC, volumes SBS ou snapshots sans procédure de
   sauvegarde validée.

## Secours si `scale 0` est refusé

`stop.ps1` tente `scale-sks-nodepool 0`. Si Exoscale refuse l’opération ou si
des instances restent visibles :

1. ne pas supprimer les Nodepools ni les volumes ;
2. relever l’erreur exacte et l’état des instances ;
3. vérifier avec un compte administrateur :
   `exo compute sks nodepool show ...` et `exo compute instance list --zone ch-gva-2` ;
4. ouvrir un incident Exoscale ou demander une intervention humaine ;
5. ne reprendre qu’après confirmation que les instances sont arrêtées.

Aucun contournement automatique destructif n’est autorisé.

## Ordre de suppression

### 1. Nodepools

```powershell
exo compute sks nodepool delete kubeai-observed observed --zone ch-gva-2
exo compute sks nodepool delete kubeai-supervision supervision --zone ch-gva-2
```

### 2. Clusters SKS

```powershell
exo compute sks delete kubeai-observed --zone ch-gva-2
exo compute sks delete kubeai-supervision --zone ch-gva-2
```

### 3. Règles du Security Group

Lister les règles et récupérer leurs IDs :

```powershell
exo compute security-group show kubeai-private-sg -O json
```

Puis supprimer chaque règle indésirable avec l’ID retourné :

```powershell
exo compute security-group rule delete kubeai-private-sg <RULE-ID>
```

### 4. Security Group

```powershell
exo compute security-group delete kubeai-private-sg
```

### 5. Réseau privé

```powershell
exo compute private-network delete kubeai-private --zone ch-gva-2
```

## Après le nettoyage

```powershell
exo compute sks list --zone ch-gva-2
exo compute instance list --zone ch-gva-2
exo compute block-storage list --zone ch-gva-2
exo compute load-balancer list --zone ch-gva-2
exo compute private-network list --zone ch-gva-2
exo compute security-group list
```

Les volumes SBS peuvent rester présents et facturables. Leur suppression est une
opération distincte, destructive, qui exige une sauvegarde et une autorisation
explicite.

Aucune commande de ce document n’a été exécutée.
