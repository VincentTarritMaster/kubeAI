#Requires -Version 5.1
param([string]$Context = '', [string]$Namespace = '')
if (-not $Context) {
    $pythonExe = if (Get-Command py -ErrorAction SilentlyContinue) { 'py' } else { 'python' }
    $pythonArgs = @()
    if ($pythonExe -eq 'py') { $pythonArgs = @('-3') }
    $Context = & $pythonExe @pythonArgs (Join-Path $PSScriptRoot 'cluster-context.py') observed
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}
if (-not $Namespace) {
    $pythonExe = if (Get-Command py -ErrorAction SilentlyContinue) { 'py' } else { 'python' }
    $pythonArgs = @()
    if ($pythonExe -eq 'py') { $pythonArgs = @('-3') }
    $Namespace = & $pythonExe @pythonArgs (Join-Path $PSScriptRoot 'cluster-context.py') namespace
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}
$demoContext = $Context
. (Join-Path $PSScriptRoot 'common.ps1')
$script:Context = $demoContext

try {
    $existing = Invoke-KubectlResult -Arguments @('-n',$Namespace,'get','pod','kubeai-demo-incident','-o','json')
    if ($existing.ExitCode -eq 0) {
        $pod = ($existing.StdOut -join "`n") | ConvertFrom-Json
        if ($pod.status.phase -eq 'Running') {
            throw 'Le scénario kubeai-demo-incident est déjà en cours ; suivez ses logs avant de le relancer.'
        }
        if ($pod.status.phase -notin @('Succeeded','Failed')) {
            throw "Le pod de démonstration est dans l'état inattendu : $($pod.status.phase)."
        }
        # Seul le pod de démonstration est remplacé ; les PVC de monitoring
        # conservent les observations déjà ingérées.
        Invoke-Kubectl -Arguments @('-n',$Namespace,'delete','pod','kubeai-demo-incident','--wait=true') | Out-Null
    } elseif ($existing.ExitCode -ne 1) {
        $detail = if ($existing.StdErr.Count) { $existing.StdErr -join ' ' } else { "code $($existing.ExitCode)" }
        throw "Impossible de vérifier le pod de démonstration : $detail"
    }

    Invoke-Kubectl -Arguments @('-n',$Namespace,'apply','-k',(Join-Path $script:ProjectRoot 'k8s\demo\incident')) | Out-Null
    Invoke-Kubectl -Arguments @('-n',$Namespace,'wait','--for=condition=Ready','pod/kubeai-demo-incident','--timeout=180s') | Out-Null
    Write-Host 'Scénario en cours ; les marqueurs ci-dessous sont horodatés en UTC.'
    $logs = Invoke-Kubectl -Arguments @('--request-timeout=0','-n',$Namespace,'logs','--follow','pod/kubeai-demo-incident')
    $logs | ForEach-Object { Write-Host $_ }
    if (-not ($logs -match 'phase=complete')) {
        throw "Le scénario ne s’est pas terminé normalement ; aucun succès de démonstration n’est annoncé."
    }
    Write-Host 'Scénario terminé. Utilisez ses horodatages UTC réels pour les questions B et C.'
    Write-Host 'Les preuves sont conservées dans les PVC de Loki et Mimir selon leur rétention.'
} catch {
    [Console]::Error.WriteLine("Scénario KubeAI : $($_.Exception.Message)")
    exit 1
}
