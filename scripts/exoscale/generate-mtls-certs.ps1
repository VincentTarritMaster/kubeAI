#Requires -Version 5.1
[CmdletBinding()]
param(
    [string]$OutDir = "$HOME\.kube\kubeai-mtls",
    [switch]$Apply,
    [switch]$DryRun,
    [switch]$Force,
    [switch]$CertsOnly
)
. (Join-Path $PSScriptRoot 'common.ps1')
$ErrorActionPreference = 'Continue'

if ($Apply -and $DryRun) { throw "Choisir -Apply ou -DryRun, pas les deux." }
$openssl = (Get-Command openssl -ErrorAction SilentlyContinue).Source
if (-not $openssl) {
    $candidates = @(
        'C:\Program Files\Git\mingw64\bin\openssl.exe',
        'C:\Program Files\Git\usr\bin\openssl.exe'
    )
    foreach ($candidate in $candidates) {
        if (Test-Path -LiteralPath $candidate) { $openssl = $candidate; break }
    }
}
if (-not $openssl) { throw "OpenSSL introuvable ; installer Git for Windows ou OpenSSL." }

Write-Host "OpenSSL : $openssl"
Write-Host "Certificats : $OutDir"
Write-Host "CA, certificat serveur mTLS et certificat client Alloy seront générés hors du dépôt."
if (-not $Apply -or $DryRun) {
    Write-Host "Simulation : aucun certificat ni Secret créé."
    exit 0
}

New-Item -ItemType Directory -Path $OutDir -Force | Out-Null
$files = @('ca.key','ca.crt','server.key','server.csr','server.crt','client.key','client.csr','client.crt')
if (-not $Force) {
    $existing = @($files | Where-Object { Test-Path (Join-Path $OutDir $_) })
    if ($existing.Count) { throw "Fichiers déjà présents : $($existing -join ', '). Utiliser -Force pour les remplacer." }
}

Push-Location $OutDir
try {
    $serverExt = Join-Path $OutDir 'server.ext'
    $clientExt = Join-Path $OutDir 'client.ext'
    Set-Content -LiteralPath $serverExt -Value @(
        'subjectAltName=DNS:mimir.kubeai.svc,DNS:loki.kubeai.svc,DNS:observability-proxy.kubeai.svc',
        'extendedKeyUsage=serverAuth'
    ) -Encoding ascii
    Set-Content -LiteralPath $clientExt -Value @('extendedKeyUsage=clientAuth') -Encoding ascii

    & $openssl req -x509 -newkey rsa:4096 -sha256 -days 3650 -nodes `
        -keyout ca.key -out ca.crt -subj "/CN=kubeai-private-ca" 2>$null
    if ($LASTEXITCODE -ne 0) { throw 'Génération CA impossible.' }
    & $openssl req -newkey rsa:3072 -sha256 -nodes -keyout server.key -out server.csr `
        -subj "/CN=mimir.kubeai.svc" 2>$null
    if ($LASTEXITCODE -ne 0) { throw 'Génération clé serveur impossible.' }
    & $openssl x509 -req -in server.csr -CA ca.crt -CAkey ca.key -CAcreateserial -out server.crt `
        -days 825 -sha256 -extfile $serverExt 2>$null
    if ($LASTEXITCODE -ne 0) { throw 'Signature certificat serveur impossible.' }
    & $openssl req -newkey rsa:3072 -sha256 -nodes -keyout client.key -out client.csr `
        -subj "/CN=alloy" 2>$null
    if ($LASTEXITCODE -ne 0) { throw 'Génération clé client impossible.' }
    & $openssl x509 -req -in client.csr -CA ca.crt -CAkey ca.key -CAcreateserial -out client.crt `
        -days 825 -sha256 -extfile $clientExt 2>$null
    if ($LASTEXITCODE -ne 0) { throw 'Signature certificat client impossible.' }
} finally {
    Pop-Location
}

if ($CertsOnly) {
    Write-Host 'Certificats générés localement ; Secrets non créés (-CertsOnly).'
    return
}

& kubectl --context 'kubeai-supervision' -n kubeai create secret generic kubeai-observability-mtls `
    --from-file=server.crt="$OutDir\server.crt" --from-file=server.key="$OutDir\server.key" `
    --from-file=ca.crt="$OutDir\ca.crt" --dry-run=client -o yaml |
    kubectl --context 'kubeai-supervision' apply -f -
if ($LASTEXITCODE -ne 0) { throw 'Création du Secret serveur impossible.' }

& kubectl --context 'kubeai-observed' -n collecte create secret generic kubeai-alloy-mtls-client `
    --from-file=client.crt="$OutDir\client.crt" --from-file=client.key="$OutDir\client.key" `
    --from-file=ca.crt="$OutDir\ca.crt" --dry-run=client -o yaml |
    kubectl --context 'kubeai-observed' apply -f -
if ($LASTEXITCODE -ne 0) { throw 'Création du Secret client impossible.' }

Write-Host 'Certificats générés et Secrets créés hors du dépôt.'
Write-Host 'Ne jamais ajouter le contenu de ce répertoire à Git.'
