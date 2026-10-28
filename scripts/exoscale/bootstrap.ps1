#Requires -Version 5.1
[CmdletBinding()]
param(
    [switch]$Apply,
    [switch]$DryRun,
    [switch]$Yes,
    [string]$KubeConfigDir = "$HOME\.kube\kubeai-exoscale"
)
. (Join-Path $PSScriptRoot 'common.ps1')

if ($Apply -and $DryRun) { throw "Choisir -Apply ou -DryRun, pas les deux." }
$zone = $script:Config.zone
$network = $script:Config.privateNetwork
$cidr = $script:Config.privateNetworkCidr
$startIp = $script:Config.privateNetworkStartIp
$endIp = $script:Config.privateNetworkEndIp
$netmask = $script:Config.privateNetworkNetmask
$sg = $script:Config.securityGroup

function Assert-Ipv4DhcpRange {
    param([string]$Cidr, [string]$Start, [string]$End, [string]$Mask)
    $parts = $Cidr.Split('/')
    if ($parts.Count -ne 2 -or $parts[1] -ne '24') {
        throw "CIDR non supporte pour ce bootstrap : $Cidr (attendu /24)."
    }
    $networkParts = $parts[0].Split('.')
    $startParts = $Start.Split('.')
    $endParts = $End.Split('.')
    if ($networkParts.Count -ne 4 -or $startParts.Count -ne 4 -or $endParts.Count -ne 4) {
        throw "Adresse IPv4 invalide dans la plage DHCP."
    }
    $networkBase = ($networkParts[0..2] -join '.')
    if (($startParts[0..2] -join '.') -ne $networkBase -or ($endParts[0..2] -join '.') -ne $networkBase) {
        throw "Les adresses DHCP doivent appartenir au reseau $Cidr."
    }
    if ($Mask -ne '255.255.255.0') {
        throw "Masque non supporte : $Mask (attendu 255.255.255.0)."
    }
    $startHost = [int]$startParts[3]
    $endHost = [int]$endParts[3]
    if ($startHost -lt 2 -or $startHost -gt 253 -or $endHost -lt 2 -or $endHost -gt 253) {
        throw "Plage DHCP invalide : les adresses reseau, DHCP reservee et broadcast sont interdites."
    }
    if ($endHost -lt $startHost) {
        throw "Plage DHCP invalide : la fin ($End) precede le debut ($Start)."
    }
}

Assert-Ipv4DhcpRange -Cidr $cidr -Start $startIp -End $endIp -Mask $netmask

function Write-BootstrapPlan {
    Write-Host ""
    Write-Host "== KubeAI Exoscale - bootstrap initial =="
    Write-Host "Zone : $zone"
    Write-Host "Reseau prive : $network ($cidr)"
    Write-Host "Plage DHCP : $startIp -> $endIp / $netmask"
    Write-Host "Security Group : $sg"
    foreach ($item in @(Get-ClusterPlan)) {
        Write-Host ("{0} / {1} / {2} / disque {3} GiB" -f $item.Name, $item.NodePool, $item.NodeType, $item.DiskSizeGb)
    }
    Write-Host "Kubeconfigs : $KubeConfigDir"
    Write-Host "Aucune ressource ne sera creee sans -Apply et confirmation."
}

function Invoke-ExoWrite {
    param([string[]]$Arguments)
    Write-Host ("exo " + ($Arguments -join ' '))
    & exo @Arguments
    if ($LASTEXITCODE -ne 0) { throw "Commande Exoscale en echec : exo $($Arguments -join ' ')" }
}

function Add-PrivateRule {
    param([string]$SecurityGroup)
    try {
        Invoke-ExoWrite @('compute', 'security-group', 'rule', 'add', $SecurityGroup,
                          '--flow', 'ingress', '--protocol', 'all', '--network', $cidr,
                          '--description', 'kubeai private intercluster') | Out-Null
    } catch {
        Write-Host "Regle privee deja presente ou non ajoutee : $($_.Exception.Message)"
    }
}

function Add-EgressRule {
    param([string]$SecurityGroup)
    try {
        Invoke-ExoWrite @('compute', 'security-group', 'rule', 'add', $SecurityGroup,
                          '--flow', 'egress', '--protocol', 'all', '--network', '0.0.0.0/0',
                          '--description', 'kubeai egress') | Out-Null
    } catch {
        Write-Host "Regle egress deja presente ou non ajoutee : $($_.Exception.Message)"
    }
}

function Add-SelfRule {
    param([string]$SecurityGroup, [string]$Protocol, [string]$Port, [string]$Description)
    try {
        Invoke-ExoWrite @('compute', 'security-group', 'rule', 'add', $SecurityGroup,
                          '--flow', 'ingress', '--protocol', $Protocol, '--port', $Port,
                          '--security-group', $SecurityGroup,
                          '--description', $Description) | Out-Null
    } catch {
        Write-Host "Regle $Description deja presente ou non ajoutee : $($_.Exception.Message)"
    }
}

Write-BootstrapPlan
if (-not $Apply -or $DryRun) {
    Write-Host "Mode simulation : aucune creation."
    exit 0
}

Assert-ExoConfigured
if (-not $Yes) {
    Confirm-PaidOperation "Le bootstrap cree deux clusters SKS et des ressources possiblement facturables."
}

