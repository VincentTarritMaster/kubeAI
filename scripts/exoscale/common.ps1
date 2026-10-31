#Requires -Version 5.1
$ErrorActionPreference = 'Stop'
$script:RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$script:ConfigPath = Join-Path $RepoRoot 'infra/exoscale/config.json'
$script:Config = Get-Content -LiteralPath $ConfigPath -Raw | ConvertFrom-Json

# Profil CLI de déploiement (lecture, création/scale des Nodepools et
# génération des kubeconfigs). Sans ce profil, la CLI utilise le compte par
# défaut en lecture seule et `exo compute sks kubeconfig` est refusé par la
# politique IAM. Un EXOSCALE_ACCOUNT déjà défini reste prioritaire.
if (-not $env:EXOSCALE_ACCOUNT) { $env:EXOSCALE_ACCOUNT = 'kubeai-deployer' }

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
    # `nodepool show` peut être refusé par la politique du profil de
    # déploiement ; `nodepool list` renvoie déjà la taille et reste autorisé.
    $pools = @(Get-ExoNodePools -Cluster $Cluster)
    $pool = $pools | Where-Object { $_.name -eq $NodePool } | Select-Object -First 1
    if ($null -eq $pool) { return 0 }
    return [int]$pool.size
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
        InstanceCount = 0
        Volumes = @()
        LoadBalancers = @()
        PrivateNetworks = @()
        Limits = $null
    }
    try { $snapshot.Clusters = @(Get-ExoClusters) } catch { }
    try {
        $snapshot.Instances = @(Invoke-ExoJson @('compute', 'instance', 'list', '--zone', $script:Config.zone))
        $snapshot.InstanceCount = @($snapshot.Instances).Count
    } catch { }
    try { $snapshot.Volumes = @(Invoke-ExoJson @('compute', 'block-storage', 'list', '--zone', $script:Config.zone)) } catch { }
    try { $snapshot.LoadBalancers = @(Invoke-ExoJson @('compute', 'load-balancer', 'list', '--zone', $script:Config.zone)) } catch { }
    try { $snapshot.PrivateNetworks = @(Invoke-ExoJson @('compute', 'private-network', 'list', '--zone', $script:Config.zone)) } catch { }
    try { $snapshot.Limits = Invoke-ExoJson @('limits') } catch { }
    # Le profil de déploiement peut lister les quotas sans pouvoir enrichir
    # chaque instance avec son type ; `limits` reste la source fiable du
    # nombre d'instances facturées.
    if ($snapshot.InstanceCount -eq 0 -and $null -ne $snapshot.Limits) {
        $instanceQuota = @($snapshot.Limits) |
            Where-Object { $_.resource -eq 'Compute instances' } |
            Select-Object -First 1
        if ($null -ne $instanceQuota) { $snapshot.InstanceCount = [int]$instanceQuota.used }
    }
    return [pscustomobject]$snapshot
}
