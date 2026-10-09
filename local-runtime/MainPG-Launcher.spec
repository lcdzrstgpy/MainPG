# -*- mode: python ; coding: utf-8 -*-
# MainPG-Launcher PyInstaller spec (onefile + windowed).
#
# Deterministic build note:
#   PyInstaller's PySide6 hook and binary dependency analysis may pull ICU DLLs
#   (icudt73.dll / icuuc.dll / icuin*.dll) and an incompatible OpenSSL from the
#   system PATH (e.g. C:\Program Files\PostgreSQL\16\bin). Bundling those foreign
#   binaries makes Qt6Core.dll fail to load with:
#       "DLL load failed while importing QtCore: 找不到指定的程序。"
#   (A Windows ERROR_PROC_NOT_FOUND, error code 127.)
#   The post-Analysis filter below strips such binaries so builds are reproducible.
import os
import re

# ICU data/library DLLs (versioned like icudt73.dll/icuuc73.dll OR bare like icuuc.dll).
_ICU_RE = re.compile(r'^icu[a-z]+\d*\.dll$', re.IGNORECASE)


def _strip_poison_binaries(entry):
    """Return True if a binary (dest, src, typecode) must be removed from the bundle."""
    dest = entry[0] if isinstance(entry[0], str) else ''
    src = entry[1] if isinstance(entry[1], str) else ''
    base = os.path.basename(dest)

    # 1) ICU DLLs: proven to break QtCore load when bundled from a foreign source.
    if _ICU_RE.match(base):
        return True

    # 2) OpenSSL pulled from PostgreSQL 16: incompatible build (different exports).
    if 'PostgreSQL' in src and base in ('libcrypto-3-x64.dll', 'libssl-3-x64.dll'):
        return True

    return False


a = Analysis(
    ['launcher_run.py'],
    pathex=[],
    binaries=[],
    datas=[
        ('launcher/default_golden.json', '.'),
        # 应用图标（「界」字）：运行期用于 setWindowIcon（标题栏/任务栏）。
        ('app-icon.ico', '.'),
        # 侧栏图标字体：_asset_path() 按 `__file__` 相对位置读取，必须保持
        # launcher/assets 目录结构，否则打包后侧栏图标会退化为系统字体兜底。
        ('launcher/assets/iconfont.ttf', 'launcher/assets'),
    ],
    hiddenimports=[
        # Ed25519 签名验证（launcher/update.py）。cryptography 为 Rust 扩展，
        # 显式声明其使用到的子模块，确保 onefile 分析时全部捆绑。
        'cryptography',
        'cryptography.exceptions',
        'cryptography.hazmat.primitives.asymmetric.ed25519',
        'cryptography.hazmat.bindings._rust',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['PySide6.QtQml', 'PySide6.QtQuick', 'PySide6.QtNetwork',
              'PySide6.QtPdf', 'PySide6.QtSvg', 'PySide6.QtOpenGL'],
    noarchive=False,
    optimize=0,
)
a.binaries = [b for b in a.binaries if not _strip_poison_binaries(b)]

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='MainPG-Launcher',
    icon='app-icon.ico',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
