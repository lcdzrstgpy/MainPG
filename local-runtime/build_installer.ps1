# Build the local workbench distribution (PyInstaller onedir + zip + Inno Setup installer).
# Usage: powershell -ExecutionPolicy Bypass -File build_installer.ps1
# Precondition: gitignored cos.local.json / onebound.local.json exist locally (packed into the dist).
# NOTE: keep this file ASCII-only to avoid PowerShell 5 (ANSI) encoding issues.

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

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

# 4. Copy gitignored local credentials next to the exe (COS image host + OneBound collection API)
Write-Host "[build] copying local credentials into bundle ..."
Copy-Item "wh_local\modules\product_processing\cos.local.json" $dist -ErrorAction Stop
Copy-Item "wh_local\onebound.local.json" $dist -ErrorAction Stop

# 5. Pack a portable zip
Write-Host "[build] creating portable zip ..."
$zip = Join-Path $PSScriptRoot "dist\MainPG-portable.zip"
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
    & $iscc $issBuild
    if ($LASTEXITCODE -ne 0) { throw "Inno Setup compile failed" }
} finally {
    Remove-Item $issBuild -ErrorAction SilentlyContinue
}

Write-Host "[build] done: $zip"
Write-Host "[build] done: $(Join-Path $PSScriptRoot 'dist\MainPG-Setup.exe')"
