#Requires -Version 5.1
param()
. (Join-Path $PSScriptRoot 'common.ps1')
try {
    foreach ($name in @('webui','fastapi','mimir','loki')) { Stop-TrackedProcess $name }
    if (Get-Command kubectl -ErrorAction SilentlyContinue) {
        $deployment = Invoke-KubectlResult -Arguments @('-n','kubeai','get','deployment','open-webui','-o','name')
        if ($deployment.ExitCode -eq 0 -and $deployment.StdOut.Count) {
            Invoke-Kubectl -Arguments @('-n','kubeai','scale','deployment/open-webui','--replicas=0') | Out-Null
        }
    }
    Write-Host 'KubeAI arrêté. Kubernetes et les données de monitoring sont conservés.'
} catch { [Console]::Error.WriteLine("Arrêt KubeAI : $($_.Exception.Message)"); exit 1 }
