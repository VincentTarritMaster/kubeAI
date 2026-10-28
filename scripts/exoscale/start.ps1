#Requires -Version 5.1
[CmdletBinding()]
param(
    [switch]$Apply,
    [switch]$DryRun,
    [switch]$Yes,
    [switch]$Deploy,
    [switch]$SkipTokenRefresh
)
. (Join-Path $PSScriptRoot 'common.ps1')

if ($Apply -and $DryRun) { throw 'Choisir -Apply ou -DryRun, pas les deux.' }
if (-not $Apply -or $DryRun) {
    Write-Plan -Title 'KubeAI Exoscale — plan de démarrage'
    Write-Host 'Nodepool absent : création à 1 nœud. Nodepool à 0 : scale à 1.'
    Write-Host 'Cette opération crée des machines facturables ; aucun appel Cloud en DryRun.'
    Write-Host 'Relancer avec -Apply après autorisation explicite.'
    exit 0
}

Assert-ExoConfigured
$plan = @(Get-ClusterPlan)
$clusters = @(Get-ExoClusters)
$missing = @($plan | Where-Object { $_.Name -notin $clusters.name })
if ($missing.Count) {
    throw ("Clusters SKS absents : " + (($missing | ForEach-Object Name) -join ', ') +
           ". Le bootstrap doit être exécuté avant le démarrage.")
}

$contexts = @(Get-KubernetesContexts)
foreach ($item in $plan) {
    if ($item.Name -notin $contexts) {
        throw "Contexte kubectl absent : $($item.Name). Aucun Nodepool n'a été créé."
    }
}

Write-Plan -Title 'KubeAI Exoscale — démarrage demandé'
Write-Host ("Coût estimé après démarrage : {0} CHF/h" -f (Get-EstimatedHourlyCost))
Write-Host 'Les tarifs sont des estimations à vérifier dans la console Exoscale.'
if (-not $Yes) { Confirm-PaidOperation "Le démarrage de deux nœuds SKS est facturé à l'heure." }

