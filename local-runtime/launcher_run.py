"""PyInstaller 打包入口：以包方式导入 launcher.app，保证相对导入可用。

用法（可由 build_launcher.ps1 调用）：
    pyinstaller --noconfirm --clean --onefile --windowed --name MainPG-Launcher \
        --add-data "launcher/default_golden.json;." launcher_run.py
"""
from __future__ import annotations

import sys

from launcher.app import main

if __name__ == "__main__":
    raise SystemExit(main())
