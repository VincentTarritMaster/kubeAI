#Requires -Version 5.1
[CmdletBinding()]
param(
    [switch]$Apply,
    [switch]$DryRun,
    [switch]$Yes
)
. (Join-Path $PSScriptRoot 'common.ps1')

if ($Apply -and $DryRun) { throw 'Choisir -Apply ou -DryRun, pas les deux.' }
if (-not $Apply -or $DryRun) {
    Write-Plan -Title "KubeAI Exoscale — plan d'arrêt"
    Write-Host 'Aucune action. Relancer avec -Apply pour réduire les Nodepools à 0.'
    exit 0
}

Assert-ExoConfigured
$plan = @(Get-ClusterPlan)

Write-Host 'Vérification des données persistantes avant arrêt...'
foreach ($item in $plan) {
    $pvc = @(Get-KubernetesPvcSummary -Context $item.Name)
    if ($pvc.Count) {
        Write-Host "PVC conservés dans $($item.Name) :"
        $pvc | Format-Table -AutoSize
    } else {
        Write-Host "Aucun PVC lisible dans $($item.Name) ; l'arrêt continue sans suppression."
    }
}
Write-Host 'Aucun PVC, volume ou Secret ne sera supprimé.'

if (-not $Yes) {
    Confirm-PaidOperation 'Réduire les deux Nodepools à 0 arrête les instances ; les volumes restent facturables.'
}

$stopFailures = @()
foreach ($item in $plan) {
    $pools = @(Get-ExoNodePools -Cluster $item.Name)
    $pool = $pools | Where-Object { $_.name -eq $item.NodePool } | Select-Object -First 1
    if ($null -eq $pool) {
        Write-Host "$($item.Name)/$($item.NodePool) absent : aucun Nodepool à arrêter."
        continue
    }
    $current = [int]$pool.size
    if ($current -gt 0) {
        Write-Host "Scale down $($item.Name)/$($item.NodePool) : $current -> 0"
        & exo compute sks nodepool scale $item.Name $item.NodePool 0 -f --zone $script:Config.zone
        if ($LASTEXITCODE -ne 0) {
            $stopFailures += "Scale down refusé pour $($item.Name)/$($item.NodePool)."
            Write-Warning $stopFailures[-1]
        }
    } else {
        Write-Host "$($item.Name)/$($item.NodePool) déjà à 0."
    }
}

$deadline = (Get-Date).AddMinutes(10)
do {
    $stopped = $true
    foreach ($item in $plan) {
        $pools = @(Get-ExoNodePools -Cluster $item.Name)
        $pool = $pools | Where-Object { $_.name -eq $item.NodePool } | Select-Object -First 1
        if ($null -ne $pool -and [int]$pool.size -gt 0) { $stopped = $false }
    }
    if (-not $stopped) { Start-Sleep -Seconds 15 }
} while (-not $stopped -and (Get-Date) -lt $deadline)
if (-not $stopped) {
    $stopFailures += 'Les Nodepools ne sont pas encore à 0 après 10 minutes.'
    Write-Warning $stopFailures[-1]
}

$snapshot = Get-ExoResourceSnapshot
$instanceCount = @($snapshot.Instances).Count
Write-Host ''
Write-Host 'Ressources encore présentes (facturation possible) :'
Write-Host ("Clusters SKS : {0}" -f @($snapshot.Clusters).Count)
Write-Host ("Instances Compute : {0}" -f $instanceCount)
Write-Host ("Volumes SBS : {0}" -f @($snapshot.Volumes).Count)
Write-Host ("Load Balancers : {0}" -f @($snapshot.LoadBalancers).Count)
Write-Host ("Réseaux privés : {0}" -f @($snapshot.PrivateNetworks).Count)
Write-Host 'Les volumes SBS, PVC et clusters SKS ne sont pas supprimés automatiquement.'

if ($stopFailures.Count -gt 0 -or $instanceCount -gt 0) {
    Write-Warning 'CRITIQUE : des instances ou Nodepools restent actifs et peuvent être facturés.'
    Write-Host 'Procédure de secours manuelle : voir infra/exoscale/iam/manual-cleanup.md'
    exit 1
}

Write-Host 'Arrêt vérifié : 0 Nodepool actif, 0 instance Compute.'
Write-Host 'Vérifier la console Exoscale avant de supprimer les données.'
