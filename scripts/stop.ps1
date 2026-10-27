#Requires -Version 5.1
param([switch]$KeepClusters)
& (Join-Path $PSScriptRoot '../stop.ps1') -KeepClusters:$KeepClusters
exit $LASTEXITCODE
