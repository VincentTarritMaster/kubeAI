#Requires -Version 5.1
[CmdletBinding()]
param(
    [string]$Registry = 'ghcr.io/CHANGE_ME/kubeai-agent',
    [string]$Tag = '0.1.0',
    [string]$SmokeKubeConfig = '',
    [switch]$Push,
    [switch]$BuildOnly,
    [switch]$DryRun
)
. (Join-Path $PSScriptRoot 'common.ps1')
$image = "${Registry}:$Tag"
$dockerfile = Join-Path $RepoRoot 'agent/Dockerfile'

Write-Host "Image : $image"
Write-Host "Dockerfile : $dockerfile"
if (-not (Test-Path -LiteralPath $dockerfile)) { throw 'agent/Dockerfile introuvable.' }
if ($DryRun -or (-not $Push -and -not $BuildOnly)) {
    Write-Host "Commande de construction :"
    Write-Host "docker build --pull -t $image $RepoRoot/agent"
    Write-Host "Publication non effectuée. Utiliser -Push après autorisation explicite."
    exit 0
}

docker build --pull -t $image (Join-Path $RepoRoot 'agent')
if ($LASTEXITCODE -ne 0) { throw 'Build Docker impossible.' }
if ($SmokeKubeConfig) {
    Write-Host 'Test local /health avec kubeconfig monté :'
    $container = docker run --rm -d -p 18000:8000 `
        -e KUBERNETES_ACCESS_MODE=kubeconfig `
        -e KUBERNETES_KUBECONFIG_FILE=/tmp/kubeconfig `
        -v "${SmokeKubeConfig}:/tmp/kubeconfig:ro" $image
    if ($LASTEXITCODE -ne 0) { throw 'Démarrage du conteneur de test impossible.' }
    try {
        Start-Sleep -Seconds 3
        $response = Invoke-WebRequest 'http://127.0.0.1:18000/health' -UseBasicParsing -TimeoutSec 10
        if ($response.StatusCode -ne 200) { throw 'Le conteneur de test ne répond pas sur /health.' }
        Write-Host 'Test /health : OK'
    } finally {
        docker stop $container | Out-Null
    }
} else {
    Write-Host 'Test local ignoré : fournir -SmokeKubeConfig pour démarrer le conteneur.'
}
if ($BuildOnly) {
    Write-Host 'Image construite localement ; publication non effectuée (-BuildOnly).'
    exit 0
}
docker push $image
if ($LASTEXITCODE -ne 0) { throw 'Publication OCI impossible.' }
$digest = docker inspect --format '{{index .RepoDigests 0}}' $image
Write-Host "Image publiée : $image"
Write-Host "Référence immuable : $digest"
Write-Host "Utiliser cette référence par digest dans l'overlay supervision."
