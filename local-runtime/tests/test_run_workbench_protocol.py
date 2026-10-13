"""run_workbench「重新连接」恢复协议：命令行容错、协议拉起命令生成与数据目录固定。"""
from __future__ import annotations

import sys

import run_workbench


def test_build_parser_tolerates_protocol_uri_argument() -> None:
    """协议拉起时系统会在命令行追加 mainpg://... —— 必须被忽略而不是报错退出。"""
    args, extras = run_workbench._build_parser().parse_known_args(
        ["--no-browser", "mainpg://recover/"]
    )
    assert args.no_browser is True
    assert extras == ["mainpg://recover/"]


def test_parser_accepts_explicit_chdir() -> None:
    """源码态协议拉起依赖 --chdir 固定工作目录（数据目录跟随 cwd）。"""
    args, extras = run_workbench._build_parser().parse_known_args(
        ["--no-browser", "--chdir", r"D:\dev\local-runtime", "%1"]
    )
    assert args.chdir == r"D:\dev\local-runtime"
    assert extras == ["%1"]


def test_protocol_open_command_frozen_points_to_exe(monkeypatch) -> None:
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", r"C:\Apps\MainPG\MainPG.exe")
    assert run_workbench._protocol_open_command() == r'"C:\Apps\MainPG\MainPG.exe" --no-browser "%1"'


def test_protocol_open_command_source_pins_workdir(monkeypatch) -> None:
    monkeypatch.delattr(sys, "frozen", raising=False)
    monkeypatch.setattr(sys, "executable", r"C:\Python\python.exe")
    monkeypatch.setattr(run_workbench, "_windowless_python", lambda: r"C:\Python\pythonw.exe")
    command = run_workbench._protocol_open_command()
    assert command.startswith(r'"C:\Python\pythonw.exe" ')
    assert "run_workbench.py" in command
    assert "--no-browser" in command
    assert '--chdir "' in command
    assert command.endswith('"%1"')
    assert "local-runtime" in command
