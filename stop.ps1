#Requires -Version 5.1
param(
    [switch]$KeepClusters
)
$ErrorActionPreference = 'Stop'
$root = $PSScriptRoot

# Supprimer uniquement la démo ; les PVC et les preuves restent intacts.
$cleanup = Join-Path $root 'stop-failure.ps1'
if (Test-Path -LiteralPath $cleanup) {
    try { & $cleanup } catch {
        Write-Host "Nettoyage de la démo ignoré : $($_.Exception.Message)"
    }
}

# Fermer le tunnel WebUI géré par start.ps1.
$state = Join-Path $root '.cache/two-clusters/webui-forward.json'
if (Test-Path -LiteralPath $state) {
    try {
        $pidValue = (Get-Content -LiteralPath $state -Raw | ConvertFrom-Json).pid
        if ($pidValue) { Stop-Process -Id ([int]$pidValue) -Force -ErrorAction SilentlyContinue }
    } catch {
        Write-Host "Tunnel WebUI déjà arrêté."
    }
    Remove-Item -LiteralPath $state -Force -ErrorAction SilentlyContinue
}

if (-not $KeepClusters) {
    foreach ($profile in @('kubeai-observed', 'kubeai-supervision')) {
        & minikube stop -p $profile 2>$null
    }
}

Write-Host 'KubeAI arrêté. Les PVC, Mimir et Loki sont conservés.'
