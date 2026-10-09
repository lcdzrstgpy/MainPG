# ============================================================================
# MainPG-Launcher build + sign script
# ----------------------------------------------------------------------------
# Usage (PowerShell):
#   .\build_launcher.ps1 -Version 1.0.0
#   .\build_launcher.ps1 -Version 1.0.0 -SignKey <key> -SignPassword <pwd>
#
# Notes:
#   * PyInstaller onefile + windowed, bundles launcher/default_golden.json
#   * Output: dist\MainPG-Launcher-<Version>.exe (after sign copies to dist\MainPG-Launcher.exe)
#   * If no sign args passed, skip signing (local debug). Priority: args > env EVSIGN_KEY / EVSIGN_PASSWORD
# ============================================================================
param(
    [string]$Version = "1.0.0",
    [string]$SignKey = "",
    [string]$SignPassword = "",
    [string]$Py = ""
)

$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ScriptDir

# Pick a Python that has BOTH PySide6 and PyInstaller installed.
function Resolve-LauncherPython([string]$Wanted) {
    $exes = @()
    if ($Wanted) { $exes += ,$Wanted }
    $exes += @(
        "C:\Users\HUAWEI\AppData\Local\Programs\Python\Python311\python.exe",
        "python"
    )
    foreach ($exe in $exes) {
        $probe = "import PySide6, PyInstaller"
        & $exe -c $probe 2>$null | Out-Null
        if ($LASTEXITCODE -eq 0) { return $exe }
    }
    throw "No Python with PySide6 + PyInstaller found. Pass -Py <path>."
}
$Py = Resolve-LauncherPython $Py
Write-Host "Using interpreter: $Py"

$SpecName = "MainPG-Launcher"
$OutExe = Join-Path $ScriptDir "dist\$SpecName.exe"
$OutVer = Join-Path $ScriptDir "dist\$SpecName-$Version.exe"

Write-Host "== 1. Clean old build/dist ==" -ForegroundColor Cyan
$BuildDir = Join-Path $ScriptDir "build\$SpecName"
if (Test-Path $BuildDir) { Remove-Item -Recurse -Force $BuildDir }
New-Item -ItemType Directory -Force -Path (Join-Path $ScriptDir "dist") | Out-Null

Write-Host "== 2. PyInstaller build (from spec, deterministic ICU/OpenSSL filter) ==" -ForegroundColor Cyan
& $Py -m PyInstaller `
    --noconfirm --clean `
    --distpath "dist" --workpath "build" `
    "MainPG-Launcher.spec"
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed, exit code $LASTEXITCODE" }

Write-Host "== 3. Rename to versioned file ==" -ForegroundColor Cyan
Copy-Item -Force $OutExe $OutVer

if (-not $SignKey) { $SignKey = $env:EVSIGN_KEY }
if (-not $SignPassword) { $SignPassword = $env:EVSIGN_PASSWORD }

if ($SignKey -and $SignPassword) {
    Write-Host "== 4. evsign sign (force close rename then sign) ==" -ForegroundColor Cyan
    Copy-Item -Force $OutVer "$OutVer.unsigned"
    evsign-client "$OutVer" -key $SignKey $SignPassword
    if ($LASTEXITCODE -ne 0) { throw "evsign signing failed, exit code $LASTEXITCODE" }
    Copy-Item -Force $OutVer $OutExe
} else {
    Write-Host "== 4. No sign key provided, skip signing (local debug) ==" -ForegroundColor Yellow
}

Write-Host "Done: $OutVer" -ForegroundColor Green
if (Test-Path $OutVer) {
    $sha = (Get-FileHash -Algorithm SHA256 -Path $OutVer).Hash
    Write-Host "SHA256: $sha" -ForegroundColor Cyan
}
