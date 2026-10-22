#Requires -Version 5.1
param()
. (Join-Path $PSScriptRoot 'common.ps1')

try {
    $existing = Invoke-KubectlResult -Arguments @('-n','kubeai','get','pod','kubeai-demo-incident','-o','json')
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
        Invoke-Kubectl -Arguments @('-n','kubeai','delete','pod','kubeai-demo-incident','--wait=true') | Out-Null
    } elseif ($existing.ExitCode -ne 1) {
        $detail = if ($existing.StdErr.Count) { $existing.StdErr -join ' ' } else { "code $($existing.ExitCode)" }
        throw "Impossible de vérifier le pod de démonstration : $detail"
    }

    Invoke-Kubectl -Arguments @('apply','-k',(Join-Path $script:ProjectRoot 'k8s\demo\incident')) | Out-Null
    Invoke-Kubectl -Arguments @('-n','kubeai','wait','--for=condition=Ready','pod/kubeai-demo-incident','--timeout=180s') | Out-Null
    Write-Host 'Scénario en cours ; les marqueurs ci-dessous sont horodatés en UTC.'
    $logs = Invoke-Kubectl -Arguments @('-n','kubeai','logs','--follow','pod/kubeai-demo-incident')
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
