#Requires -Version 5.1
param(
    [string]$Supervision = '',
    [string]$Observed = '',
    [switch]$NoForward
)
$ErrorActionPreference = 'Stop'
$pythonExe = if (Get-Command py -ErrorAction SilentlyContinue) { 'py' } else { 'python' }
$pythonArgs = @()
    if ($pythonExe -eq 'py') { $pythonArgs = @('-3') }
$arguments = @((Join-Path $PSScriptRoot 'scripts/start-two-clusters.py'))
if ($Supervision) { $arguments += @('--supervision', $Supervision) }
if ($Observed) { $arguments += @('--observed', $Observed) }
if ($NoForward) { $arguments += '--no-forward' }
& $pythonExe @pythonArgs @arguments
exit $LASTEXITCODE
