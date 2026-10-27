#Requires -Version 5.1
param([string]$Context = '', [string]$Namespace = '')
$ErrorActionPreference = 'Stop'
if (-not $Context) {
    $pythonExe = if (Get-Command py -ErrorAction SilentlyContinue) { 'py' } else { 'python' }
    $pythonArgs = @()
    if ($pythonExe -eq 'py') { $pythonArgs = @('-3') }
    $Context = & $pythonExe @pythonArgs (Join-Path $PSScriptRoot 'scripts/cluster-context.py') observed
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}
if (-not $Namespace) {
    $pythonExe = if (Get-Command py -ErrorAction SilentlyContinue) { 'py' } else { 'python' }
    $pythonArgs = @()
    if ($pythonExe -eq 'py') { $pythonArgs = @('-3') }
    $Namespace = & $pythonExe @pythonArgs (Join-Path $PSScriptRoot 'scripts/cluster-context.py') namespace
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}
foreach ($item in @(@('kubeai-demo-incident', 'incident'),
                    @('kubeai-demo-failure', 'manual-failure'),
                    @('kubeai-demo-correlation', 'correlation'))) {
    & kubectl --context $Context --request-timeout=30s -n $Namespace delete pods `
        "--field-selector=metadata.name=$($item[0])" "--selector=kubeai-demo=$($item[1])" `
        --ignore-not-found --wait=true --timeout=60s
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}
& kubectl --context $Context --request-timeout=30s -n $Namespace delete configmaps `
    --field-selector=metadata.name=kubeai-demo-incident-app --selector=kubeai-demo=incident `
    --ignore-not-found --wait=true --timeout=60s
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
Write-Host 'Cluster observé nettoyé. La supervision et les preuves collectées restent disponibles.'
