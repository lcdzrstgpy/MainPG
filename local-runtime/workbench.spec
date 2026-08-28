# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包配置：本地工作台桌面版（onedir）。

构建步骤（见 build_installer.ps1）：
    1. 先执行 web-frontend 的 `npm run build` 生成 dist；
    2. `python -m PyInstaller workbench.spec --noconfirm --clean`；
    3. 复制图标，但绝不复制 gitignored 的本地凭据、数据库或用户产物；
    4. 压缩 dist\\MainPG 得到安装包 zip。

关键打包点：
- 迁移 SQL：运行期按 `Path(__file__)` 相对位置读取，必须按原目录结构打进包；
- 前端 dist：打进 _internal\\web-frontend\\dist（_frontend_dist_dir 优先从 _MEIPASS 读取）；
- 第三方包：uvicorn / qcloud_cos / rapidocr_onnxruntime / onnxruntime / openpyxl 收集完整
  （含数据文件与 DLL，避免动态加载缺失）。
"""
from pathlib import Path

from PyInstaller.utils.hooks import collect_all

ROOT = Path(SPECPATH)  # local-runtime
REPO = ROOT.parent

datas: list = []
binaries: list = []
hiddenimports: list = []

# 第三方包完整收集（datas/binaries/hiddenimports）
for _pkg in ("uvicorn", "qcloud_cos", "rapidocr_onnxruntime", "onnxruntime", "openpyxl", "PIL"):
    _d, _b, _h = collect_all(_pkg)
    datas += _d
    binaries += _b
    hiddenimports += _h

# 运行期按 Path(__file__) 相对位置读取的迁移 SQL（必须按原目录结构打进包）
for _rel in (
    "wh_local/data_collection/migrations",
    "wh_local/modules/ai_service/migrations",
    "wh_local/modules/pod_customization/migrations",
    "wh_local/modules/product_processing/migrations",
    "wh_local/modules/profit_activity/migrations",
    "wh_local/modules/combo_kit/migrations",
    "wh_local/price_verification/migrations",
):
    _src = ROOT / _rel
    if _src.is_dir():
        datas.append((str(_src), _rel))
    else:
        print(f"[workbench.spec] WARNING: 缺失 {_rel}")

# 前端构建产物（web-frontend/dist → _internal/web-frontend/dist）
_frontend_dist = REPO / "web-frontend" / "dist"
if _frontend_dist.is_dir():
    datas.append((str(_frontend_dist), "web-frontend/dist"))
else:
    print("[workbench.spec] WARNING: web-frontend/dist 不存在，请先执行 npm run build")

# Optional low-resource distractor detector (person/cat/dog). Missing model is
# safe at runtime, but official builds include it for reference-image cleanup.
_distractor_models = ROOT / "wh_local" / "price_verification" / "sourcing" / "models"
if _distractor_models.is_dir():
    datas.append((str(_distractor_models), "wh_local/price_verification/sourcing/models"))

a = Analysis(
    [str(ROOT / "run_workbench.py")],
    pathex=[str(ROOT)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="MainPG",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    icon=str(ROOT / "app-icon.ico"),
    # 正式交付使用 windowed（无黑色控制台窗口），日志不影响功能
    console=False,
    disable_windowed_traceback=False,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="MainPG",
)
