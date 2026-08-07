param(
    [Parameter(Mandatory = $true)]
    [string]$PackageRoot,

    [Parameter(Mandatory = $true)]
    [string]$CertificateThumbprint,

    [Parameter(Mandatory = $true)]
    [string]$OutputArchive,

    [string]$TimestampUrl = "http://timestamp.digicert.com"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$PackageRoot = (Resolve-Path $PackageRoot).Path
$OutputArchive = [IO.Path]::GetFullPath($OutputArchive)
$CertificateThumbprint = ($CertificateThumbprint -replace "\s", "").ToUpperInvariant()

if ($CertificateThumbprint -notmatch "^[0-9A-F]{40}$") {
    throw "CertificateThumbprint must be a SHA-1 certificate thumbprint."
}
if (-not (Test-Path $PackageRoot -PathType Container)) {
    throw "The Windows package directory does not exist: $PackageRoot"
}
if (-not [Uri]::IsWellFormedUriString($TimestampUrl, [UriKind]::Absolute)) {
    throw "TimestampUrl must be an absolute RFC 3161 timestamp URL."
}

$CertificatePath = "Cert:\CurrentUser\My\$CertificateThumbprint"
$Certificate = Get-Item $CertificatePath -ErrorAction Stop
if (-not $Certificate.HasPrivateKey) {
    throw "The selected signing certificate does not have an accessible private key."
}
if ($Certificate.NotAfter.ToUniversalTime() -le [DateTime]::UtcNow) {
    throw "The selected signing certificate is expired."
}
$CodeSigningOid = "1.3.6.1.5.5.7.3.3"
$HasCodeSigningUsage = @($Certificate.EnhancedKeyUsageList) | Where-Object {
    $_.ObjectId.Value -eq $CodeSigningOid
}
if (-not $HasCodeSigningUsage) {
    throw "The selected certificate is not valid for code signing."
}

$WindowsKits = Join-Path ${env:ProgramFiles(x86)} "Windows Kits\10\bin"
$SignTool = Get-ChildItem $WindowsKits -Filter signtool.exe -File -Recurse |
    Where-Object { $_.FullName -match '\\x64\\signtool\.exe$' } |
    Sort-Object FullName -Descending |
    Select-Object -First 1
if (-not $SignTool) {
    throw "The Windows SDK SignTool executable is unavailable."
}

# Only project-owned PE files are signed. Third-party runtime files remain untouched.
$Targets = @(
    (Join-Path $PackageRoot "Clean My Codex.exe"),
    (Join-Path $PackageRoot "Clean My Codex.dll"),
    (Join-Path $PackageRoot "server\clean-my-codex-server.exe")
)
foreach ($Target in $Targets) {
    if (-not (Test-Path $Target -PathType Leaf)) {
        throw "Expected project-owned signing target is missing: $Target"
    }
}

foreach ($Target in $Targets) {
    & $SignTool.FullName sign `
        /sha1 $CertificateThumbprint `
        /s My `
        /fd SHA256 `
        /tr $TimestampUrl `
        /td SHA256 `
        $Target
    if ($LASTEXITCODE -ne 0) {
        throw "SignTool failed while signing: $Target"
    }

    & $SignTool.FullName verify /pa /all /tw /v $Target
    if ($LASTEXITCODE -ne 0) {
        throw "SignTool verification failed: $Target"
    }

    $Signature = Get-AuthenticodeSignature $Target
    if ($Signature.Status -ne [System.Management.Automation.SignatureStatus]::Valid) {
        throw "Authenticode verification did not return Valid: $Target"
    }
    if ($Signature.SignerCertificate.Thumbprint -ne $CertificateThumbprint) {
        throw "The signed file does not use the requested certificate: $Target"
    }
    if (-not $Signature.TimeStamperCertificate) {
        throw "The signed file does not contain a trusted timestamp: $Target"
    }
}

$OutputDirectory = Split-Path $OutputArchive -Parent
New-Item $OutputDirectory -ItemType Directory -Force | Out-Null
$Checksum = "$OutputArchive.sha256"
Remove-Item $OutputArchive, $Checksum -Force -ErrorAction SilentlyContinue
Compress-Archive -Path (Join-Path $PackageRoot "*") -DestinationPath $OutputArchive -CompressionLevel Optimal
$Hash = (Get-FileHash $OutputArchive -Algorithm SHA256).Hash.ToLowerInvariant()
Set-Content -Path $Checksum -Value "$Hash  $([IO.Path]::GetFileName($OutputArchive))" -Encoding ascii

Write-Output "Signed Windows package: $OutputArchive"
Write-Output "Signed project files: $($Targets.Count)"
Write-Output "SHA-256: $Hash"
