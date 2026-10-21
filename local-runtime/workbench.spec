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
# 说明：uvicorn 在运行期会惰性加载 websockets/wsproto，若不显式 collect 会导致
# 打包产物启动时出现 "Unsupported upgrade request" 警告，故补充进收集列表。
# 注意 onnxruntime：这里按「导入包名」收集，收的就是构建机上当前装的那个发行版。
# Windows 构建机在 build_installer.ps1 第 3 步装了 onnxruntime-directml（DirectML GPU
# 加速，N/A/I 通吃、免 CUDA），所以 Windows 包内置 GPU 版；mac 构建机仍是 CPU 版。
# 运行期选哪个后端见 wh_local/media_enhance.py 的 _select_providers()。
for _pkg in ("uvicorn", "qcloud_cos", "rapidocr_onnxruntime", "onnxruntime", "openpyxl", "PIL", "websockets", "wsproto"):
    _d, _b, _h = collect_all(_pkg)
    datas += _d
    binaries += _b
    hiddenimports += _h

# 运行期按 Path(__file__) 相对位置读取的迁移 SQL（必须按原目录结构打进包）
for _rel in (
    "wh_local/data_collection/migrations",
    "wh_local/modules/ai_service/migrations",
    "wh_local/modules/help_agent/migrations",
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

# 操作答疑 FAQ 数据（service.faq_data_path() 按 __file__ / _MEIPASS 相对位置读取）
_help_agent_data = ROOT / "wh_local/modules/help_agent/data"
if _help_agent_data.is_dir():
    datas.append((str(_help_agent_data), "wh_local/modules/help_agent/data"))
else:
    print("[workbench.spec] WARNING: 操作答疑 FAQ 数据目录不存在")

# 妙手导入官方模板（妙手导出按 Path(__file__) 相对位置读取）。只打包官方模板本身，
# generated/ 下的精简缓存由运行期首次导出时自动生成，不随包分发。
_ms_templates = ROOT / "wh_local/modules/product_processing/templates"
if _ms_templates.is_dir():
    for _tpl in _ms_templates.glob("*.xlsx"):
        datas.append((str(_tpl), "wh_local/modules/product_processing/templates"))
else:
    print("[workbench.spec] WARNING: 妙手导入模板目录不存在")

# 前端构建产物（web-frontend/dist → _internal/web-frontend/dist）
_frontend_dist = REPO / "web-frontend" / "dist"
if _frontend_dist.is_dir():
    datas.append((str(_frontend_dist), "web-frontend/dist"))
else:
    print("[workbench.spec] WARNING: web-frontend/dist 不存在，请先执行 npm run build")

# 启动卡片底图（launcher/splash_window.py 按 _MEIPASS 相对位置读取）。
# 只用一张静态图：卡片用 PIL 逐帧绘制并整体浮动，不需要视频解码（PIL 也解不了 mp4）。
_splash_assets = ROOT / "launcher" / "assets" / "splash"
if _splash_assets.is_dir():
    datas.append((str(_splash_assets), "launcher/assets/splash"))
else:
    print("[workbench.spec] WARNING: 启动卡片素材目录不存在（launcher/assets/splash）")

# 启动卡片依赖 PySide6（launcher/splash_qt.py）。缺了不会让打包失败 ——
# 运行时会静默回落成「等 2.5 秒开浏览器」，但用户看到的是白屏空窗，等于这个
# 功能白做。PyInstaller 对缺失的 import 也不报错，所以这里必须自己把关。
import sys as _build_sys

try:
    import PySide6  # noqa: F401

    # 只 import PySide6 不够：1.5.8 就是这样过了检查、到用户机器上却不起卡片。
    # 真正用到的是 QtWidgets，它要连 Qt6*.dll 与 shiboken6 一起装载成功才算数。
    from PySide6 import QtWidgets  # noqa: F401

    print(f"[workbench.spec] PySide6 {PySide6.__version__} 可用"
          f"（python {_build_sys.version.split()[0]}，QtWidgets 装载成功），启动卡片随包分发")
except Exception as _exc:  # noqa: BLE001 - 打包机环境缺失要显式告警
    print("=" * 72)
    print("[workbench.spec] !! PySide6 不可用 —— 启动卡片将不会生效！")
    print(f"[workbench.spec] !! 失败原因: {type(_exc).__name__}: {_exc}")
    print(f"[workbench.spec] !! 构建用的 Python: {_build_sys.version.split()[0]}")
    print("[workbench.spec] !! 请在构建机、用同一个 Python 执行:  python -m pip install PySide6")
    print("[workbench.spec] !! 然后确认 `python -c \"from PySide6 import QtWidgets\"` 不报错")
    print("[workbench.spec] !! 否则打出的包双击后要白等 2.5 秒才开浏览器")
    print("=" * 72)

# 启动卡片模块是在 run_workbench._start_splash() 里延迟导入的，显式声明一次，
# 免得静态分析漏掉 → 打包后卡片永远起不来（静默回落成「等 2.5 秒开浏览器」）。
hiddenimports.append("launcher.splash_qt")

# Optional low-resource distractor detector (person/cat/dog). Missing model is
# safe at runtime, but official builds include it for reference-image cleanup.
_distractor_models = ROOT / "wh_local" / "price_verification" / "sourcing" / "models"
if _distractor_models.is_dir():
    datas.append((str(_distractor_models), "wh_local/price_verification/sourcing/models"))

# POD 交付图清晰化模型（Real-ESRGAN 4× 超分）。media_enhance._model_path 按 __file__/_MEIPASS
# 相对位置读取；缺失时只做 USM 锐化，不影响生图。
_enhance_models = ROOT / "wh_local" / "models"
if _enhance_models.is_dir():
    datas.append((str(_enhance_models), "wh_local/models"))
else:
    print("[workbench.spec] WARNING: wh_local/models 不存在，清晰化将只做 USM 锐化")

# 启动卡片是在 launcher/splash_qt.py 里用 PySide6 画的（无边框透明窗口）。
# 复用 launcher/MainPG-Launcher.spec 的裁剪思路，排除没用到的大模块，
# 否则 PySide6 全家桶会让安装包白白多出几十 MB。
_PYSIDE_EXCLUDES = [
    "PySide6.QtQml", "PySide6.QtQuick", "PySide6.QtNetwork",
    "PySide6.QtPdf", "PySide6.QtSvg", "PySide6.QtOpenGL",
    "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets",
    "PySide6.QtMultimedia", "PySide6.QtCharts", "PySide6.QtDataVisualization",
]

a = Analysis(
    [str(ROOT / "run_workbench.py")],
    pathex=[str(ROOT)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=_PYSIDE_EXCLUDES,
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
