# Exoscale — coûts et nettoyage

## Hypothèses de coût — non vérifiées

Les tarifs ci-dessous sont des **hypothèses de préparation**, pas des tarifs
contractuels. La page tarifaire Exoscale consultée le 28 octobre 2026 affiche
les prix via des variables dynamiques ; aucun montant ferme n’a pu être extrait
sans la console ou le calculateur.

| Ressource | Quantité | Hypothèse horaire |
|---|---:|---:|
| `standard.medium` observé | 1 | 0,08 CHF/h |
| `standard.large` supervision | 1 | 0,16 CHF/h |
| Total nœuds actifs | 2 | **0,24 CHF/h** |

Statut :

```text
Instances Medium/Large : hypothèse, à vérifier
Disques locaux 20 GiB  : facturation séparée possible, à vérifier
Volumes SBS            : quantité connue, prix à vérifier
Trafic sortant         : à vérifier
Load Balancers         : 0 prévu
Contrôleur SKS Starter : FREE (vérifié)
```

Avec 20 CHF de crédits et 5 CHF de réserve, le budget réellement mobilisable est
de **15 CHF**. À 0,24 CHF/h, cela représente environ **62 heures de nœuds
actifs**, hors volumes, disques et trafic.

Une session de démonstration de 2 heures coûte environ **0,48 CHF** de nœuds,
hors stockage. Les volumes SBS restent facturables même après l’arrêt des
nœuds.

## Tarifs vérifiés

Sources officielles consultées le 28 octobre 2026 :

- page SKS : Starter Cluster **FREE**, sans SLA ;
- page Compute : trafic entrant et trafic inter-nœuds annoncés gratuits ;
- page Pricing : les prix Compute/SBS sont affichés dynamiquement et n’ont pas
  pu être extraits sous forme de montants fermes.

Statut :

```text
SKS Starter            : vérifié FREE
Trafic entrant/interne : vérifié gratuit (selon page Compute)
Compute Medium/Large   : hypothèse, à confirmer dans la console
Disques locaux 20 GiB  : prix non vérifié
Volumes SBS            : prix non vérifié
Trafic sortant         : prix non vérifié
Load Balancers         : 0 prévu
```

Aucun tarif ferme Medium/Large ou SBS n’a été vérifié. Les valeurs ci-dessus ne
doivent pas être présentées comme contractuelles.

## Ce qui reste facturable après `stop.ps1`

`stop.ps1 -Apply` réduit les Nodepools à 0 et arrête les instances. Il ne
supprime pas :

- les clusters SKS ;
- les volumes SBS/PVC ;
- les réseaux privés ;
- les Security Groups ;
- les éventuels Load Balancers.

Les volumes et les Load Balancers peuvent rester facturables même sans nœud.
`status.ps1` les liste et avertit.

## Vérification avant arrêt

```powershell
.\scripts\exoscale\status.ps1
```

Le script affiche :

- clusters et Nodepools ;
- nombre d’instances ;
- volumes SBS ;
- Load Balancers ;
- réseaux privés ;
- estimation horaire ;
- avertissement si des ressources facturables restent présentes.

## Arrêt

Simulation :

```powershell
.\scripts\exoscale\stop.ps1 -DryRun
```

Réel, après confirmation :

```powershell
.\scripts\exoscale\stop.ps1 -Apply
```

Le script vérifie les PVC avant réduction, tente de lister les volumes, puis
réduit chaque Nodepool à 0. Il ne supprime aucune donnée.

## Redémarrage

```powershell
.\scripts\exoscale\start.ps1 -DryRun
.\scripts\exoscale\start.ps1 -Apply
```

Le script vérifie les quotas, estime le coût, identifie les clusters existants,
crée les Nodepools manquants à 1 ou redimensionne à 1, attend les nœuds
`Ready`, puis vérifie les PVC. Il ne crée aucun cluster.

## Nettoyage complet — manuel et risqué

Ces commandes ne sont **pas exécutées** et ne doivent être lancées qu’après
sauvegarde et accord explicite.

```powershell
# Suppression des Nodepools et clusters SKS
exo compute sks delete kubeai-observed --zone ch-gva-2
exo compute sks delete kubeai-supervision --zone ch-gva-2

# Réseau privé
exo compute private-network delete kubeai-private --zone ch-gva-2
```

La suppression des PVC, volumes SBS et snapshots est **destructive** et doit
être effectuée séparément, après export des preuves nécessaires.

## Règles de sécurité financière

- toujours lancer `status.ps1` avant et après une démonstration ;
- toujours arrêter les Nodepools après les captures ;
- ne jamais supprimer les volumes automatiquement ;
- ne pas créer de Load Balancer si `kubectl port-forward` suffit ;
- vérifier les quotas et la zone avant toute création ;
- ne pas utiliser le compte lecture seule pour une création.
