#Requires -Version 5.1
param()
. (Join-Path $PSScriptRoot 'common.ps1')
$created = New-Object System.Collections.Generic.List[string]
$webuiStarted = $false

function Initialize-ReadonlyKubeconfig {
    $source = Invoke-Kubectl -Arguments @('config','view','--minify','--raw','-o','json') |
        Out-String | ConvertFrom-Json
    if (-not $source.clusters -or $source.clusters.Count -ne 1) {
        throw 'Impossible de lire le serveur Kubernetes du contexte docker-desktop.'
    }
    $cluster = $source.clusters[0].cluster
    $skipTls = $cluster.PSObject.Properties['insecure-skip-tls-verify'] -and $cluster.'insecure-skip-tls-verify'
    if ($skipTls -or -not $cluster.server.StartsWith('https://')) {
        throw 'Le contexte docker-desktop doit utiliser HTTPS avec vérification TLS.'
    }
    $token = Invoke-Kubectl -Arguments @('-n','kubeai','create','token','kubeai-agent','--duration=8h')
    if (-not $token) { throw 'Impossible de renouveler le jeton kubeai-agent.' }
    $config = @{
        apiVersion='v1'; kind='Config'; 'current-context'='kubeai-readonly'
        clusters=@(@{name='docker-desktop';cluster=$cluster})
        contexts=@(@{name='kubeai-readonly';context=@{cluster='docker-desktop';user='kubeai-agent';namespace='kubeai'}})
        users=@(@{name='kubeai-agent';user=@{token=$token}})
    } | ConvertTo-Json -Depth 20
    $directory = Split-Path $script:Kubeconfig -Parent
    New-Item -ItemType Directory -Path $directory -Force | Out-Null
    if (-not (Test-Path $script:Kubeconfig)) { New-Item -ItemType File -Path $script:Kubeconfig | Out-Null }
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $currentAcl = Get-Acl -LiteralPath $script:Kubeconfig
    $private = $currentAcl.AreAccessRulesProtected -and $currentAcl.Owner -eq $identity.Name -and
        @($currentAcl.Access | Where-Object {
            $_.IdentityReference.Value -notin @($identity.Name, $identity.User.Value)
        }).Count -eq 0
    if (-not $private) {
        $acl = New-Object Security.AccessControl.FileSecurity
        $acl.SetOwner($identity.User)
        $acl.SetAccessRuleProtection($true, $false)
        $rule = New-Object Security.AccessControl.FileSystemAccessRule($identity.User, 'FullControl', 'Allow')
        $acl.AddAccessRule($rule)
        Set-Acl -LiteralPath $script:Kubeconfig -AclObject $acl
    }
    [IO.File]::WriteAllText($script:Kubeconfig, $config, (New-Object Text.UTF8Encoding($false)))
    $token = $null; $config = $null
    $allowed = Invoke-KubectlResult -Kubeconfig $script:Kubeconfig -Context 'kubeai-readonly' `
        -Arguments @('auth','can-i','list','nodes','--all-namespaces')
    if ($allowed.ExitCode -ne 0 -or ($allowed.StdOut -join '') -ne 'yes') {
        throw 'Le kubeconfig de lecture seule ne peut pas lister les nœuds.'
    }
    $denied = Invoke-KubectlResult -Kubeconfig $script:Kubeconfig -Context 'kubeai-readonly' `
        -Arguments @('auth','can-i','get','secrets','-n','kubeai')
    # kubectl auth can-i retourne le code 1 pour une réponse légitime « no ».
    if (($denied.StdOut -join '') -ne 'no' -or $denied.ExitCode -notin @(0,1)) {
        throw 'Impossible de confirmer le refus des Secrets pour kubeai-agent.'
    }
}

