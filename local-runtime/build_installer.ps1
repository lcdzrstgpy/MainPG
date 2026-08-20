# Build the local workbench distribution (PyInstaller onedir + zip + Inno Setup installer).
# Usage: powershell -ExecutionPolicy Bypass -File build_installer.ps1 -Version 1.1.0
# Security: local credential files are never copied into public artifacts.
# Set MAINPG_RELEASE_SIGNING_KEY_PATH only for signed release-manifest generation.
# NOTE: keep this file ASCII-only to avoid PowerShell 5 (ANSI) encoding issues.

param(
    # SemVer 2.0.0, kept in parity with wh_local.app_update.SemanticVersion.
    [ValidatePattern('^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)(?:-(?:(?:0|[1-9][0-9]*|[0-9A-Za-z-]*[A-Za-z-][0-9A-Za-z-]*)(?:\.(?:0|[1-9][0-9]*|[0-9A-Za-z-]*[A-Za-z-][0-9A-Za-z-]*))*))?(?:\+(?:[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?\z')]
    [string]$Version = "1.1.0",
    [string]$InstallerUrl = "",
    [switch]$Mandatory,
    [string]$ReleaseNotes = "",
    [string]$PublishedAt = "",
    # Incremental patch: previous dist\MainPG directory + upload base URL.
    # When PatchFromDir is set, build_installer.ps1 also produces the signed
    # patch-manifest.json plus the changed files under dist\patch-<Version>.
    [string]$PatchFromDir = "",
    [string]$PatchBaseUrl = ""
)

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

if (-not $PublishedAt) { $PublishedAt = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ") }

# This module is bundled by PyInstaller and is the runtime's build metadata.
$runtimeConfig = Join-Path $PSScriptRoot "wh_local\config.py"
# PowerShell 5's Get-Content/Set-Content use the system ANSI code page and write a
# UTF-8 BOM, which corrupts non-ASCII source comments on every build. Use explicit
# UTF-8 (no BOM) file IO instead.
$configText = [IO.File]::ReadAllText($runtimeConfig, [Text.Encoding]::UTF8)
$versionMatch = [regex]::Match($configText, '(?m)^APP_VERSION = "[^"]*"$')
if (-not $versionMatch.Success) { throw "APP_VERSION metadata entry missing from $runtimeConfig" }
$updatedConfig = [regex]::Replace($configText, '(?m)^APP_VERSION = "[^"]*"$', "APP_VERSION = `"$Version`"", 1)
[IO.File]::WriteAllText($runtimeConfig, $updatedConfig, [Text.UTF8Encoding]::new($false))

# Pin the interpreter that has the packaging dependencies (override with WH_PYTHON);
# avoids interference from other Pythons in PATH.
$python = $null
if ($env:WH_PYTHON -and (Test-Path $env:WH_PYTHON)) { $python = $env:WH_PYTHON }
if (-not $python) {
    $python = @(
        "$env:LOCALAPPDATA\Programs\Python\Python311\python.exe",
        "C:\ProgramData\miniconda3\python.exe"
    ) | Where-Object { Test-Path $_ } | Select-Object -First 1
}
if (-not $python) { throw "Python not found (set WH_PYTHON to an interpreter with PyInstaller/uvicorn/qcloud_cos)" }
Write-Host "[build] using Python: $python"

# 1. Build the frontend
Write-Host "[build] building web-frontend ..."
Push-Location ..\web-frontend
npm run build
if ($LASTEXITCODE -ne 0) { throw "frontend build failed" }
Pop-Location

# 2. Ensure PyInstaller
& $python -c "import importlib.util, sys; sys.exit(0 if importlib.util.find_spec('PyInstaller') else 1)"
if ($LASTEXITCODE -ne 0) {
    Write-Host "[build] installing PyInstaller ..."
    & $python -m pip install pyinstaller
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller install failed" }
}

# 3. PyInstaller bundle (onedir -> dist\MainPG)
Write-Host "[build] running PyInstaller ..."
& $python -m PyInstaller workbench.spec --noconfirm --clean
if ($LASTEXITCODE -ne 0) { throw "PyInstaller build failed" }

$dist = Join-Path $PSScriptRoot "dist\MainPG"
if (-not (Test-Path $dist)) { throw "bundle output missing: $dist" }

# 4. Copy the app icon so shortcuts can use the product logo (jieye-mark).
# Existing installations keep their app-local credential files because the installer does not
# delete them. Fresh installations must be configured from System Settings or environment vars.
Copy-Item "app-icon.ico" $dist -ErrorAction Stop

# 4b. Compile the offline patch updater into the bundle root (next to MainPG.exe).
# It applies replace/add/delete after the main process exits, then relaunches.
Write-Host "[build] compiling MainPG-Updater.exe ..."
$csc = @(
    "$env:WINDIR\Microsoft.NET\Framework64\v4.0.30319\csc.exe",
    "$env:WINDIR\Microsoft.NET\Framework\v4.0.30319\csc.exe"
) | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $csc) { throw "csc.exe (.NET Framework) not found - cannot build MainPG-Updater.exe" }
& $csc /nologo /target:winexe "/out:$dist\MainPG-Updater.exe" "updater\MainPGUpdater.cs"
if ($LASTEXITCODE -ne 0) { throw "MainPG-Updater.exe compile failed" }

# 4c. Write version.json (UTF-8 without BOM - Python json.loads rejects BOM).
$versionJson = Join-Path $dist "version.json"
[IO.File]::WriteAllText($versionJson, "{`"version`":`"$Version`"}" + [Environment]::NewLine, (New-Object System.Text.UTF8Encoding($false)))
Write-Host "[build] wrote $versionJson"

$forbiddenNames = @(
    "cos.local.json",
    "onebound.local.json",
    ".env",
    "workbench.sqlite3"
)
$forbiddenFiles = Get-ChildItem -LiteralPath $dist -Recurse -File | Where-Object {
    $forbiddenNames -contains $_.Name -or
    $_.Extension -in @(".sqlite", ".sqlite3", ".db")
}
if ($forbiddenFiles) {
    throw "Refusing to package local credentials or databases"
}

# 5. Pack a portable zip
Write-Host "[build] creating portable zip ..."
$zip = Join-Path $PSScriptRoot "dist\MainPG-portable-$Version.zip"
if (Test-Path $zip) { Remove-Item $zip }
Compress-Archive -Path $dist -DestinationPath $zip -CompressionLevel Optimal

# 6. Build the Setup installer with Inno Setup (creates desktop / start-menu shortcuts on install)
Write-Host "[build] building Setup installer with Inno Setup ..."
$iscc = @(
    "$env:ProgramFiles(x86)\Inno Setup 6\ISCC.exe",
    "$env:ProgramFiles\Inno Setup 6\ISCC.exe",
    "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe"
) | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $iscc) { throw "ISCC.exe (Inno Setup 6) not found - install Inno Setup first" }

