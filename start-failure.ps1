#Requires -Version 5.1
param(
    [string]$Context = '',
    [ValidateSet('ps1', 'sh')][string]$Shell = 'ps1'
)

$ErrorActionPreference = 'Stop'
if (-not $Context) {
    $pythonExe = if (Get-Command py -ErrorAction SilentlyContinue) { 'py' } else { 'python' }
    $pythonArgs = @()
    if ($pythonExe -eq 'py') { $pythonArgs = @('-3') }
    $Context = & $pythonExe @pythonArgs (Join-Path $PSScriptRoot 'scripts/cluster-context.py') observed
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}
if ($Shell -eq 'sh') {
    if (-not (Get-Command bash -ErrorAction SilentlyContinue)) {
        throw 'Bash est introuvable. Utilisez .\start-failure.ps1 pour le lanceur PowerShell.'
    }
    & bash (Join-Path $PSScriptRoot 'scripts/run-demo-incident.sh') $Context
    exit $LASTEXITCODE
}

& (Join-Path $PSScriptRoot 'scripts/run-demo-incident.ps1') -Context $Context
if (-not $?) { exit 1 }
