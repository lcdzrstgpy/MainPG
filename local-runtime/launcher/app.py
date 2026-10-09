"""MainPG 启动器——入口。

PyInstaller 打包入口：`pyinstaller --name Launcher app.py`。
打包后会生成 Launcher.exe，配合 default_golden.json + 通过环境变量或界面
配置 golden 接口地址。
"""
from __future__ import annotations

import sys

from .ui_window import main

if __name__ == "__main__":
    raise SystemExit(main())
