Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$script:ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$script:StateDir = Join-Path $script:ProjectRoot '.cache\windows-start'
$script:Context = 'docker-desktop'
$profileDirectory = $env:USERPROFILE
if ([string]::IsNullOrWhiteSpace($profileDirectory)) {
    $profileDirectory = [Environment]::GetFolderPath('UserProfile')
}
$script:Kubeconfig = Join-Path $profileDirectory '.kube/kubeai-readonly.yaml'

function Assert-Command([string]$Name, [string]$Help) {
    if (-not (Get-Command $Name -ErrorAction SilentlyContinue)) { throw "$Name est introuvable. $Help" }
}

function Invoke-KubectlResult([string[]]$Arguments, [string]$Context = $script:Context,
                              [string]$Kubeconfig = '', [string]$InputText = '') {
    $commandArgs = @('--context', $Context)
    if ($Kubeconfig) { $commandArgs += @('--kubeconfig', $Kubeconfig) }
    $commandArgs += $Arguments
    # Windows PowerShell 5.1 transforme stderr d'un programme natif en ErrorRecord.
    # Avec ErrorActionPreference=Stop, même un avertissement et un code 0 arrête
    # alors le script. Isoler cette règle pendant l'appel et vérifier le code.
    $previousPreference = $ErrorActionPreference
    try {
        $ErrorActionPreference = 'Continue'
        if ($PSBoundParameters.ContainsKey('InputText')) {
            $raw = @($InputText | & kubectl @commandArgs 2>&1)
        } else {
            $raw = @(& kubectl @commandArgs 2>&1)
        }
        $exitCode = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $previousPreference
    }
    $stdout = @($raw | Where-Object { $_ -isnot [Management.Automation.ErrorRecord] } |
        ForEach-Object { [string]$_ })
    $stderr = @($raw | Where-Object { $_ -is [Management.Automation.ErrorRecord] } |
        ForEach-Object { $_.ToString() } | Where-Object { $_ })
    return [pscustomobject]@{ StdOut = $stdout; StdErr = $stderr; ExitCode = $exitCode }
}

function Invoke-Kubectl([string[]]$Arguments, [string]$Context = $script:Context,
                        [string]$Kubeconfig = '') {
    $result = Invoke-KubectlResult -Arguments $Arguments -Context $Context -Kubeconfig $Kubeconfig
    if ($result.ExitCode -ne 0) {
        $detail = if ($result.StdErr.Count) { $result.StdErr -join ' ' } else { "code $($result.ExitCode)" }
        throw "kubectl a échoué ($($Arguments -join ' ')): $detail"
    }
    return $result.StdOut
}

function Test-Http([string]$Url, [int]$Timeout = 4) {
    try {
        $response = Invoke-WebRequest -Uri $Url -TimeoutSec $Timeout -UseBasicParsing
        return ($response.StatusCode -ge 200 -and $response.StatusCode -lt 300)
    } catch { return $false }
}

function Wait-Http([string]$Url, [int]$Seconds = 60) {
    $end = (Get-Date).AddSeconds($Seconds)
    do {
        if (Test-Http $Url) { return }
        Start-Sleep -Seconds 2
    } while ((Get-Date) -lt $end)
    throw "Service inaccessible après $Seconds secondes : $Url. Consultez les journaux dans $script:StateDir."
}

function Get-FreeGiB {
    $os = Get-CimInstance Win32_OperatingSystem
    return [math]::Round(($os.FreePhysicalMemory * 1KB) / 1GB, 2)
}

function Assert-Memory([double]$Minimum = 0.5) {
    $free = Get-FreeGiB
    if ($free -lt $Minimum) {
        $deadline = (Get-Date).AddSeconds(30)
        Write-Host "RAM libre : $free Gio. Attente d'une récupération de mémoire..."
        do {
            Start-Sleep -Seconds 3
            $free = Get-FreeGiB
        } while ($free -lt $Minimum -and (Get-Date) -lt $deadline)
    }
    if ($free -lt $Minimum) {
        throw "RAM libre : $free Gio, sous le seuil de $Minimum Gio. Fermez une application gourmande en mémoire, puis relancez .\scripts\start.ps1."
    }
    Write-Host "RAM libre : $free Gio"
}

function Get-TrackedProcess([string]$Name) {
    $path = Join-Path $script:StateDir "$Name.json"
    if (-not (Test-Path $path)) { return $null }
    try {
        $record = Get-Content -LiteralPath $path -Raw | ConvertFrom-Json
        $process = Get-Process -Id $record.pid -ErrorAction SilentlyContinue
        if ($process -and $process.StartTime.ToUniversalTime().Ticks -eq [int64]$record.startTicks) {
            return $process
        }
    } catch { }
    Remove-Item -LiteralPath $path -Force -ErrorAction SilentlyContinue
    return $null
}

