#Requires -Version 5.1
[CmdletBinding()]
param(
    [string]$ObservedContext = 'kubeai-observed',
    [string]$SupervisionContext = 'kubeai-supervision',
    [string]$Duration = '24h',
    [switch]$Apply,
    [switch]$DryRun
)
. (Join-Path $PSScriptRoot 'common.ps1')

if ($Apply -and $DryRun) { throw "Choisir -Apply ou -DryRun, pas les deux." }
Write-Host "Renouvellement du jeton observé : $ObservedContext -> $SupervisionContext"
Write-Host "Durée : $Duration. Aucun droit administrateur permanent dans l'agent."
if (-not $Apply -or $DryRun) {
    Write-Host "Simulation : aucune mise à jour du Secret."
    exit 0
}

$source = & kubectl --context $ObservedContext config view --minify --flatten --raw -o json 2>$null
if ($LASTEXITCODE -ne 0 -or -not $source) { throw "Kubeconfig observé introuvable pour $ObservedContext." }
$config = $source | ConvertFrom-Json
if (-not $config.clusters -or -not $config.clusters[0].cluster.server) {
    throw "Kubeconfig observé incomplet."
}
$token = & kubectl --context $ObservedContext -n kubeai create token kubeai-agent --duration=$Duration 2>$null
if ($LASTEXITCODE -ne 0 -or -not $token) { throw "Création du jeton impossible." }
$cluster = $config.clusters[0].cluster
$watched = [ordered]@{
    apiVersion = 'v1'
    kind = 'Config'
    'current-context' = 'kubeai-observed'
    clusters = @(@{ name = 'kubeai-observed'; cluster = @{
        server = $cluster.server
        'certificate-authority-data' = $cluster.'certificate-authority-data'
    }})
    contexts = @(@{ name = 'kubeai-observed'; context = @{
        cluster = 'kubeai-observed'; user = 'kubeai-agent'; namespace = 'kubeai'
    }})
    users = @(@{ name = 'kubeai-agent'; user = @{ token = $token.Trim() } })
}
$secret = [ordered]@{
    apiVersion = 'v1'
    kind = 'Secret'
    metadata = @{ name = 'kubeai-observed-kubeconfig'; namespace = 'kubeai' }
    type = 'Opaque'
    stringData = @{ kubeconfig = ($watched | ConvertTo-Json -Depth 20 -Compress) }
}
$json = $secret | ConvertTo-Json -Depth 20 -Compress
$json | kubectl --context $SupervisionContext apply -f -
if ($LASTEXITCODE -ne 0) { throw "Mise à jour du Secret impossible." }
Write-Host "Secret kubeai-observed-kubeconfig mis à jour."
# Le kubeconfig est lu au démarrage de l'agent : sans redémarrage, le jeton
# précédent resterait chargé jusqu'à son expiration.
Write-Host "Redémarrage de l'agent pour charger le nouveau kubeconfig..."
& kubectl --context $SupervisionContext -n kubeai rollout restart deployment/kubeai-agent
if ($LASTEXITCODE -ne 0) { throw "Redémarrage de l'agent impossible." }
& kubectl --context $SupervisionContext -n kubeai rollout status deployment/kubeai-agent --timeout=180s
if ($LASTEXITCODE -ne 0) { throw "L'agent n'est pas redevenu prêt après le renouvellement." }
