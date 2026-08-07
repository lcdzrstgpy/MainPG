# 构建本地工作台安装包（PyInstaller onedir + zip）。
# 用法：powershell -ExecutionPolicy Bypass -File build_installer.ps1
# 前提：本地存在 gitignored 的 cos.local.json / onebound.local.json（打包进安装包）。

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

# 1. 构建前端
Write-Host "[build] 构建 web-frontend ..."
Push-Location ..\web-frontend
npm run build
if ($LASTEXITCODE -ne 0) { throw "前端构建失败" }
Pop-Location

# 2. 安装/确认 PyInstaller
python -m pip show pyinstaller *> $null
if ($LASTEXITCODE -ne 0) {
    Write-Host "[build] 安装 PyInstaller ..."
    python -m pip install pyinstaller
}

# 3. PyInstaller 打包（onedir → dist\MainPG）
Write-Host "[build] PyInstaller 打包 ..."
python -m PyInstaller workbench.spec --noconfirm --clean
if ($LASTEXITCODE -ne 0) { throw "PyInstaller 打包失败" }

$dist = Join-Path $PSScriptRoot "dist\MainPG"
if (-not (Test-Path $dist)) { throw "打包产物不存在: $dist" }

# 4. 复制 gitignored 本地凭据到 exe 同目录（COS 图床 + OneBound 采集 API）
Write-Host "[build] 复制本地凭据到产物 ..."
Copy-Item "wh_local\modules\product_processing\cos.local.json" $dist -ErrorAction Stop
Copy-Item "wh_local\onebound.local.json" $dist -ErrorAction Stop

# 5. 压缩为安装包 zip
Write-Host "[build] 压缩安装包 ..."
$zip = Join-Path $PSScriptRoot "dist\MainPG-安装包.zip"
if (Test-Path $zip) { Remove-Item $zip }
Compress-Archive -Path $dist -DestinationPath $zip -CompressionLevel Optimal

Write-Host "[build] 完成: $zip"