$networks = @(Get-ExoPrivateNetworks)
if ($network -notin $networks.name) {
    Invoke-ExoWrite @('compute', 'private-network', 'create', $network, '--zone', $zone,
                      '--start-ip', $startIp, '--end-ip', $endIp,
                      '--netmask', $netmask) | Out-Null
} else {
    Write-Host "Reseau prive deja present : $network"
    $existing = $networks | Where-Object { $_.name -eq $network } | Select-Object -First 1
    if ($null -ne $existing) {
        $existingStart = $existing.'start-ip'
        $existingEnd = $existing.'end-ip'
        $existingMask = $existing.netmask
        if (($null -ne $existingStart -and $existingStart -ne $startIp) -or
            ($null -ne $existingEnd -and $existingEnd -ne $endIp) -or
            ($null -ne $existingMask -and $existingMask -ne $netmask)) {
            throw "Le reseau $network existe avec une plage differente ($existingStart -> $existingEnd / $existingMask). Corriger manuellement ; le bootstrap ne supprime rien."
        }
    }
}

$groups = @(Get-ExoSecurityGroups)
if ($sg -notin $groups.name) {
    Invoke-ExoWrite @('compute', 'security-group', 'create', $sg, '--description',
                      'KubeAI private intercluster only') | Out-Null
} else {
    Write-Host "Security Group deja present : $sg"
}
Add-PrivateRule -SecurityGroup $sg
Add-SelfRule -SecurityGroup $sg -Protocol 'tcp' -Port '10250' -Description 'SKS kubelet'
Add-SelfRule -SecurityGroup $sg -Protocol 'udp' -Port '4789' -Description 'Calico VXLAN'
Add-EgressRule -SecurityGroup $sg

$clusters = @(Get-ExoClusters)
function Wait-SksRunning {
    param([string]$Name)
    $deadline = (Get-Date).AddMinutes(5)
    do {
        try {
            $found = Get-ExoCluster -Name $Name
        } catch {
            $found = $null
        }
        if ($null -ne $found -and $found.state -in @('running', 'Running')) {
            Write-Host "Cluster Running : $Name"
            return
        }
        Start-Sleep -Seconds 15
    } while ((Get-Date) -lt $deadline)
    throw "Le cluster $Name n'est pas Running apres 5 minutes."
}

foreach ($item in @(Get-ClusterPlan)) {
    if ($item.Name -notin $clusters.name) {
        Invoke-ExoWrite @('compute', 'sks', 'create', $item.Name, '--zone', $zone,
                          '--service-level', 'starter', '--cni', 'calico', '--exoscale-csi',
                          '--nodepool-size', '0', '--label', "kubeai.io/role=$($item.Role)") | Out-Null
    } else {
        Write-Host "Cluster deja present : $($item.Name)"
    }
    Wait-SksRunning -Name $item.Name

    $pools = @(Get-ExoNodePools -Cluster $item.Name)
    if ($pools.Count -eq 0) {
        Write-Host "Aucun Nodepool dans $($item.Name) (attendu pour ce bootstrap)."
    } else {
        Write-Warning ("Nodepool(s) deja present(s) dans $($item.Name) : " +
                       (($pools | ForEach-Object name) -join ', ') +
                       ". Le bootstrap ne les cree ni ne les supprime.")
    }
}

$deadline = (Get-Date).AddMinutes(5)
do {
    $allReady = $true
    foreach ($item in @(Get-ClusterPlan)) {
        try {
            $found = Get-ExoCluster -Name $item.Name
        } catch {
            $found = $null
        }
        if ($null -eq $found -or $found.state -notin @('running', 'Running')) { $allReady = $false }
    }
    if (-not $allReady) { Start-Sleep -Seconds 15 }
} while (-not $allReady -and (Get-Date) -lt $deadline)
if (-not $allReady) { throw "Les clusters SKS ne sont pas Running apres 5 minutes." }

$totalPools = 0
foreach ($item in @(Get-ClusterPlan)) {
    $totalPools += @(Get-ExoNodePools -Cluster $item.Name).Count
}
$snapshot = Get-ExoResourceSnapshot
$instanceCount = @($snapshot.Instances).Count

if ($totalPools -eq 0 -and $instanceCount -eq 0) {
    Write-Host "BOOTSTRAP SUCCESS - 2 clusters, 0 Nodepool, 0 Compute instance"
} else {
    Write-Warning ("Bootstrap incomplet pour le mode zero-Nodepool : {0} Nodepool(s), {1} instance(s) Compute." -f $totalPools, $instanceCount)
    Write-Host "BOOTSTRAP NO-OP - ressources existantes conservees, aucune suppression."
}

New-Item -ItemType Directory -Path $KubeConfigDir -Force | Out-Null
foreach ($item in @(Get-ClusterPlan)) {
    $path = Join-Path $KubeConfigDir ($item.Name + '.yaml')
    if (-not (Test-Path -LiteralPath $path)) {
        Write-Host "Generation du kubeconfig admin : $path"
        & exo compute sks kubeconfig $item.Name admin --zone $zone -g system:masters -t 43200 |
            Out-File -LiteralPath $path -Encoding ascii
        if ($LASTEXITCODE -ne 0) { throw "Generation du kubeconfig impossible pour $($item.Name)." }
    } else {
        Write-Host "Kubeconfig deja present, non ecrase : $path"
    }
}

Write-Host ""
Write-Host "Bootstrap termine."
Write-Host "Les kubeconfigs admin sont hors du depot et ne doivent jamais etre commites."
Write-Host "Pour cette session PowerShell :"
Write-Host ('$env:KUBECONFIG = "' + (Join-Path $KubeConfigDir 'kubeai-observed.yaml') + ';' +
           (Join-Path $KubeConfigDir 'kubeai-supervision.yaml') + '"')
Write-Host "Verifier ensuite : kubectl config get-contexts"
exit 0