function Start-Forward([string]$Name, [string]$Namespace, [string]$Service, [int]$LocalPort, [int]$RemotePort, [string]$HealthUrl) {
    if (-not (Get-TrackedProcess $Name)) { $created.Add($Name) }
    Start-TrackedProcess $Name (Get-Command kubectl).Source `
        @('--context', $script:Context, '-n', $Namespace, 'port-forward', '--address', '127.0.0.1',
          "service/$Service", "${LocalPort}:$RemotePort") $LocalPort $HealthUrl
}

try {
    & (Join-Path $PSScriptRoot 'setup.ps1')
    if ($LASTEXITCODE -ne 0) { throw 'Préparation initiale interrompue.' }
    $envValues = Read-LocalEnv
    Assert-Memory
    $pods = Invoke-Kubectl -Arguments @('-n','monitoring','get','pods','-o','json') | Out-String | ConvertFrom-Json
    if (-not @($pods.items).Count) { throw 'Aucun pod de monitoring dans le namespace monitoring.' }
    Initialize-ReadonlyKubeconfig
    Start-Forward 'mimir' 'monitoring' 'mimir' 9009 9009 'http://127.0.0.1:9009/ready'
    Start-Forward 'loki' 'monitoring' 'loki' 3100 3100 'http://127.0.0.1:3100/ready'

    $b64 = Invoke-Kubectl -Arguments @('-n','kubeai','get','secret','kubeai-api-auth',
        '-o','jsonpath={.data.api-key}')
    if (-not $b64) { throw 'Secret kubeai-api-auth introuvable ou vide.' }
    $env:KUBEAI_API_KEY = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($b64)).Trim()
    $b64 = $null
    $env:KUBEAI_ENV = 'development'
    $env:KUBERNETES_ACCESS_MODE = 'kubeconfig'
    $env:KUBERNETES_CONTEXT = 'kubeai-readonly'
    $env:KUBERNETES_KUBECONFIG_FILE = $script:Kubeconfig
    $env:MIMIR_URL = 'http://127.0.0.1:9009/prometheus'
    $env:LOKI_URL = 'http://127.0.0.1:3100'
    if (-not (Get-TrackedProcess 'fastapi')) { $created.Add('fastapi') }
    Start-TrackedProcess 'fastapi' (Join-Path $script:ProjectRoot '.venv\Scripts\python.exe') `
        @('-m','uvicorn','app.main:app','--app-dir','agent','--host','0.0.0.0','--port','8000','--no-proxy-headers') `
        8000 'http://127.0.0.1:8000/ready'
    # /llm/status interroge /models sans lancer de génération coûteuse.
    $headers = @{ Authorization = "Bearer $env:KUBEAI_API_KEY" }
    try {
        $llm = Invoke-RestMethod -Uri 'http://127.0.0.1:8000/llm/status' -Headers $headers -TimeoutSec 12
        if ($llm.status -ne 'ready' -or $llm.provider -ne $envValues['LLM_PROVIDER']) { throw 'Statut LLM inattendu.' }
    } catch { throw "Fournisseur IA indisponible ($($envValues['LLM_PROVIDER'])). Vérifiez sa clé, son endpoint et son modèle." }

    Assert-Memory
    Invoke-Kubectl -Arguments @('apply','-f',(Join-Path $script:ProjectRoot 'k8s\open-webui-storage.yaml')) | Out-Null
    $webui = Invoke-KubectlResult -Arguments @('-n','kubeai','get','deployment','open-webui','-o','name')
    if ($webui.ExitCode -ne 0) {
        Invoke-Kubectl -Arguments @('apply','-f',(Join-Path $script:ProjectRoot 'k8s\open-webui.yaml')) | Out-Null
    }
    $webuiStarted = $true
    # Un pod ne peut pas joindre le 127.0.0.1 de Windows. Docker Desktop fournit host.docker.internal.
    Invoke-Kubectl -Arguments @('-n','kubeai','set','env','deployment/open-webui',
        'OPENAI_API_BASE_URL=http://host.docker.internal:8000/v1') | Out-Null
    Invoke-Kubectl -Arguments @('-n','kubeai','scale','deployment/open-webui','--replicas=1') | Out-Null
    Invoke-Kubectl -Arguments @('-n','kubeai','rollout','status','deployment/open-webui','--timeout=180s') | Out-Null
    Assert-Memory
    $check = 'import json,os,urllib.request; u="http://host.docker.internal:8000/v1/models"; r=urllib.request.Request(u,headers={"Authorization":"Bearer "+os.environ["OPENAI_API_KEY"].strip()}); d=json.load(urllib.request.urlopen(r,timeout=8)); assert any(m.get("id")=="kubeai" for m in d["data"])'
    # Envoyer le code sur stdin : PowerShell 5.1 perd les guillemets de python -c.
    $podCheck = Invoke-KubectlResult -Arguments @('-n','kubeai','exec','-i','deployment/open-webui','--','python','-') `
        -InputText $check
    if ($podCheck.ExitCode -ne 0) { throw 'Open WebUI ne peut pas joindre FastAPI via host.docker.internal:8000. Vérifiez Docker Desktop et le pare-feu Windows.' }
    $openBrowser = -not (Get-TrackedProcess 'webui')
    Start-Forward 'webui' 'kubeai' 'open-webui' 8080 80 'http://127.0.0.1:8080/'
    Assert-Memory
    if ($openBrowser) { Start-Process 'http://127.0.0.1:8080' | Out-Null }
    Write-Host "`n========================================"
    Write-Host 'P5-G17 | KubeAI'
    Write-Host '========================================'
    Write-Host 'Kubernetes      : OK'
    Write-Host 'Mimir           : OK'
    Write-Host 'Loki            : OK'
    Write-Host 'FastAPI         : OK'
    Write-Host 'Open WebUI      : OK'
    Write-Host "Fournisseur IA  : $($envValues['LLM_PROVIDER'])"
    Write-Host 'Interface       : http://127.0.0.1:8080'
    Write-Host 'KubeAI est prêt.'
    Write-Host '========================================'
} catch {
    $failure = $_.Exception.Message
    if ($webuiStarted) {
        try { Invoke-KubectlResult -Arguments @('-n','kubeai','scale','deployment/open-webui','--replicas=0') | Out-Null } catch { }
    }
    foreach ($name in $created) { try { Stop-TrackedProcess $name } catch { } }
    [Console]::Error.WriteLine("KubeAI : $failure")
    exit 1
} finally {
    Remove-Item Env:KUBEAI_API_KEY -ErrorAction SilentlyContinue
}