# mainpg-installer.iss contains Chinese; ISCC decodes BOM-less files using the system ANSI
# code page, so write a UTF-8-with-BOM copy before compiling.
$issSource = Join-Path $PSScriptRoot "mainpg-installer.iss"
$issBuild = Join-Path $PSScriptRoot "mainpg-installer.build.iss"
$issText = Get-Content -Raw -Encoding UTF8 $issSource
[IO.File]::WriteAllText($issBuild, $issText, [Text.UTF8Encoding]::new($true))
try {
    & $iscc "/DMyAppVersion=$Version" "/DMySetupBaseFilename=MainPG-Setup-$Version" $issBuild
    if ($LASTEXITCODE -ne 0) { throw "Inno Setup compile failed" }
} finally {
    Remove-Item $issBuild -ErrorAction SilentlyContinue
}

$installer = Join-Path $PSScriptRoot "dist\MainPG-Setup-$Version.exe"
if (-not (Test-Path -LiteralPath $installer)) { throw "versioned installer output missing: $installer" }

# 7. Build the signed incremental patch (optional). Compares PatchFromDir (the
# previous dist\MainPG) against the new bundle and emits only changed files.
if ($PatchFromDir) {
    $signingKeyPath = $env:MAINPG_RELEASE_SIGNING_KEY_PATH
    if (-not $signingKeyPath) {
        throw "MAINPG_RELEASE_SIGNING_KEY_PATH is required when -PatchFromDir requests patch generation"
    }
    if (-not (Test-Path -LiteralPath $signingKeyPath -PathType Leaf)) {
        throw "MAINPG_RELEASE_SIGNING_KEY_PATH does not point to a readable private key file"
    }
    if (-not (Test-Path -LiteralPath $PatchFromDir)) {
        throw "PatchFromDir does not exist: $PatchFromDir"
    }
    if (-not $PatchBaseUrl) {
        throw "PatchBaseUrl is required with -PatchFromDir (e.g. https://workbench.haocoming.top/mainpg/windows/patch/$Version)"
    }
    $patchOut = Join-Path $PSScriptRoot "dist\patch-$Version"
    $oldVersion = "0.0.0"
    $oldVersionJson = Join-Path $PatchFromDir "version.json"
    if (Test-Path -LiteralPath $oldVersionJson) {
        try {
            $oldVersion = (Get-Content -Raw -LiteralPath $oldVersionJson | ConvertFrom-Json).version
        } catch { }
    }
    & $python -m wh_local.runtime.patch_manifest_builder `
        --from-dir $PatchFromDir `
        --to-dir $dist `
        --from-version $oldVersion `
        --to-version $Version `
        --file-base-url $PatchBaseUrl `
        --private-key-path $signingKeyPath `
        --output-dir $patchOut
    if ($LASTEXITCODE -ne 0) { throw "patch manifest generation failed" }
    if (-not (Test-Path -LiteralPath (Join-Path $patchOut "patch-manifest.json"))) { throw "patch manifest output missing" }
    Write-Host "[build] signed incremental patch: $patchOut"
}

if ($InstallerUrl) {
    $signingKeyPath = $env:MAINPG_RELEASE_SIGNING_KEY_PATH
    if (-not $signingKeyPath) {
        throw "MAINPG_RELEASE_SIGNING_KEY_PATH is required when -InstallerUrl requests release-manifest generation"
    }
    if (-not (Test-Path -LiteralPath $signingKeyPath -PathType Leaf)) {
        throw "MAINPG_RELEASE_SIGNING_KEY_PATH does not point to a readable private key file"
    }

    $sha256 = (Get-FileHash -LiteralPath $installer -Algorithm SHA256).Hash.ToLowerInvariant()
    $manifest = Join-Path $PSScriptRoot "dist\release-manifest.json"
    & $python -m wh_local.runtime.release_manifest `
        --version $Version `
        --mandatory $Mandatory.IsPresent.ToString().ToLowerInvariant() `
        --installer-url $InstallerUrl `
        --sha256 $sha256 `
        --release-notes $ReleaseNotes `
        --published-at $PublishedAt `
        --private-key-path $signingKeyPath `
        --output $manifest
    if ($LASTEXITCODE -ne 0) { throw "release-manifest signing failed" }
    if (-not (Test-Path -LiteralPath $manifest)) { throw "release manifest output missing: $manifest" }
    Write-Host "[build] signed release manifest: $manifest"
}

Write-Host "[build] done: $zip"
Write-Host "[build] done: $installer"
