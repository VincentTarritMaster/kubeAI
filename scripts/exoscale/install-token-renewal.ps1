#Requires -Version 5.1
[CmdletBinding()]
param(
    [switch]$Apply,
    [switch]$DryRun,
    [int]$IntervalHours = 12,
    [string]$TaskName = 'KubeAI-Refresh-Observed-Token'
)
. (Join-Path $PSScriptRoot 'common.ps1')

$target = Join-Path $PSScriptRoot 'refresh-observed-token.ps1'
Write-Host "Tâche : $TaskName"
Write-Host "Script : $target"
Write-Host "Intervalle : $IntervalHours heures"
Write-Host "Exécution sous l'utilisateur courant, sans secret dans la tâche."
if (-not $Apply -or $DryRun) {
    Write-Host 'Simulation : aucune tâche planifiée créée.'
    exit 0
}

$arguments = "-NoProfile -ExecutionPolicy Bypass -File `"$target`" -Apply"
$action = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument $arguments
$trigger = New-ScheduledTaskTrigger -Once -At ((Get-Date).AddMinutes(5)) `
    -RepetitionInterval (New-TimeSpan -Hours $IntervalHours) `
    -RepetitionDuration (New-TimeSpan -Days 3650)
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew
Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger `
    -Settings $settings -Description 'Renouvelle le jeton kubeai-agent observé pour KubeAI' -Force | Out-Null
Write-Host 'Tâche planifiée installée. Vérifier avec : Get-ScheduledTask -TaskName $TaskName'
Write-Host 'Le kubeconfig admin doit rester hors du dépôt et accessible à cet utilisateur.'
