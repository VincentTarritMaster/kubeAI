#Requires -Version 5.1
$ErrorActionPreference = 'Stop'
$script:RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$script:ConfigPath = Join-Path $RepoRoot 'infra/exoscale/config.json'
$script:Config = Get-Content -LiteralPath $ConfigPath -Raw | ConvertFrom-Json

function Invoke-ExoJson {
    param([string[]]$Arguments)
    $output = & exo @Arguments -O json 2>$null
    if ($LASTEXITCODE -ne 0) {
        throw "Commande Exoscale en échec : exo $($Arguments -join ' ')"
    }
    if (-not $output) { return $null }
    return ($output | ConvertFrom-Json)
}

function Assert-ExoConfigured {
    $null = & exo config list -O json 2>$null
    if ($LASTEXITCODE -ne 0) {
        throw "Exoscale CLI n'est pas configurée. Exécuter 'exo config add' manuellement."
    }
}

function Get-ClusterPlan {
    foreach ($cluster in $script:Config.clusters) {
        [pscustomobject]@{
            Name = $cluster.name
            Role = $cluster.role
            NodePool = $cluster.nodepool
            NodeType = $cluster.nodeType
            DiskSizeGb = $cluster.diskSizeGb
            HourlyChf = [double]$script:Config.hourlyEstimates.($cluster.nodeType)
        }
    }
}

function Get-ExoClusters {
    return @(Invoke-ExoJson @('compute', 'sks', 'list', '--zone', $script:Config.zone))
}

function Get-ExoCluster {
    param([string]$Name)
    return Invoke-ExoJson @('compute', 'sks', 'show', $Name, '--zone', $script:Config.zone)
}

function Get-ExoSecurityGroups {
    return @(Invoke-ExoJson @('compute', 'security-group', 'list'))
}

function Get-ExoPrivateNetworks {
    return @(Invoke-ExoJson @('compute', 'private-network', 'list', '--zone', $script:Config.zone))
}

function Get-ExoNodePools {
    param([string]$Cluster)
    $pools = @(Invoke-ExoJson @('compute', 'sks', 'nodepool', 'list', '--zone', $script:Config.zone))
    return @($pools | Where-Object {
        $_.cluster -eq $Cluster -or $_.cluster.name -eq $Cluster
    })
}

function Get-ExoNodePool {
    param([string]$Cluster, [string]$NodePool)
    return Invoke-ExoJson @('compute', 'sks', 'nodepool', 'show', $Cluster, $NodePool,
                            '--zone', $script:Config.zone)
}

function Get-NodePoolSize {
    param([string]$Cluster, [string]$NodePool)
    $value = Get-ExoNodePool -Cluster $Cluster -NodePool $NodePool
    if ($null -eq $value) { return 0 }
    return [int]$value.size
}

function Get-EstimatedHourlyCost {
    param([int]$ObservedNodes = 1, [int]$SupervisionNodes = 1)
    $plan = Get-ClusterPlan
    $observed = ($plan | Where-Object Role -eq 'observed').HourlyChf
    $supervision = ($plan | Where-Object Role -eq 'supervision').HourlyChf
    return [math]::Round(($observed * $ObservedNodes) + ($supervision * $SupervisionNodes), 2)
}

function Confirm-PaidOperation {
    param([string]$Message)
    Write-Host $Message
    $answer = Read-Host 'Confirmer cette opération payante ? (oui/non)'
    if ($answer -notin @('oui', 'o', 'yes', 'y')) {
        throw "Opération annulée par l'utilisateur."
    }
}

function Write-Plan {
    param([string]$Title)
    Write-Host ""
    Write-Host "== $Title =="
    Write-Host "Zone : $($script:Config.zone)"
    foreach ($item in (Get-ClusterPlan)) {
        Write-Host ("{0} / {1} / {2} / {3} CHF/h estimé" -f $item.Name, $item.NodePool, $item.NodeType, $item.HourlyChf)
    }
    Write-Host ("Coût estimé avec 1 nœud par cluster : {0} CHF/h" -f (Get-EstimatedHourlyCost))
    Write-Host "Aucune ressource n'est créée par ce script."
}

function Get-KubernetesContexts {
    $output = & kubectl config get-contexts -o name 2>$null
    if ($LASTEXITCODE -ne 0) { return @() }
    return @($output | Where-Object { $_ })
}

function Get-KubernetesPvcSummary {
    param([string]$Context)
    try {
        $rows = & kubectl --context $Context get pvc -A -o json 2>$null | ConvertFrom-Json
        if ($LASTEXITCODE -ne 0 -or $null -eq $rows) { return @() }
        return @($rows.items | ForEach-Object {
            [pscustomobject]@{
                Namespace = $_.metadata.namespace
                Name = $_.metadata.name
                Phase = $_.status.phase
                Capacity = $_.status.capacity.storage
            }
        })
    } catch {
        return @()
    }
}

function Get-ExoResourceSnapshot {
    $snapshot = [ordered]@{
        Clusters = @()
        Instances = @()
        Volumes = @()
        LoadBalancers = @()
        PrivateNetworks = @()
        Limits = $null
    }
    try { $snapshot.Clusters = @(Get-ExoClusters) } catch { }
    try { $snapshot.Instances = @(Invoke-ExoJson @('compute', 'instance', 'list', '--zone', $script:Config.zone)) } catch { }
    try { $snapshot.Volumes = @(Invoke-ExoJson @('compute', 'block-storage', 'list', '--zone', $script:Config.zone)) } catch { }
    try { $snapshot.LoadBalancers = @(Invoke-ExoJson @('compute', 'load-balancer', 'list', '--zone', $script:Config.zone)) } catch { }
    try { $snapshot.PrivateNetworks = @(Invoke-ExoJson @('compute', 'private-network', 'list', '--zone', $script:Config.zone)) } catch { }
    try { $snapshot.Limits = Invoke-ExoJson @('limits') } catch { }
    return [pscustomobject]$snapshot
}