foreach ($item in $plan) {
    $pools = @(Get-ExoNodePools -Cluster $item.Name)
    $pool = $pools | Where-Object { $_.name -eq $item.NodePool } | Select-Object -First 1
    if ($null -eq $pool) {
        Write-Host "Création du Nodepool $($item.Name)/$($item.NodePool) à 1 nœud..."
        & exo compute sks nodepool add $item.Name $item.NodePool `
            --zone $script:Config.zone --size 1 --instance-type $item.NodeType `
            --disk-size ([string]$item.DiskSizeGb) --private-network $script:Config.privateNetwork `
            --security-group $script:Config.securityGroup --label "kubeai.io/role=$($item.Role)"
        if ($LASTEXITCODE -ne 0) { throw "Création du Nodepool impossible pour $($item.Name)/$($item.NodePool)." }
    } elseif ([int]$pool.size -eq 0) {
        Write-Host "Scale up $($item.Name)/$($item.NodePool) : 0 -> 1"
        & exo compute sks nodepool scale $item.Name $item.NodePool 1 -f --zone $script:Config.zone
        if ($LASTEXITCODE -ne 0) { throw "Scale up impossible pour $($item.Name)/$($item.NodePool)." }
    } elseif ([int]$pool.size -eq 1) {
        Write-Host "$($item.Name)/$($item.NodePool) déjà à 1 nœud."
    } else {
        throw "Nodepool $($item.Name)/$($item.NodePool) à une taille inattendue ($($pool.size)) ; arrêt."
    }
}

$deadline = (Get-Date).AddMinutes(10)
do {
    $ready = $true
    foreach ($item in $plan) {
        try {
            $size = Get-NodePoolSize -Cluster $item.Name -NodePool $item.NodePool
        } catch {
            $size = 0
        }
        if ($size -lt 1) { $ready = $false }
    }
    if (-not $ready) { Start-Sleep -Seconds 15 }
} while (-not $ready -and (Get-Date) -lt $deadline)
if (-not $ready) { throw 'Les Nodepools ne sont pas encore à 1 après 10 minutes.' }

foreach ($item in $plan) {
    Write-Host "Attente des nœuds Ready pour $($item.Name)..."
    & kubectl --context $item.Name wait --for=condition=Ready nodes --all --timeout=600s
    if ($LASTEXITCODE -ne 0) { throw "Nœuds non Ready pour $($item.Name)." }
}

if (-not $SkipTokenRefresh) {
    try {
        Write-Host 'Réémission du jeton observé (lecture seule, 24 h)...'
        & (Join-Path $PSScriptRoot 'refresh-observed-token.ps1') -Apply
        if ($LASTEXITCODE -ne 0) { throw 'Le script de renouvellement a échoué.' }
    } catch {
        Write-Warning "Jeton observé non renouvelé : $($_.Exception.Message)"
    }
}

$supervisionIp = (& kubectl --context 'kubeai-supervision' get nodes -o jsonpath='{.items[0].status.addresses[?(@.type=="InternalIP")].address}' 2>$null).Trim()
if ($LASTEXITCODE -eq 0 -and $supervisionIp) {
    $exists = & kubectl --context 'kubeai-observed' -n collecte get configmap kubeai-collector-targets -o name 2>$null
    if ($LASTEXITCODE -eq 0 -and $exists) {
        $patch = @{ data = @{
            MIMIR_WRITE_URL = "https://${supervisionIp}:30009/api/v1/push"
            LOKI_WRITE_URL = "https://${supervisionIp}:30310/loki/api/v1/push"
        }} | ConvertTo-Json -Depth 5 -Compress
        $cache = Join-Path $RepoRoot '.cache/exoscale'
        New-Item -ItemType Directory -Path $cache -Force | Out-Null
        $patchFile = Join-Path $cache 'collector-targets.json'
        [IO.File]::WriteAllText($patchFile, $patch, (New-Object Text.UTF8Encoding($false)))
        try {
            & kubectl --context 'kubeai-observed' -n collecte patch configmap kubeai-collector-targets --type merge --patch-file $patchFile
            if ($LASTEXITCODE -ne 0) {
                Write-Warning 'Mise à jour Alloy impossible ; vérifier manuellement les URLs.'
            } else {
                & kubectl --context 'kubeai-observed' -n collecte rollout restart deployment/alloy
                if ($LASTEXITCODE -ne 0) { Write-Warning 'Redémarrage Alloy impossible ; les URLs ne seront pas rechargées.' }
            }
        } finally {
            Remove-Item -LiteralPath $patchFile -Force -ErrorAction SilentlyContinue
        }
    }
} else {
    Write-Warning 'Adresse privée de supervision introuvable ; Alloy devra être configuré manuellement.'
}

foreach ($item in $plan) {
    $pvc = @(Get-KubernetesPvcSummary -Context $item.Name)
    if ($pvc.Count) {
        Write-Host "PVC présents dans $($item.Name) :"
        $pvc | Format-Table -AutoSize
    } else {
        Write-Host "Aucun PVC visible dans $($item.Name) (ou API indisponible)."
    }
}

if ($Deploy) {
    Write-Host 'Déploiement des overlays Cloud...'
    $observed = kubectl kustomize --load-restrictor=LoadRestrictionsNone (Join-Path $RepoRoot 'k8s/overlays/exoscale/observed')
    if ($LASTEXITCODE -ne 0) { throw 'Rendu Kustomize observé invalide.' }
    $observed | kubectl --context 'kubeai-observed' apply -f -
    if ($LASTEXITCODE -ne 0) { throw 'Déploiement observé impossible.' }
    $supervision = kubectl kustomize --load-restrictor=LoadRestrictionsNone (Join-Path $RepoRoot 'k8s/overlays/exoscale/supervision')
    if ($LASTEXITCODE -ne 0) { throw 'Rendu Kustomize supervision invalide.' }
    $supervision | kubectl --context 'kubeai-supervision' apply -f -
    if ($LASTEXITCODE -ne 0) { throw 'Déploiement supervision impossible.' }
}

Write-Host ''
Write-Host 'KubeAI Exoscale prêt.'
Write-Host "Open WebUI (port-forward local, pas d'exposition publique) :"
Write-Host '  kubectl --context kubeai-supervision -n kubeai port-forward svc/open-webui 8080:80'
Write-Host 'Puis ouvrir http://127.0.0.1:8080'
