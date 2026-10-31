#Requires -Version 5.1
[CmdletBinding()]
param([switch]$DryRun)
. (Join-Path $PSScriptRoot 'common.ps1')

Assert-ExoConfigured
$plan = @(Get-ClusterPlan)
$clusters = @(Get-ExoClusters)
$snapshot = Get-ExoResourceSnapshot
$observedNodes = 0
$supervisionNodes = 0
$nodeStates = @()
$missing = @($plan | Where-Object { $_.Name -notin $clusters.name })

foreach ($item in $plan) {
    $exists = $item.Name -in $clusters.name
    $poolExists = $false
    $size = 0
    if ($exists) {
        try {
            $pools = @(Get-ExoNodePools -Cluster $item.Name)
            $poolExists = $item.NodePool -in $pools.name
            if ($poolExists) { $size = Get-NodePoolSize -Cluster $item.Name -NodePool $item.NodePool }
        } catch {
            $size = 0
        }
    }
    if ($item.Role -eq 'observed') { $observedNodes = $size } else { $supervisionNodes = $size }
    $nodeStates += [pscustomobject]@{
        Cluster = $item.Name
        NodePool = $item.NodePool
        Exists = $exists
        NodePoolExists = $poolExists
        Nodes = $size
        NodeType = $item.NodeType
    }
}

Write-Host ''
Write-Host '== KubeAI Exoscale — status =='
Write-Host "Zone : $($script:Config.zone)"
$account = if ($env:EXOSCALE_ACCOUNT) { $env:EXOSCALE_ACCOUNT } else { 'compte par défaut' }
Write-Host "Compte : $account"
Write-Host ''
Write-Host 'Clusters et Nodepools :'
$nodeStates | Format-Table -AutoSize
if ($missing.Count) {
    Write-Warning ("Clusters SKS absents : " + (($missing | ForEach-Object Name) -join ', '))
    Write-Host 'Aucun cluster ne sera créé automatiquement ; voir infra/exoscale/README.md.'
}
Write-Host ("Instances Compute : {0}" -f $snapshot.InstanceCount)
Write-Host ("Volumes SBS : {0}" -f @($snapshot.Volumes).Count)
Write-Host ("Load Balancers : {0}" -f @($snapshot.LoadBalancers).Count)
Write-Host ("Réseaux privés : {0}" -f @($snapshot.PrivateNetworks).Count)
Write-Host ''
Write-Host ("Coût nœuds estimé : {0} CHF/h" -f (Get-EstimatedHourlyCost -ObservedNodes $observedNodes -SupervisionNodes $supervisionNodes))
Write-Host 'Tarifs estimatifs à vérifier dans la console Exoscale.'
Write-Host ''
if (@($snapshot.Volumes).Count -gt 0 -or @($snapshot.LoadBalancers).Count -gt 0 -or @($snapshot.Clusters).Count -gt 0) {
    Write-Warning 'Des ressources facturables existent encore : clusters SKS, volumes SBS et/ou Load Balancers.'
    Write-Host 'Arrêt recommandé après les démonstrations :'
    Write-Host '  .\scripts\exoscale\stop.ps1 -Apply'
}
Write-Host "Aucune ressource n'a été créée ou modifiée par ce status."
