#Requires -Version 5.1
param(
    [string]$Supervision = '',
    [string]$Observed = '',
    [switch]$NoForward
)
$ErrorActionPreference = 'Stop'
$python = if (Get-Command python3 -ErrorAction SilentlyContinue) { 'python3' } else { 'python' }
$arguments = @((Join-Path $PSScriptRoot 'scripts/start-two-clusters.py'))
if ($Supervision) { $arguments += @('--supervision', $Supervision) }
if ($Observed) { $arguments += @('--observed', $Observed) }
if ($NoForward) { $arguments += '--no-forward' }
& $python @arguments
exit $LASTEXITCODE