function Stop-TrackedProcess([string]$Name) {
    $process = Get-TrackedProcess $Name
    if ($process) {
        Stop-Process -Id $process.Id -ErrorAction Stop
        $process.WaitForExit(5000) | Out-Null
    }
    $path = Join-Path $script:StateDir "$Name.json"
    Remove-Item -LiteralPath $path -Force -ErrorAction SilentlyContinue
}

function Assert-PortAvailable([int]$Port, [string]$Name) {
    if (Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue) {
        throw "Le port $Port est déjà occupé et aucun processus $Name suivi n'est utilisable. Libérez ce port."
    }
}

function Start-TrackedProcess([string]$Name, [string]$File, [string[]]$Arguments, [int]$Port, [string]$HealthUrl) {
    $existing = Get-TrackedProcess $Name
    if ($existing) {
        if (Test-Http $HealthUrl) { return }
        Stop-TrackedProcess $Name
    }
    Assert-PortAvailable $Port $Name
    New-Item -ItemType Directory -Path $script:StateDir -Force | Out-Null
    $process = Start-Process -FilePath $File -ArgumentList $Arguments -WorkingDirectory $script:ProjectRoot -PassThru -WindowStyle Hidden `
        -RedirectStandardOutput (Join-Path $script:StateDir "$Name.out.log") `
        -RedirectStandardError (Join-Path $script:StateDir "$Name.err.log")
    @{ pid = $process.Id; startTicks = $process.StartTime.ToUniversalTime().Ticks } |
        ConvertTo-Json | Set-Content -LiteralPath (Join-Path $script:StateDir "$Name.json") -Encoding UTF8
    try { Wait-Http $HealthUrl 60 } catch { Stop-TrackedProcess $Name; throw }
}

function Read-LocalEnv {
    $path = Join-Path $script:ProjectRoot '.env'
    if (-not (Test-Path $path)) { throw "Fichier .env absent. Copiez .env.example vers .env et configurez le fournisseur IA." }
    $values = @{}
    foreach ($line in Get-Content -LiteralPath $path) {
        $trimmed = $line.Trim()
        if (-not $trimmed -or $trimmed.StartsWith('#')) { continue }
        if ($trimmed -notmatch '^([A-Z][A-Z0-9_]*)=(.*)$') { throw "Ligne .env invalide (format NOM=valeur)." }
        $value = $Matches[2].Trim()
        if (($value.StartsWith('"') -and $value.EndsWith('"')) -or
            ($value.StartsWith("'") -and $value.EndsWith("'"))) { $value = $value.Substring(1, $value.Length - 2) }
        $values[$Matches[1]] = $value
    }
    $allowed = @('LLM_PROVIDER','MISTRAL_API_KEY','MISTRAL_BASE_URL','MISTRAL_MODEL','MISTRAL_API_KEY_FILE',
        'LLM_BASE_URL','LLM_MODEL','LLM_API_KEY_FILE','LLM_TIMEOUT_SECONDS',
        'UPSTREAM_TIMEOUT_SECONDS','METRICS_MAX_AGE_SECONDS','KUBERNETES_NAMESPACE',
        'KUBEAI_DISPLAY_TIMEZONE')
    foreach ($key in $allowed) {
        if ($key -in @('MISTRAL_API_KEY_FILE','LLM_API_KEY_FILE') -and $values[$key] -and
            -not [IO.Path]::IsPathRooted($values[$key])) {
            $values[$key] = Join-Path $script:ProjectRoot $values[$key]
        }
        if ($values.ContainsKey($key)) { [Environment]::SetEnvironmentVariable($key, $values[$key], 'Process') }
    }
    if ($values['LLM_PROVIDER'] -notin @('mistral-api', 'local')) {
        throw 'LLM_PROVIDER doit être mistral-api ou local dans .env.'
    }
    if ($values['LLM_PROVIDER'] -eq 'mistral-api' -and
        (-not $values['MISTRAL_API_KEY'] -or $values['MISTRAL_API_KEY'] -like 'REMPLACER*') -and
        (-not $values['MISTRAL_API_KEY_FILE'] -or -not (Test-Path $values['MISTRAL_API_KEY_FILE']))) {
        throw 'Clé Mistral manquante dans .env (MISTRAL_API_KEY ou MISTRAL_API_KEY_FILE).'
    }
    if ($values['LLM_PROVIDER'] -eq 'local' -and (-not $values['LLM_BASE_URL'] -or -not $values['LLM_MODEL'])) {
        throw 'Le profil local exige LLM_BASE_URL et LLM_MODEL dans .env.'
    }
    return $values
}
