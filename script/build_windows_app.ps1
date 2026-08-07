param(
    [string]$Python = "python",
    [string]$Configuration = "Release"
)

$ErrorActionPreference = "Stop"
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$BuildRoot = Join-Path $Root "build\windows"
$DistRoot = Join-Path $Root "dist"
$PackageRoot = Join-Path $DistRoot "Clean My Codex Windows x64"
$Project = Join-Path $Root "windows\CleanMyCodexApp\CleanMyCodexApp.csproj"
$Icon = Join-Path $BuildRoot "AppIcon.ico"
$Version = (& $Python -c "from clean_my_codex import APP_VERSION; print(APP_VERSION)").Trim()
$WebViewVersion = "1.0.4078.44"

& $Python -c "import PyInstaller" 2>$null
if ($LASTEXITCODE -ne 0) {
    throw "PyInstaller is required. Install requirements-windows-build.txt in a build-only environment."
}
if (-not (Get-Command dotnet -ErrorAction SilentlyContinue)) {
    throw ".NET 8 SDK is required to build the Windows desktop shell."
}

Remove-Item $BuildRoot -Recurse -Force -ErrorAction SilentlyContinue
Remove-Item $PackageRoot -Recurse -Force -ErrorAction SilentlyContinue
New-Item $BuildRoot -ItemType Directory -Force | Out-Null
New-Item $DistRoot -ItemType Directory -Force | Out-Null
New-Item (Join-Path $BuildRoot "server-spec") -ItemType Directory -Force | Out-Null
& $Python (Join-Path $Root "script\package_windows_icon.py") $Icon

$env:PYINSTALLER_CONFIG_DIR = Join-Path $BuildRoot "pyinstaller-config"
& $Python -m PyInstaller (Join-Path $Root "run.py") `
    --noconfirm `
    --clean `
    --onedir `
    --console `
    --name clean-my-codex-server `
    --distpath (Join-Path $BuildRoot "server-dist") `
    --workpath (Join-Path $BuildRoot "server-work") `
    --specpath (Join-Path $BuildRoot "server-spec")
if ($LASTEXITCODE -ne 0) { throw "The bundled Python service build failed." }

& dotnet restore $Project -p:CleanMyCodexIcon=$Icon
if ($LASTEXITCODE -ne 0) { throw "The Windows desktop dependencies could not be restored." }
& dotnet publish $Project `
    --configuration $Configuration `
    --runtime win-x64 `
    --self-contained true `
    --no-restore `
    --output (Join-Path $BuildRoot "desktop-publish") `
    -p:Version=$Version `
    -p:CleanMyCodexIcon=$Icon `
    -p:DebugType=None `
    -p:DebugSymbols=false
if ($LASTEXITCODE -ne 0) { throw "The Windows desktop shell build failed." }

New-Item $PackageRoot -ItemType Directory -Force | Out-Null
Copy-Item (Join-Path $BuildRoot "desktop-publish\*") $PackageRoot -Recurse -Force
Copy-Item (Join-Path $BuildRoot "server-dist\clean-my-codex-server") (Join-Path $PackageRoot "server") -Recurse
New-Item (Join-Path $PackageRoot "app") -ItemType Directory -Force | Out-Null
Copy-Item (Join-Path $Root "static") (Join-Path $PackageRoot "app\static") -Recurse
$Documentation = Join-Path $PackageRoot "Documentation"
New-Item $Documentation -ItemType Directory -Force | Out-Null
Copy-Item (Join-Path $Root "LICENSE"), (Join-Path $Root "README.md"), (Join-Path $Root "THIRD_PARTY_NOTICES.md") $Documentation

$PyInstallerLicense = (& $Python -c "from importlib.metadata import distribution; d=distribution('pyinstaller'); print(next(p.locate() for p in d.files if p.name == 'COPYING.txt'))").Trim()
$PythonLicense = (& $Python -c "import sys; from pathlib import Path; roots=(Path(sys.base_prefix), *Path(sys.base_prefix).parents); names=('LICENSE.txt','LICENSE'); print(next(root/name for root in roots for name in names if (root/name).is_file()))").Trim()
Copy-Item $PyInstallerLicense (Join-Path $Documentation "PYINSTALLER_COPYING.txt")
Copy-Item $PythonLicense (Join-Path $Documentation "PYTHON_LICENSE.txt")

$NuGetLine = (& dotnet nuget locals global-packages --list).Trim()
$NuGetRoot = ($NuGetLine -replace "^global-packages:\s*", "").Trim()
$WebViewPackage = Join-Path $NuGetRoot "microsoft.web.webview2\$WebViewVersion"
Copy-Item (Join-Path $WebViewPackage "LICENSE.txt") (Join-Path $Documentation "WEBVIEW2_LICENSE.txt")
Copy-Item (Join-Path $WebViewPackage "NOTICE.txt") (Join-Path $Documentation "WEBVIEW2_NOTICE.txt")

$SmokeRoot = Join-Path $BuildRoot "smoke"
$env:CLEAN_MY_CODEX_HOME = Join-Path $SmokeRoot ".codex"
$env:CLEAN_MY_CODEX_APP_HOME = Join-Path $SmokeRoot "app-data"
New-Item $env:CLEAN_MY_CODEX_HOME -ItemType Directory -Force | Out-Null
try {
    & (Join-Path $PackageRoot "Clean My Codex.exe") --smoke-test
    if ($LASTEXITCODE -ne 0) { throw "The packaged Windows app smoke test failed." }
} finally {
    Remove-Item Env:CLEAN_MY_CODEX_HOME -ErrorAction SilentlyContinue
    Remove-Item Env:CLEAN_MY_CODEX_APP_HOME -ErrorAction SilentlyContinue
}

$Archive = Join-Path $DistRoot "Clean-My-Codex-Windows-x64-v$Version.zip"
$Checksum = "$Archive.sha256"
Remove-Item $Archive, $Checksum -Force -ErrorAction SilentlyContinue
Compress-Archive -Path (Join-Path $PackageRoot "*") -DestinationPath $Archive -CompressionLevel Optimal
$Hash = (Get-FileHash $Archive -Algorithm SHA256).Hash.ToLowerInvariant()
Set-Content -Path $Checksum -Value "$Hash  $([IO.Path]::GetFileName($Archive))" -Encoding ascii

Write-Output "Package: $PackageRoot"
Write-Output "Archive: $Archive"
Write-Output "SHA-256: $Hash"
