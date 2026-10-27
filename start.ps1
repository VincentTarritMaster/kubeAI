#Requires -Version 5.1
param(
    [string]$Supervision = '',
    [string]$Observed = '',
    [switch]$NoForward
)
$ErrorActionPreference = 'Stop'
$python = if (Get-Command python -ErrorAction SilentlyContinue) {
    'python'
} elseif (Get-Command python3 -ErrorAction SilentlyContinue) {
    'python3'
} else {
    throw 'Python est introuvable dans le PATH. Installez Python 3.12 ou ajoutez-le au PATH.'
}
$arguments = @((Join-Path $PSScriptRoot 'scripts/start-two-clusters.py'))
if ($Supervision) { $arguments += @('--supervision', $Supervision) }
if ($Observed) { $arguments += @('--observed', $Observed) }
if ($NoForward) { $arguments += '--no-forward' }
& $python @arguments
exit $LASTEXITCODE
