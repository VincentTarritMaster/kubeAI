#Requires -Version 5.1
param(
    [string]$Supervision = '',
    [string]$Observed = '',
    [switch]$NoForward
)
& (Join-Path $PSScriptRoot '../start.ps1') -Supervision $Supervision -Observed $Observed -NoForward:$NoForward
exit $LASTEXITCODE
