#Requires -Version 5.1
param()
. (Join-Path $PSScriptRoot 'common.ps1')

function Wait-DockerDesktop {
    $previousPreference = $ErrorActionPreference
    try {
        $ErrorActionPreference = 'Continue'
        & docker info --format '{{.ServerVersion}}' *> $null
        if ($LASTEXITCODE -eq 0) { return }
    } finally { $ErrorActionPreference = $previousPreference }
    if (-not (Get-Process -Name 'Docker Desktop' -ErrorAction SilentlyContinue)) {
        $executables = @(
            (Join-Path $env:ProgramFiles 'Docker\Docker\Docker Desktop.exe'),
            (Join-Path $env:LOCALAPPDATA 'Programs\Docker\Docker Desktop.exe')
        )
        $desktop = $executables | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
        if (-not $desktop) { throw 'Docker Desktop est introuvable. Installez-le avant de démarrer KubeAI.' }
        Start-Process -FilePath $desktop -WindowStyle Hidden | Out-Null
        Write-Host 'Démarrage de Docker Desktop en cours…'
    }
    $deadline = (Get-Date).AddMinutes(5)
    do {
        Start-Sleep -Seconds 3
        $previousPreference = $ErrorActionPreference
        try {
            $ErrorActionPreference = 'Continue'
            & docker info --format '{{.ServerVersion}}' *> $null
            if ($LASTEXITCODE -eq 0) { return }
        } finally { $ErrorActionPreference = $previousPreference }
    } while ((Get-Date) -lt $deadline)
    throw 'Docker Desktop ne répond pas après cinq minutes. Vérifiez son état dans Windows.'
}

try {
    if ($env:OS -ne 'Windows_NT') { throw 'Ces scripts sont destinés à Windows avec Docker Desktop.' }
    Assert-Command docker 'Installez Docker Desktop et démarrez-le.'
    Assert-Command kubectl 'Installez kubectl (fourni avec Docker Desktop ou Kubernetes).'
    Assert-Command python 'Installez Python 3.12 ou plus récent et ajoutez-le au PATH.'
    $previousPreference = $ErrorActionPreference
    try {
        $ErrorActionPreference = 'Continue'
        $version = & python -c 'import sys; print(sys.version_info >= (3, 12))' 2>$null
        $pythonExit = $LASTEXITCODE
    } finally { $ErrorActionPreference = $previousPreference }
    if ($pythonExit -ne 0 -or $version -ne 'True') { throw 'Python 3.12 ou plus récent est requis.' }
    Wait-DockerDesktop
    $contexts = Invoke-KubectlResult -Arguments @('config','get-contexts','-o','name')
    if ($contexts.ExitCode -ne 0 -or $script:Context -notin $contexts.StdOut) {
        throw 'Contexte docker-desktop absent. Activez Kubernetes dans les paramètres de Docker Desktop.'
    }
    $nodes = Invoke-Kubectl -Arguments @('get','nodes','-o','json') | Out-String | ConvertFrom-Json
    if (-not @($nodes.items | Where-Object { $_.status.conditions | Where-Object { $_.type -eq 'Ready' -and $_.status -eq 'True' } }).Count) {
        throw 'Aucun nœud Kubernetes Ready. Attendez le démarrage de Kubernetes dans Docker Desktop.'
    }
    $envValues = Read-LocalEnv
    foreach ($path in @('agent/requirements.txt','k8s/monitoring/kustomization.yaml','k8s/agent-rbac.yaml','k8s/open-webui.yaml','k8s/open-webui-storage.yaml')) {
        if (-not (Test-Path (Join-Path $script:ProjectRoot $path))) { throw "Fichier du dépôt manquant : $path" }
    }
    $python = Join-Path $script:ProjectRoot '.venv\Scripts\python.exe'
    if (-not (Test-Path $python)) {
        $previousPreference = $ErrorActionPreference
        try {
            $ErrorActionPreference = 'Continue'
            & python -m venv (Join-Path $script:ProjectRoot '.venv')
            $venvExit = $LASTEXITCODE
        } finally { $ErrorActionPreference = $previousPreference }
        if ($venvExit -ne 0) { throw 'Création de .venv impossible.' }
    }
    $requirements = Join-Path $script:ProjectRoot 'agent\requirements.txt'
    $hash = (Get-FileHash -LiteralPath $requirements -Algorithm SHA256).Hash
    $stamp = Join-Path $script:ProjectRoot '.venv\kubeai-requirements.sha256'
    if (-not (Test-Path $stamp) -or (Get-Content -LiteralPath $stamp -Raw).Trim() -ne $hash) {
        $previousPreference = $ErrorActionPreference
        try {
            $ErrorActionPreference = 'Continue'
            & $python -m pip install -r $requirements
            $pipExit = $LASTEXITCODE
        } finally { $ErrorActionPreference = $previousPreference }
        if ($pipExit -ne 0) { throw 'Installation des dépendances Python impossible.' }
        Set-Content -LiteralPath $stamp -Value $hash -Encoding ASCII
    }
    # Appliquer les ressources idempotentes nécessaires à la session Windows.
    Invoke-Kubectl -Arguments @('apply','-f',(Join-Path $script:ProjectRoot 'k8s\namespace.yaml')) | Out-Null
    Invoke-Kubectl -Arguments @('apply','-f',(Join-Path $script:ProjectRoot 'k8s\agent-rbac.yaml')) | Out-Null
    Invoke-Kubectl -Arguments @('apply','-k',(Join-Path $script:ProjectRoot 'k8s\monitoring')) | Out-Null
    foreach ($deployment in @('mimir','loki','alloy','kube-state-metrics')) {
        Invoke-Kubectl -Arguments @('-n','monitoring','rollout','status',"deployment/$deployment",'--timeout=180s') | Out-Null
    }
    $existingSecret = Invoke-KubectlResult -Arguments @('-n','kubeai','get','secret','kubeai-api-auth','-o','name')
    if ($existingSecret.ExitCode -ne 0) {
        # Le secret passe par stdin ; aucune valeur n'est ajoutée à la ligne de commande.
        $bytes = New-Object byte[] 32
        [Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)
        $key = [Convert]::ToBase64String($bytes)
        $manifest = @{apiVersion='v1';kind='Secret';metadata=@{name='kubeai-api-auth';namespace='kubeai'};type='Opaque';stringData=@{'api-key'=$key}} | ConvertTo-Json -Depth 5 -Compress
        $previousPreference = $ErrorActionPreference
        try {
            $ErrorActionPreference = 'Continue'
            $manifest | & kubectl --context $script:Context apply -f - *> $null
            $secretExit = $LASTEXITCODE
        } finally { $ErrorActionPreference = $previousPreference }
        $key = $null; $manifest = $null
        if ($secretExit -ne 0) { throw 'Création du Secret kubeai-api-auth impossible.' }
    }
    Write-Host "Préparation terminée. Profil : $($envValues['LLM_PROVIDER'])."
} catch {
    [Console]::Error.WriteLine("Préparation KubeAI : $($_.Exception.Message)")
    exit 1
}
