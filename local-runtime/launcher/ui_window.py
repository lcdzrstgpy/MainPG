"""Launcher GUI（PySide6）— 现代侧边栏式启动器。

采用「左侧深色导航栏 + 右侧浅色内容区」布局，四个功能页通过 QStackedWidget 切换：
  1. 本地环境监测：只告诉用户「就绪 / 未就绪」，逐条输出检查项。
  2. 本地文件资源缓存：扫描本地个人资产生成物，可清理 / 导出 / 导入。
  3. 版本更新检查：查询当前版本，非最新则去官网下载最新版。
  4. 本地报错日志上传：用户账户/密码登录后，将本地 runtime.log 上报到服务器。

主界面左侧底部提供「一键启动本地程序」按钮。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

from PySide6.QtCore import Qt, QThread, QTimer, Signal
from PySide6.QtGui import QColor, QTextCursor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)
from PySide6 import QtWidgets

from . import console, core, logupload, update

STATUS_COLOR = {
    "ok": "#2e7d32",
    "warn": "#b06a00",
    "fail": "#c62828",
    "skip": "#757575",
}

ACCENT = "#2f6fed"
ACCENT_HOVER = "#2456c8"
ACCENT_PRESSED = "#1f4bb0"
TEXT = "#1f2d3d"
DIM = "#8a919d"
BG = "#f4f6f9"
CARD_BORDER = "#e9edf3"
SIDEBAR_BG = "#1b2231"
SIDEBAR_HOVER = "#273043"
SIDEBAR_ACTIVE = "#2f6fed"

# 侧边栏导航项（图标 + 标题）
NAV_ITEMS = [
    ("◉", "本地环境监测"),
    ("▤", "本地文件资源"),
    ("⟳", "版本更新检查"),
    ("⇧", "日志上报"),
]

# --------------------------- 后台线程 --------------------------------------- #


class CheckWorker(QThread):
    """后台执行体检，避免阻塞 UI。"""

    done = Signal(object)   # core.LauncherReport
    error = Signal(str)

    def __init__(self, golden_url: str) -> None:
        super().__init__()
        self.golden_url = golden_url

    def run(self) -> None:  # noqa: D102
        try:
            report = core.build_report(server_golden_url=self.golden_url)
            probe_checks, _engine = core.probe_product()
            report.checks.extend(probe_checks)
            self.done.emit(report)
        except Exception as exc:  # noqa: BLE001 - 用户直接看到原因
            self.error.emit(repr(exc))


class ConsoleScanWorker(QThread):
    """后台扫描资源占用，避免阻塞 UI。"""

    done = Signal(object)   # list[dict] 统计结果
    error = Signal(str)

    def run(self) -> None:  # noqa: D102
        try:
            self.done.emit(console.scan())
        except Exception as exc:  # noqa: BLE001 - 用户直接看到原因
            self.error.emit(repr(exc))


class UpdateCheckWorker(QThread):
    """后台检测更新（拉取 manifest + 签名验证 + 版本比较），避免阻塞 UI。"""

    done = Signal(object)   # update.UpdateRelease | None
    error = Signal(str)

    def run(self) -> None:  # noqa: D102
        try:
            self.done.emit(update.check_for_update(timeout=12.0))
        except Exception as exc:  # noqa: BLE001 - 用户直接看到原因
            self.error.emit(repr(exc))


class UpdateDownloadWorker(QThread):
    """后台下载并校验安装包，回调进度后再拉起安装器。"""

    done = Signal(str)                  # 本地安装包路径
    progress = Signal(int, int, float)  # downloaded, total, percentage
    error = Signal(str)

    def __init__(self, release: update.UpdateRelease) -> None:
        super().__init__()
        self._release = release

    def run(self) -> None:  # noqa: D102
        try:
            path = update.download_release(
                self._release, on_progress=self._on_progress
            )
            self.done.emit(str(path))
        except Exception as exc:  # noqa: BLE001 - 用户直接看到原因
            self.error.emit(repr(exc))

    def _on_progress(self, downloaded: int, total: int | None, pct: float | None) -> None:
        self.progress.emit(downloaded, int(total) if total else 0, pct if pct is not None else 0.0)


class LogLoginWorker(QThread):
    """后台登录，换取 remote_token。"""

    done = Signal(object)  # (token, account)
    error = Signal(str)

    def __init__(self, username: str, password: str) -> None:
        super().__init__()
        self.username = username
        self.password = password

    def run(self) -> None:  # noqa: D102
        try:
            token, account = logupload.login(self.username, self.password)
            self.done.emit((token, account))
        except Exception as exc:  # noqa: BLE001 - 用户直接看到原因
            self.error.emit(str(exc))


class LogUploadWorker(QThread):
    """后台上传日志，回调进度文本后返回结果。"""

    log = Signal(str)
    done = Signal(object)   # dict 服务器返回
    error = Signal(str)

    def __init__(self, token: str, log_path: Path) -> None:
        super().__init__()
        self.token = token
        self.log_path = log_path

    def run(self) -> None:  # noqa: D102
        try:
            self.log.emit(f"正在读取日志：{self.log_path.name}")
            result = logupload.upload_log(self.token, self.log_path)
            self.done.emit(result)
        except Exception as exc:  # noqa: BLE001 - 用户直接看到原因
            self.error.emit(str(exc))


def _fmt_bytes(size: int) -> str:
    return console._human(size)


def _friendly_key(key: str) -> str:
    k = key
    for prefix, label in (("limit.", "并发/限流 "), ("update.", "更新 "),
                          ("cap.text_ai", "文本 AI "), ("cap.image_ai", "图片 AI "),
                          ("cap.ocr", "OCR "), ("cap.", "能力 "),
                          ("dep.", "依赖 "), ("net.", "网络 "), ("engine.", "引擎 "),
                          ("port.", "端口 "), ("cos.", "COS "),
                          ("db.", "数据 "), ("config.", "配置 ")):
        if k.startswith(prefix):
            rest = k[len(prefix):]
            if rest.endswith(".reason"):
                rest = rest[: -len(".reason")] + "·原因"
            return label + rest
    return k


# ------------------------------- 主窗口 -------------------------------------- #


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("MainPG 启动器")
        self.setWindowIcon(QtWidgets.QApplication.windowIcon())
        self.resize(1080, 760)
        self.setMinimumSize(960, 660)

        self._worker: CheckWorker | None = None
        self._console_worker: ConsoleScanWorker | None = None
        self._update_worker: UpdateCheckWorker | None = None
        self._update_download_worker: UpdateDownloadWorker | None = None
        self._login_worker: LogLoginWorker | None = None
        self._upload_worker: LogUploadWorker | None = None
        self._update_release: update.UpdateRelease | None = None
        self._report: core.LauncherReport | None = None
        self._remote_token: str | None = None
        self._golden_url = core.golden_url_resolve()
        self._check_lines: list[str] = []
        self._check_timer = QTimer(self)
        self._check_timer.setInterval(40)
        self._check_timer.timeout.connect(self._reveal_check_line)

        self._build_ui()
        self.run_check()
        self.run_update_check()

    # ---------------------------------------------------------------- UI 总装
    def _build_ui(self) -> None:
        central = QWidget()
        central.setObjectName("central")
        central.setStyleSheet(f"#central{{background:{BG};}}")
        self.setCentralWidget(central)

        main = QHBoxLayout(central)
        main.setContentsMargins(0, 0, 0, 0)
        main.setSpacing(0)

        main.addWidget(self._build_sidebar())

        right = QVBoxLayout()
        right.setContentsMargins(0, 0, 0, 0)
        right.setSpacing(0)
        self.stack = QStackedWidget()
        self.stack.setStyleSheet(f"QStackedWidget{{background:{BG};}}")
        self.stack.addWidget(self._build_env_page())
        self.stack.addWidget(self._build_resource_page())
        self.stack.addWidget(self._build_update_page())
        self.stack.addWidget(self._build_log_page())
        right.addWidget(self.stack, 1)
        right.addWidget(self._build_update_bar())
        main.addLayout(right, 1)

    def _build_sidebar(self) -> QFrame:
        side = QFrame()
        side.setObjectName("sidebar")
        side.setFixedWidth(204)
        side.setStyleSheet(
            f"#sidebar{{background:{SIDEBAR_BG};}}"
        )
        lay = QVBoxLayout(side)
        lay.setContentsMargins(14, 22, 14, 16)
        lay.setSpacing(4)

        brand = QLabel("MainPG")
        brand.setStyleSheet("font-size:20px;font-weight:800;color:#ffffff;")
        brand.setContentsMargins(6, 0, 0, 0)
        lay.addWidget(brand)
        brand_sub = QLabel("启动器 · Launcher")
        brand_sub.setStyleSheet("font-size:11px;color:#7f8aa0;")
        brand_sub.setContentsMargins(6, 0, 0, 0)
        lay.addWidget(brand_sub)
        lay.addSpacing(20)

        self._nav_buttons: list[QPushButton] = []
        for idx, (icon, text) in enumerate(NAV_ITEMS):
            btn = QPushButton(f"{icon}   {text}")
            btn.setCheckable(True)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setFixedHeight(40)
            btn.setStyleSheet(
                f"QPushButton{{color:#aeb6c2;background:transparent;border:none;"
                f"border-radius:8px;padding:0 14px;font-size:13px;text-align:left;}}"
                f"QPushButton:hover{{background:{SIDEBAR_HOVER};color:#e8edf4;}}"
                f"QPushButton:checked{{background:{SIDEBAR_ACTIVE};color:#ffffff;font-weight:600;}}"
            )
            btn.clicked.connect(lambda _=False, i=idx: self._switch_page(i))
            lay.addWidget(btn)
            self._nav_buttons.append(btn)

        lay.addStretch(1)

        self.btn_launch = QPushButton("▶  一键启动本地程序")
        self.btn_launch.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_launch.setFixedHeight(40)
        self.btn_launch.setStyleSheet(
            f"QPushButton{{background:{ACCENT};color:#fff;border:none;border-radius:8px;"
            f"padding:0 16px;font-size:13px;font-weight:600;}}"
            f"QPushButton:hover{{background:{ACCENT_HOVER};}}"
            f"QPushButton:pressed{{background:{ACCENT_PRESSED};}}"
            f"QPushButton:disabled{{background:#3d4a5f;}}"
        )
        self.btn_launch.clicked.connect(self.on_start)
        lay.addWidget(self.btn_launch)

        # 默认选中第一页
        if self._nav_buttons:
            self._nav_buttons[0].setChecked(True)
        return side

    def _switch_page(self, idx: int) -> None:
        self.stack.setCurrentIndex(idx)
        for i, b in enumerate(self._nav_buttons):
            b.setChecked(i == idx)

    def _build_update_bar(self) -> QFrame:
        bar = QFrame()
        bar.setStyleSheet("QFrame{background:#ffffff;border-top:1px solid #e6e9ee;}")
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(20, 8, 20, 8)
        lay.setSpacing(10)
        self.lbl_update = QLabel("启动自检更新…")
        self.lbl_update.setStyleSheet(f"color:{DIM};font-size:12px;")
        self.update_progress = QProgressBar()
        self.update_progress.setTextVisible(False)
        self.update_progress.setRange(0, 0)
        self.update_progress.setFixedHeight(6)
        self.update_progress.setStyleSheet(
            "QProgressBar{background:#eef1f5;border:none;border-radius:3px;}"
            "QProgressBar::chunk{background:#2f6fed;border-radius:3px;}"
        )
        self.update_progress.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed
        )
        lay.addWidget(self.lbl_update, 1)
        lay.addWidget(self.update_progress, 1)
        return bar

    # ------------------------------- 页面容器 ---------------------------- #
    def _page(self, title: str, subtitle: str) -> tuple[QWidget, QVBoxLayout]:
        """创建一个浅色内容页，内部为一张白色卡片。"""
        page = QWidget()
        page.setStyleSheet(f"QWidget{{background:{BG};}}")
        outer = QVBoxLayout(page)
        outer.setContentsMargins(22, 20, 22, 20)
        frame = QFrame()
        frame.setStyleSheet(
            f"QFrame{{background:#ffffff;border:1px solid {CARD_BORDER};border-radius:14px;}}"
        )
        lay = QVBoxLayout(frame)
        lay.setContentsMargins(22, 20, 22, 20)
        lay.setSpacing(12)
        head = QLabel(title)
        head.setStyleSheet(f"font-size:17px;font-weight:700;color:{TEXT};")
        lay.addWidget(head)
        sub = QLabel(subtitle)
        sub.setStyleSheet(f"font-size:12px;color:{DIM};")
        sub.setWordWrap(True)
        lay.addWidget(sub)
        outer.addWidget(frame)
        return page, lay

    # --------------------------- 板块一：环境监测 -------------------------- #
    def _build_env_page(self) -> QWidget:
        page, lay = self._page(
            "本地环境监测",
            "启动前检查依赖、配置与网络连通性，只提示是否就绪，不展示具体配置细节。",
        )

        status_row = QHBoxLayout()
        self.env_badge = QLabel("检测中…")
        self.env_badge.setStyleSheet(
            "font-size:13px;font-weight:700;color:#8a919d;"
            "background:#f4f6f9;border-radius:8px;padding:5px 12px;"
        )
        status_row.addWidget(self.env_badge)
        status_row.addStretch(1)
        self.btn_env_run = QPushButton("重新检测")
        self.btn_env_run.setFixedHeight(32)
        self.btn_env_run.setStyleSheet(_secondary_button())
        self.btn_env_run.clicked.connect(self.run_check)
        status_row.addWidget(self.btn_env_run)
        lay.addLayout(status_row)

        self.env_log = QTextEdit()
        self.env_log.setReadOnly(True)
        self.env_log.setFrameShape(QFrame.Shape.NoFrame)
        self.env_log.setStyleSheet(
            "QTextEdit{background:#12151c;color:#cfd6e0;border-radius:10px;"
            "padding:12px;font-family:Consolas,'Courier New';font-size:12px;}"
        )
        self.env_log.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        lay.addWidget(self.env_log, 1)
        return page

    def run_check(self) -> None:
        self._check_lines = []
        self.env_log.clear()
        self._append_env_log("开始环境体检…", "#cfd6e0")
        self._set_env_badge("检测中…", "#8a919d")
        self.btn_env_run.setEnabled(False)
        self._worker = CheckWorker(self._golden_url)
        self._worker.done.connect(self._on_check_done)
        self._worker.error.connect(self._on_check_error)
        self._worker.start()

    def _on_check_done(self, report: core.LauncherReport) -> None:
        self._report = report
        ok = len(report.ok)
        warn = len(report.warned)
        fail = len(report.failed)
        if fail == 0 and warn == 0:
            badge, color = "✓ 环境就绪", "#2e7d32"
            bg = "#e8f5ec"
        elif fail == 0:
            badge, color = "⚠ 基本可用，有提醒", "#b06a00"
            bg = "#fdf6e8"
        else:
            badge, color = "✕ 环境未就绪", "#c62828"
            bg = "#fdeaea"
        self._set_env_badge(badge, color, bg)

        lines: list[tuple[str, str]] = []
        for c in report.checks:
            lines.append(self._check_line(c))
        lines.append(("", ""))
        lines.append((f"共 {len(report.checks)} 项：通过 {ok} · 提醒 {warn} · 失败 {fail}", "#cfd6e0"))
        lines.append(("", ""))
        # 动画逐条输出
        self._pending_lines = lines
        self._check_timer.start()
        self.btn_env_run.setEnabled(True)

    def _check_line(self, c: core.CheckResult) -> tuple[str, str]:
        sym, color = {"ok": ("✔", "#57c07e"), "warn": ("⚠", "#e5a83c"),
                      "fail": ("✘", "#e5736a")}.get(c.status, ("·", "#8a919d"))
        label = _friendly_key(c.key)
        status = {"ok": "就绪", "warn": "提醒", "fail": "未就绪"}.get(c.status, c.status)
        return f"{sym} {label} · {status}", color

    def _reveal_check_line(self) -> None:
        if not getattr(self, "_pending_lines", None):
            self._check_timer.stop()
            return
        text, color = self._pending_lines.pop(0)
        if text:
            self._append_env_log(text, color)
        else:
            self._append_env_log("", "#cfd6e0")
        if not self._pending_lines:
            self._check_timer.stop()

    def _on_check_error(self, msg: str) -> None:
        self._check_timer.stop()
        self.btn_env_run.setEnabled(True)
        self._set_env_badge("检测失败", "#c62828", "#fdeaea")
        self._append_env_log(f"环境体检出错：{msg}", "#e5736a")
        QMessageBox.critical(self, "体检出错", msg)

    def _set_env_badge(self, text: str, color: str, bg: str = "") -> None:
        self.env_badge.setText(text)
        self.env_badge.setStyleSheet(
            f"font-size:13px;font-weight:700;color:{color};"
            f"background:{bg or '#f4f6f9'};border-radius:8px;padding:5px 12px;"
        )

    def _append_env_log(self, text: str, color: str) -> None:
        cursor = self.env_log.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        html = f"<span style='color:{color};'>{_esc(text)}</span><br>"
        cursor.insertHtml(html)
        self.env_log.setTextCursor(cursor)
        self.env_log.ensureCursorVisible()

    # --------------------------- 板块二：资源管理 -------------------------- #
    def _build_resource_page(self) -> QWidget:
        page, lay = self._page(
            "本地文件资源",
            "管理产品处理 / POD / 产品库等个人资产，可清理、导出或导入。首次打开会自动扫描本地占用。",
        )

        self.res_total = QLabel("扫描中…")
        self.res_total.setStyleSheet(f"font-size:14px;font-weight:700;color:{TEXT};")
        self.res_hint = QLabel("正在统计各模块本地资产占用…")
        self.res_hint.setStyleSheet(f"font-size:11px;color:{DIM};")
        lay.addWidget(self.res_total)
        lay.addWidget(self.res_hint)

        self.res_table = QTableWidget(0, 4)
        self.res_table.setHorizontalHeaderLabels(["类别", "文件数", "占用", "数据行"])
        self.res_table.setAlternatingRowColors(True)
        self.res_table.setShowGrid(False)
        self.res_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.res_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.res_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.res_table.verticalHeader().setVisible(False)
        self.res_table.verticalHeader().setDefaultSectionSize(34)
        header = self.res_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        self.res_table.setStyleSheet(_table_style())
        lay.addWidget(self.res_table, 1)

        row = QHBoxLayout()
        row.setSpacing(8)
        self.btn_res_refresh = QPushButton("刷新")
        self.btn_res_clean = QPushButton("清理选中")
        self.btn_res_export = QPushButton("导出")
        self.btn_res_import = QPushButton("导入")
        for b in (self.btn_res_refresh, self.btn_res_clean,
                  self.btn_res_export, self.btn_res_import):
            b.setFixedHeight(32)
            b.setStyleSheet(_secondary_button())
            row.addWidget(b)
        row.addStretch(1)
        lay.addLayout(row)

        self.btn_res_refresh.clicked.connect(self.console_scan)
        self.btn_res_clean.clicked.connect(self.console_clean)
        self.btn_res_export.clicked.connect(self.console_export)
        self.btn_res_import.clicked.connect(self.console_import)
        self.console_scan()
        return page

    def console_scan(self) -> None:
        self.res_total.setText("扫描中…")
        self.res_hint.setText("正在统计各模块本地资产占用…")
        self.btn_res_refresh.setEnabled(False)
        self._console_worker = ConsoleScanWorker()
        self._console_worker.done.connect(self._on_console_scan)
        self._console_worker.error.connect(self._on_console_error)
        self._console_worker.start()

    def _on_console_scan(self, stats: list) -> None:
        self.res_table.setRowCount(0)
        total_files = 0
        total_bytes = 0
        for s in stats:
            row = self.res_table.rowCount()
            self.res_table.insertRow(row)

            name_item = QTableWidgetItem(s["name"])
            name_item.setData(Qt.ItemDataRole.UserRole, s["id"])
            name_item.setFlags(Qt.ItemFlag.ItemIsEnabled
                               | Qt.ItemFlag.ItemIsUserCheckable
                               | Qt.ItemFlag.ItemIsSelectable)
            name_item.setCheckState(Qt.CheckState.Unchecked)
            if not s.get("exists"):
                name_item.setForeground(QColor("#9e9e9e"))

            if s["id"] == "cache":
                file_item = QTableWidgetItem(str(s.get("cache_items", 0)))
                row_item = QTableWidgetItem("—")
            else:
                file_item = QTableWidgetItem(str(s.get("file_count", 0)))
                rows = s.get("rows", 0)
                row_item = QTableWidgetItem(str(rows) if s.get("table_count") else "—")
            size_item = QTableWidgetItem(_fmt_bytes(s.get("byte_size", 0)))

            self.res_table.setItem(row, 0, name_item)
            self.res_table.setItem(row, 1, file_item)
            self.res_table.setItem(row, 2, size_item)
            self.res_table.setItem(row, 3, row_item)

            total_files += s.get("file_count", 0) + s.get("cache_items", 0)
            total_bytes += s.get("byte_size", 0)

        running = console.is_product_running()
        self.res_total.setText(f"可管理资源：{total_files} 个文件 / 约 {_fmt_bytes(total_bytes)}")
        if running:
            self.res_hint.setText("主程序运行中：仅删文件，数据库行需退出后清扫；可直接导入。")
        else:
            self.res_hint.setText("主程序未运行：可完整清理；可直接导入备份。")
        self.btn_res_refresh.setEnabled(True)

    def _on_console_error(self, msg: str) -> None:
        self.btn_res_refresh.setEnabled(True)
        self.res_total.setText("扫描出错")
        self.res_hint.setText(msg)
        QMessageBox.critical(self, "扫描出错", msg)

    def _selected_console_ids(self) -> list[str]:
        ids: list[str] = []
        for row in range(self.res_table.rowCount()):
            item = self.res_table.item(row, 0)
            if item and item.checkState() == Qt.CheckState.Checked:
                cat_id = item.data(Qt.ItemDataRole.UserRole)
                if cat_id:
                    ids.append(str(cat_id))
        return ids

    def console_clean(self) -> None:
        ids = self._selected_console_ids()
        if not ids:
            QMessageBox.information(self, "清理", "请先在列表中勾选要清理的类别。")
            return
        ok = QMessageBox.question(
            self, "确认清理",
            f"确定清理选中的 {len(ids)} 个类别？\n\n"
            f"将删除对应资产文件与数据库记录，此操作不可撤销。\n"
            f"主程序运行中仅删文件、不删数据库行。\n\n"
            f"建议先「导出」做备份。是否继续？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if ok != QMessageBox.StandardButton.Yes:
            return
        result = console.clean(ids)
        cleaned = result.get("cleaned", [])
        skipped = result.get("skipped", [])
        errors = result.get("errors", {})
        parts = [f"已清理 {len(cleaned)} 个类别：{', '.join(cleaned)}"]
        if skipped:
            parts.append(f"跳过 {len(skipped)} 个：{', '.join(skipped)}")
        if errors:
            parts.append(f"失败 {len(errors)} 个：{', '.join(errors)}")
        tidbit = "\n".join(parts)
        headline = "清理完成" if not errors else "清理部分完成"
        QMessageBox.information(self, headline, tidbit)
        self.console_scan()

    def console_export(self) -> None:
        ids = self._selected_console_ids()
        if not ids:
            QMessageBox.information(self, "导出", "请先勾选要导出的类别。")
            return
        out, _ = QFileDialog.getSaveFileName(self, "导出资源备份", "mainpg-resources.zip",
                                             "Zip (*.zip)")
        if not out:
            return
        res = console.export_archive(ids, Path(out))
        QMessageBox.information(self, "导出", res.get("message", str(res)))

    def console_import(self) -> None:
        arch, _ = QFileDialog.getOpenFileName(self, "选择备份文件", "", "Zip (*.zip)")
        if not arch:
            return
        ok = QMessageBox.question(
            self, "确认导入",
            f"导入将把备份内容恢复到本地对应目录/数据库。\n\n{arch}\n\n"
            f"已有同名资源会被覆盖，数据库行按唯一键冲突时替换。\n是否继续？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if ok != QMessageBox.StandardButton.Yes:
            return
        res = console.import_archive(Path(arch))
        headline = "导入完成" if res.get("status") in {"ok", "partial"} else "导入失败"
        QMessageBox.information(self, headline, res.get("message", str(res)))
        self.console_scan()

    # --------------------------- 板块三：版本更新 -------------------------- #
    def _build_update_page(self) -> QWidget:
        page, lay = self._page(
            "版本更新检查",
            "检测当前是否为最新版本，非最新则从官网下载并启动安装器。",
        )

        current_row = QHBoxLayout()
        self.update_current = QLabel("当前版本 v" + update.current_version())
        self.update_current.setStyleSheet(f"font-size:13px;font-weight:600;color:{TEXT};")
        current_row.addWidget(self.update_current)
        current_row.addStretch(1)
        self.btn_update_check = QPushButton("检查更新")
        self.btn_update_check.setFixedHeight(32)
        self.btn_update_check.setStyleSheet(_secondary_button())
        self.btn_update_check.clicked.connect(self.run_update_check)
        current_row.addWidget(self.btn_update_check)
        lay.addLayout(current_row)

        self.update_result = QTextEdit()
        self.update_result.setReadOnly(True)
        self.update_result.setFrameShape(QFrame.Shape.NoFrame)
        self.update_result.setStyleSheet(
            "QTextEdit{background:#f7f8fa;color:#4a5563;border-radius:10px;"
            "padding:12px;font-size:12px;}"
        )
        self.update_result.setMaximumHeight(160)
        self.update_result.setPlainText("启动时已自动检查更新，正在检测最新版本…")
        lay.addWidget(self.update_result, 1)
        return page

    def run_update_check(self) -> None:
        self._update_bar("checking")
        self.update_result.setPlainText("正在检测更新…")
        self.btn_update_check.setEnabled(False)
        self._update_worker = UpdateCheckWorker()
        self._update_worker.done.connect(self._on_update_check_done)
        self._update_worker.error.connect(self._on_update_check_error)
        self._update_worker.start()

    def _on_update_check_done(self, release: update.UpdateRelease | None) -> None:
        self.btn_update_check.setEnabled(True)
        if release is None:
            self._update_bar("full")
            self.lbl_update.setText("启动自检更新：已是最新版本")
            self.update_result.setPlainText("✓ 当前已是最新版本，无需更新。")
            return
        self._update_release = release
        self._update_bar("full")
        self.lbl_update.setText(f"启动自检更新：发现新版本 v{release.version}")
        self.update_result.setPlainText(
            f"发现新版本 v{release.version}\n"
            f"当前版本 v{update.current_version()} → v{release.version}\n"
            f"发布时间：{release.published_at}\n\n"
            f"{release.release_notes or '本次更新提升了稳定性与性能，建议尽快升级。'}"
        )
        self._show_update_dialog(release)

    def _on_update_check_error(self, msg: str) -> None:
        self.btn_update_check.setEnabled(True)
        self._update_bar("zero")
        self.lbl_update.setText("启动自检更新：检测失败（网络或签名异常）")
        self.lbl_update.setToolTip(msg)
        self.update_result.setPlainText("更新检测失败：网络或签名异常。")
        QMessageBox.critical(self, "更新检测失败", msg)

    def _show_update_dialog(self, release: update.UpdateRelease) -> None:
        dlg = QDialog(self)
        dlg.setWindowTitle("发现新版本")
        dlg.setModal(True)
        lay = QVBoxLayout(dlg)
        head = QLabel(f"发现新版本 v{release.version}")
        head.setStyleSheet("font-size:16px;font-weight:600;")
        lay.addWidget(head)
        if release.mandatory:
            warn = QLabel("此为强制更新，需更新后继续使用。")
            warn.setStyleSheet("color:#c62828;")
            lay.addWidget(warn)
        notes = QTextEdit()
        notes.setReadOnly(True)
        notes.setPlainText(release.release_notes or "本次更新提升了稳定性与性能，建议尽快升级。")
        notes.setFixedHeight(120)
        lay.addWidget(notes)
        meta = QLabel(
            f"发布时间：{release.published_at}　·　"
            f"当前 v{update.current_version()} → v{release.version}"
        )
        meta.setStyleSheet(f"color:{DIM};font-size:11px;")
        lay.addWidget(meta)
        row = QHBoxLayout()
        btn_dl = QPushButton("下载更新")
        btn_dl.setDefault(True)
        row.addWidget(btn_dl)
        btn_later = QPushButton("稍后")
        row.addWidget(btn_later)
        if not release.mandatory:
            btn_snooze = QPushButton("暂缓")
            row.addWidget(btn_snooze)
        lay.addLayout(row)
        btn_dl.clicked.connect(lambda: self._update_choose("download", release, dlg))
        btn_later.clicked.connect(dlg.reject)
        if not release.mandatory:
            btn_snooze.clicked.connect(lambda: self._update_choose("snooze", release, dlg))
        dlg.exec()

    def _update_choose(self, action: str, release: update.UpdateRelease, dlg: QDialog) -> None:
        dlg.accept()
        if action == "download":
            self._start_update_download(release)
        elif action == "snooze":
            try:
                update.snooze_version(release.version)
            except OSError as exc:
                QMessageBox.warning(self, "暂缓失败", str(exc))
                return
            self.lbl_update.setText(f"启动自检更新：已暂缓 v{release.version}（下次启动不再提示）")

    def _start_update_download(self, release: update.UpdateRelease) -> None:
        self._update_release = release
        self._update_bar("download", 0.0)
        self.update_result.setPlainText(f"正在下载 v{release.version} 更新…")
        self._update_download_worker = UpdateDownloadWorker(release)
        self._update_download_worker.progress.connect(self._on_update_download_progress)
        self._update_download_worker.done.connect(self._on_update_download_done)
        self._update_download_worker.error.connect(self._on_update_download_error)
        self._update_download_worker.start()

    def _on_update_download_progress(self, downloaded: int, total: int, pct: float) -> None:
        self._update_bar("download", pct)
        self.update_result.setPlainText(f"正在下载更新… {pct:.1f}%（{_fmt_bytes(downloaded)}）")

    def _on_update_download_done(self, path: str) -> None:
        self._update_bar("full")
        self.lbl_update.setText("启动自检更新：下载完成，正在启动安装器…")
        self.update_result.setPlainText("下载完成，正在启动安装器。")
        try:
            update.launch_installer(Path(path))
        except OSError as exc:
            QMessageBox.critical(self, "启动安装器失败", str(exc))
            return
        QMessageBox.information(
            self, "更新已就绪",
            f"新版本安装包已下载并校验收妥：\n{path}\n\n"
            f"已启动安装器，安装完成后将自动替换当前版本。",
        )

    def _on_update_download_error(self, msg: str) -> None:
        self._update_bar("zero")
        self.lbl_update.setText("启动自检更新：下载失败")
        self.lbl_update.setToolTip(msg)
        self.update_result.setPlainText("更新下载失败。")
        QMessageBox.critical(self, "更新下载失败", msg)

    def _update_bar(self, phase: str, pct: float | None = None) -> None:
        bar = self.update_progress
        if phase == "checking":
            bar.setRange(0, 0)
            self.lbl_update.setText("启动自检更新：正在检测更新…")
        elif phase == "download":
            bar.setRange(0, 1000)
            bar.setValue(0 if pct is None else int(min(100.0, pct) * 10))
            self.lbl_update.setText(
                f"启动自检更新：正在下载更新… {pct:.1f}%"
                if pct is not None else "启动自检更新：正在下载更新…")
        elif phase == "full":
            bar.setRange(0, 1000)
            bar.setValue(1000)
        elif phase == "zero":
            bar.setRange(0, 1000)
            bar.setValue(0)

    # --------------------------- 板块四：日志上传 -------------------------- #
    def _build_log_page(self) -> QWidget:
        page, lay = self._page(
            "本地报错日志上传",
            "登录后可将本地 runtime.log 上报到服务器，便于技术支持定位问题。",
        )

        # 登录区
        login_box = QFrame()
        login_box.setStyleSheet(
            "QFrame{background:#f7f8fa;border-radius:10px;} QLineEdit{background:#ffffff;"
            "border:1px solid #dfe3ea;border-radius:6px;padding:6px 8px;font-size:12px;}"
        )
        l_lay = QGridLayout(login_box)
        l_lay.setContentsMargins(14, 12, 14, 12)
        l_lay.setHorizontalSpacing(10)
        l_lay.setVerticalSpacing(10)
        lbl_u = QLabel("账号")
        lbl_u.setStyleSheet(f"font-size:12px;color:{DIM};")
        self.log_username = QLineEdit()
        self.log_username.setPlaceholderText("用户名 / 邮箱")
        self.log_username.setFixedHeight(32)
        lbl_p = QLabel("密码")
        lbl_p.setStyleSheet(f"font-size:12px;color:{DIM};")
        self.log_password = QLineEdit()
        self.log_password.setEchoMode(QLineEdit.EchoMode.Password)
        self.log_password.setPlaceholderText("账户密码")
        self.log_password.setFixedHeight(32)
        self.btn_log_login = QPushButton("登录")
        self.btn_log_login.setFixedHeight(32)
        self.btn_log_login.setStyleSheet(_secondary_button())
        self.btn_log_login.clicked.connect(self.on_log_login)
        self.btn_log_upload = QPushButton("上传日志")
        self.btn_log_upload.setFixedHeight(32)
        self.btn_log_upload.setStyleSheet(_secondary_button())
        self.btn_log_upload.setEnabled(False)
        self.btn_log_upload.clicked.connect(self.on_log_upload)
        l_lay.addWidget(lbl_u, 0, 0)
        l_lay.addWidget(self.log_username, 0, 1)
        l_lay.addWidget(lbl_p, 1, 0)
        l_lay.addWidget(self.log_password, 1, 1)
        l_lay.addWidget(self.btn_log_login, 2, 0, 1, 1)
        l_lay.addWidget(self.btn_log_upload, 2, 1, 1, 1)
        lay.addWidget(login_box)

        # 状态与结果
        self.log_status = QLabel("未登录")
        self.log_status.setStyleSheet(f"font-size:11px;color:{DIM};")
        lay.addWidget(self.log_status)
        self.log_result = QTextEdit()
        self.log_result.setReadOnly(True)
        self.log_result.setFrameShape(QFrame.Shape.NoFrame)
        self.log_result.setStyleSheet(
            "QTextEdit{background:#12151c;color:#cfd6e0;border-radius:10px;"
            "padding:12px;font-family:Consolas,'Courier New';font-size:11px;}"
        )
        log_path = logupload.runtime_log_path()
        self.log_result.setPlainText(
            f"日志文件：{log_path}\n存在：" + ("是" if log_path.exists() else "否") +
            "，登录后即可上传。"
        )
        lay.addWidget(self.log_result, 1)
        return page

    def on_log_login(self) -> None:
        username = self.log_username.text().strip()
        password = self.log_password.text()
        if not username or not password:
            QMessageBox.information(self, "登录", "请输入账号与密码。")
            return
        self.btn_log_login.setEnabled(False)
        self.log_status.setText("正在登录…")
        self.log_result.setPlainText("正在验证账号密码…")
        self._login_worker = LogLoginWorker(username, password)
        self._login_worker.done.connect(self._on_log_login_done)
        self._login_worker.error.connect(self._on_log_login_error)
        self._login_worker.start()

    def _on_log_login_done(self, result: tuple[str, dict]) -> None:
        token, account = result
        self._remote_token = token
        self.btn_log_login.setEnabled(True)
        self.btn_log_upload.setEnabled(True)
        name = account.get("username") or account.get("email") or account.get("display_name") or "用户"
        self.log_status.setText(f"已登录：{name}")
        self.log_status.setStyleSheet("font-size:11px;color:#2e7d32;")
        self.log_result.setPlainText("登录成功，可以上传日志。")

    def _on_log_login_error(self, msg: str) -> None:
        self.btn_log_login.setEnabled(True)
        self.log_status.setText("登录失败")
        self.log_status.setStyleSheet(f"font-size:11px;color:#c62828;")
        self.log_result.setPlainText(msg)
        QMessageBox.critical(self, "登录失败", msg)

    def on_log_upload(self) -> None:
        if not self._remote_token:
            QMessageBox.information(self, "上传", "请先登录。")
            return
        log_path = logupload.runtime_log_path()
        if not log_path.exists():
            QMessageBox.warning(self, "上传", f"找不到日志文件：{log_path}")
            return
        ok = QMessageBox.question(
            self, "确认上传",
            f"将把以下日志上传到服务器：\n\n{log_path}\n\n"
            f"大小：{_fmt_bytes(log_path.stat().st_size)}\n\n是否继续？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes,
        )
        if ok != QMessageBox.StandardButton.Yes:
            return
        self.btn_log_upload.setEnabled(False)
        self.btn_log_login.setEnabled(False)
        self.log_status.setText("正在上传…")
        self.log_result.clear()
        self._upload_worker = LogUploadWorker(self._remote_token, log_path)
        self._upload_worker.log.connect(self._on_log_upload_log)
        self._upload_worker.done.connect(self._on_log_upload_done)
        self._upload_worker.error.connect(self._on_log_upload_error)
        self._upload_worker.start()

    def _on_log_upload_log(self, line: str) -> None:
        self.log_result.append(line)

    def _on_log_upload_done(self, result: dict) -> None:
        self.btn_log_upload.setEnabled(True)
        self.btn_log_login.setEnabled(True)
        self.log_status.setText("上传成功")
        self.log_status.setStyleSheet("font-size:11px;color:#2e7d32;")
        log_id = result.get("upload_id", "") or result.get("id", "")
        self.log_result.append(f"✓ 上传成功，记录ID：{log_id}")

    def _on_log_upload_error(self, msg: str) -> None:
        self.btn_log_upload.setEnabled(True)
        self.btn_log_login.setEnabled(True)
        self.log_status.setText("上传失败")
        self.log_status.setStyleSheet(f"font-size:11px;color:#c62828;")
        self.log_result.append(f"上传失败：{msg}")
        QMessageBox.critical(self, "上传失败", msg)

    # ------------------------------------------------------------ 一键启动
    def on_start(self) -> None:
        exe = core.find_product()
        if not exe:
            QMessageBox.warning(self, "未找到主程序",
                                "未找到 MainPG.exe。请确认产品已安装，或用 WH_APP_EXE 指定路径。")
            return
        try:
            os.startfile(str(exe))  # type: ignore[attr-defined]
        except OSError as exc:
            QMessageBox.critical(self, "启动失败", str(exc))


def _esc(text: str) -> str:
    return (text.replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


def _secondary_button() -> str:
    return (
        "QPushButton{background:#f4f6f9;color:#3b4553;border:1px solid #dfe3ea;"
        "border-radius:6px;padding:0 14px;font-size:12px;}"
        "QPushButton:hover{background:#e9edf3;} QPushButton:disabled{background:#f0f2f5;color:#a9b0ba;}"
    )


def _table_style() -> str:
    return (
        "QTableWidget{background:#ffffff;alternate-background-color:#f7f9fc;"
        "border:1px solid #e6e9ee;border-radius:8px;color:#1f2d3d;font-size:12px;}"
        "QTableWidget::item{color:#1f2d3d;padding:2px 8px;border:none;}"
        "QTableWidget::item:selected{background:#e8eefc;color:#1f2d3d;}"
        "QTableWidget::indicator{width:16px;height:16px;border:1px solid #c3cad4;"
        "border-radius:4px;background:#ffffff;}"
        "QTableWidget::indicator:hover{border-color:#2f6fed;}"
        "QTableWidget::indicator:checked{background:#2f6fed;border-color:#2f6fed;}"
        "QHeaderView::section{background:#f7f8fa;border:none;border-bottom:1px solid #e6e9ee;"
        "font-size:11px;color:#8a919d;padding:6px;font-weight:600;}"
    )


def main() -> int:
    app = QtWidgets.QApplication(sys.argv)
    win = MainWindow()
    win.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
