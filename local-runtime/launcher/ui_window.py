"""Launcher GUI（PySide6）— 与主程序风格统一的现代化启动器。

视觉参考：主程序工作台 / 登录页
- 浅蓝灰页面背景 (#edf4fb)
- 白色毛玻璃卡片 + 22px 大圆角 + 柔和阴影
- 蓝青渐变主色 (#087bf5 → #14c8c0)
- 左侧浅色侧边栏卡片
- 启动时带 Splash 动画

五个功能页通过 QStackedWidget 切换：
  1. 本地环境监测：只告诉用户「就绪 / 未就绪」，逐条输出检查项。
  2. 主程序进程：启动 / 停止 / 重启本地主程序，观测内存占用与运行时长。
  3. 本地文件资源缓存：扫描本地个人资产生成物，可清理 / 导出 / 导入。
  4. 版本更新检查：查询当前版本，非最新则去官网下载最新版。
  5. 本地报错日志上传：用户账户/密码登录后，将本地 runtime.log 上报到服务器。

主界面左侧底部提供「启动 / 停止 / 重启主程序」控制组。
"""
from __future__ import annotations

import sys
import time
from pathlib import Path
from typing import Any

from PySide6.QtCore import (
    QEasingCurve,
    QEventLoop,
    QPointF,
    QRectF,
    Qt,
    QThread,
    QTimer,
    QVariantAnimation,
    Signal,
    Property,
)
from PySide6.QtGui import (
    QBrush,
    QColor,
    QFont,
    QFontDatabase,
    QIcon,
    QLinearGradient,
    QPainter,
    QPen,
    QTextCursor,
)
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QFileDialog,
    QFrame,
    QGraphicsDropShadowEffect,
    QGraphicsOpacityEffect,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)
from PySide6 import QtWidgets

from . import console, core, logupload, update
from .process import (
    STATE_RUNNING,
    STATE_STARTING,
    STATE_STOPPED,
    ProductProcess,
)

# ----------------------------- 设计令牌 ----------------------------------- #

# 页面
PAGE_BG = "#edf4fb"

# 卡片
CARD_BG = "rgba(255,255,255,0.92)"
CARD_BORDER = "rgba(184, 209, 230, 0.82)"
CARD_SHADOW = "rgba(26, 65, 101, 0.10)"
CARD_RADIUS = 22

# 主色
PRIMARY = "#087bf5"
PRIMARY_LIGHT = "#14c8c0"
BRAND_GRADIENT = (
    "qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #008bff, stop:1 #16d7c0)"
)
PRIMARY_GRADIENT = (
    "qlineargradient(x1:0, y1:0, x2:1, y2:0.4, stop:0 #087bf5, stop:1 #14c8c0)"
)

# 文字
TEXT_PRIMARY = "#13233a"
TEXT_SECONDARY = "#527089"
TEXT_MUTED = "#8194a8"
TEXT_ON_GRADIENT = "#ffffff"

# 状态
SUCCESS = "#1bc39a"
SUCCESS_BG = "#e8faf4"
SUCCESS_TEXT = "#07966f"
WARN = "#e5a83c"
WARN_BG = "#fffaf0"
WARN_TEXT = "#b46f00"
ERROR = "#e5484d"
ERROR_BG = "#fff5f5"
ERROR_TEXT = "#b42318"
INFO_BG = "#eaf5ff"
INFO_TEXT = "#0877d3"

# 侧边栏
SIDEBAR_BG = "rgba(255,255,255,0.94)"
SIDEBAR_HOVER = "#f0f7fb"
SIDEBAR_ACTIVE_BG = (
    "qlineargradient(x1:0, y1:0, x2:1, y2:0.2, stop:0 #ccecff, stop:1 #d5f5ef)"
)
SIDEBAR_ACTIVE_COLOR = "#056fc8"
SIDEBAR_TEXT = "#527089"

# 侧边栏宽度（展开 / 折叠）
SIDEBAR_W = 220
SIDEBAR_W_COLLAPSED = 96
SIDEBAR_ANIM_MS = 260

# 环形内存仪表的量程起步值与步进
GAUGE_MIN_SCALE = 256 * 1024 * 1024
GAUGE_STEP = 256 * 1024 * 1024

# 页面切换时内容下沉的起始像素，配合淡入形成轻微上移过渡
PAGE_SLIDE_OFFSET = 14
PAGE_FADE_MS = 240

# 输入/表单
INPUT_BORDER = "#d6e2ed"
INPUT_FOCUS = "#1e91e6"
INPUT_BG = "#ffffff"

# iconfont 图标映射（取自 web-frontend/download/font_5219619_sag0ft83mnn/iconfont.css）
ICONS = {
    "dashboard": "\ue79e",
    "folder": "\ue810",
    "folder_open": "\ue811",
    "sync": "\ue797",
    "reload": "\ue79b",
    "upload": "\ue862",
    "cloud_upload": "\ue827",
    "play": "\ue78b",
    "rocket": "\ue80e",
    "setting": "\ue7a3",
    "check_circle": "\ue77d",
    "warning_circle": "\ue796",
    "close_circle": "\ue781",
    "info_circle": "\ue783",
    "file_text": "\ue7ed",
    "database": "\ue7d5",
    "delete": "\ue7f8",
    "search": "\ue9a0",
    "cloud_server": "\ue826",
    "home": "\ue801",
    "user": "\ue7ce",
    "right": "\ue856",
    "check": "\ue886",
    "smile": "\ue78f",
    "star": "\ue839",
    "fire": "\ue897",
    "thunderbolt": "\ue898",
    "skin": "\ue800",
}

# 侧边栏导航项
NAV_ITEMS = [
    ("dashboard", "工作台", "本地环境监测"),
    ("thunderbolt", "进程", "主程序进程管理"),
    ("folder", "资源", "本地文件资源"),
    ("sync", "更新", "版本更新检查"),
    ("upload", "日志", "日志上报"),
]

STATUS_COLOR = {
    "ok": SUCCESS_TEXT,
    "warn": WARN_TEXT,
    "fail": ERROR_TEXT,
    "skip": TEXT_MUTED,
}


# --------------------------- 字体与工具 ----------------------------------- #


def _asset_path(name: str) -> str:
    """返回 launcher 包内 assets 目录下文件的绝对路径。"""
    pkg = Path(__file__).resolve().parent
    return str(pkg / "assets" / name)


def _app_icon() -> QIcon:
    """返回应用图标（「界」字 app-icon.ico），用于窗口/任务栏。

    PyInstaller onefile 时资源解包到 sys._MEIPASS；源码运行时在 local-runtime 根目录。
    """
    candidates = []
    meipass = getattr(sys, "_MEIPASS", "")
    if meipass:
        candidates.append(Path(meipass) / "app-icon.ico")
    candidates.append(Path(__file__).resolve().parent.parent / "app-icon.ico")
    for path in candidates:
        if path.is_file():
            return QIcon(str(path))
    return QIcon()


def _load_iconfont() -> int:
    """加载 iconfont.ttf，返回 font id；失败返回 -1。"""
    ttf = _asset_path("iconfont.ttf")
    if not Path(ttf).exists():
        return -1
    return QFontDatabase.addApplicationFont(ttf)


def _icon_font(size: int = 16) -> QFont:
    """构造 iconfont 字体对象。"""
    font = QFont("iconfont", size)
    font.setStyleStrategy(QFont.StyleStrategy.PreferAntialias)
    return font


def _icon_label(icon_key: str, size: int = 18, color: str = SIDEBAR_TEXT) -> QLabel:
    """创建一个 iconfont 图标 QLabel。"""
    lbl = QLabel(ICONS.get(icon_key, ""))
    lbl.setFont(_icon_font(size))
    lbl.setStyleSheet(f"color:{color};background:transparent;")
    lbl.setFixedWidth(size + 2)
    lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
    return lbl


def _fmt_bytes(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024:
            return f"{n:.2f} {unit}" if unit != "B" else f"{n} B"
        n /= 1024
    return f"{n:.2f} PB"


def _fmt_duration(seconds: float) -> str:
    """把秒数格式化成「x 小时 y 分 / y 分 z 秒 / z 秒」。"""
    total = int(max(0.0, seconds))
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours} 小时 {minutes} 分"
    if minutes:
        return f"{minutes} 分 {secs} 秒"
    return f"{secs} 秒"


def _esc(text: str) -> str:
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _friendly_key(key: str) -> str:
    mapping = {
        "python": "Python 运行时",
        "app_dir": "安装目录",
        "database": "本地数据库",
        "golden": "配置基准",
        "cos": "对象存储",
        "gateway": "认证网关",
        "engine": "本地服务",
        "updates": "更新通道",
    }
    return mapping.get(key, key)


# --------------------------- 样式片段 ------------------------------------- #


def _card_style() -> str:
    # 必须用 #objectName 限定：QFrame 类型选择器会命中子类 QLabel，
    # 未限定则卡片内所有标签都会被继承上边框。
    return (
        f"QFrame#card{{background:{CARD_BG};border:1px solid {CARD_BORDER};"
        f"border-radius:{CARD_RADIUS}px;}}"
    )


def _primary_button() -> str:
    return (
        f"QPushButton{{color:#ffffff;border:none;border-radius:11px;"
        f"padding:0 18px;font-size:13px;font-weight:750;"
        f"background:{PRIMARY_GRADIENT};}}"
        f"QPushButton:hover{{background:"
        f"qlineargradient(x1:0, y1:0, x2:1, y2:0.4, stop:0 #0a6ee0, stop:1 #12b8b0);}}"
        f"QPushButton:pressed{{background:"
        f"qlineargradient(x1:0, y1:0, x2:1, y2:0.4, stop:0 #085bb8, stop:1 #0fa39c);}}"
        f"QPushButton:disabled{{background:#c8d4e0;color:#ffffff;}}"
    )


def _secondary_button() -> str:
    return (
        f"QPushButton{{background:#ffffff;color:{TEXT_SECONDARY};"
        f"border:1px solid {INPUT_BORDER};border-radius:10px;"
        f"padding:0 14px;font-size:12px;font-weight:700;}}"
        f"QPushButton:hover{{background:#f4f9ff;border-color:#9fd6f7;color:{PRIMARY};}}"
        f"QPushButton:pressed{{background:#eaf5ff;}}"
        f"QPushButton:disabled{{background:#f5f8fb;color:#a9b4c0;}}"
    )


def _danger_button() -> str:
    return (
        f"QPushButton{{background:#fff5f5;color:{ERROR_TEXT};"
        f"border:1px solid #ffd0d0;border-radius:10px;"
        f"padding:0 14px;font-size:12px;font-weight:700;}}"
        f"QPushButton:hover{{background:#ffeded;border-color:#f5b5b5;}}"
    )


def _nav_button_style(collapsed: bool = False) -> str:
    """侧边栏导航按钮样式；折叠时图标居中。"""
    align = "center" if collapsed else "left"
    return (
        f"QPushButton{{color:{SIDEBAR_TEXT};background:transparent;"
        f"border:1px solid transparent;"
        f"border-radius:12px;padding:0 {'0' if collapsed else '11px'};"
        f"font-size:13px;font-weight:720;text-align:{align};}}"
        f"QPushButton:hover{{background:{SIDEBAR_HOVER};color:{TEXT_PRIMARY};}}"
        f"QPushButton:checked{{background:{SIDEBAR_ACTIVE_BG};"
        f"color:{SIDEBAR_ACTIVE_COLOR};border-color:#8fd3ec;}}"
    )


def _ghost_button(centered: bool = False) -> str:
    """侧边栏内的低强调按钮（折叠开关）。"""
    align = "center" if centered else "left"
    return (
        f"QPushButton{{color:{TEXT_MUTED};background:transparent;"
        f"border:1px solid transparent;border-radius:10px;"
        f"padding:0 8px;font-size:12px;font-weight:700;text-align:{align};}}"
        f"QPushButton:hover{{background:{SIDEBAR_HOVER};color:{PRIMARY};}}"
    )


def _table_style() -> str:
    return (
        f"QTableWidget{{background:#ffffff;alternate-background-color:#f7fbff;"
        f"border:1px solid #ddebf4;border-radius:14px;color:{TEXT_PRIMARY};font-size:12px;}}"
        f"QTableWidget::item{{color:{TEXT_PRIMARY};padding:4px 10px;border:none;}}"
        f"QTableWidget::item:selected{{background:#e8f4ff;color:{PRIMARY};}}"
        f"QTableWidget::indicator{{width:16px;height:16px;border:1px solid #c3cad4;"
        f"border-radius:4px;background:#ffffff;}}"
        f"QTableWidget::indicator:hover{{border-color:{PRIMARY};}}"
        f"QTableWidget::indicator:checked{{background:{PRIMARY};border-color:{PRIMARY};}}"
        f"QHeaderView::section{{background:#f0f6fa;border:none;border-bottom:1px solid #ddebf4;"
        f"font-size:11px;color:{TEXT_SECONDARY};padding:8px 10px;font-weight:750;}}"
    )


def _input_style() -> str:
    return (
        f"QLineEdit{{background:{INPUT_BG};border:1px solid {INPUT_BORDER};"
        f"border-radius:10px;padding:8px 12px;font-size:13px;color:{TEXT_PRIMARY};}}"
        f"QLineEdit:focus{{border-color:{INPUT_FOCUS};}}"
    )


def _log_view_style(light: bool = True) -> str:
    if light:
        return (
            f"QTextEdit{{background:#f7fbfd;color:{TEXT_SECONDARY};border-radius:12px;"
            f"padding:12px;font-family:Consolas,'Courier New';font-size:12px;"
            f"border:1px solid #e4eef5;}}"
        )
    return (
        "QTextEdit{background:#0f141c;color:#c8d4e0;border-radius:12px;"
        "padding:12px;font-family:Consolas,'Courier New';font-size:12px;}"
    )


# --------------------------- 后台线程 ------------------------------------- #


class CheckWorker(QThread):
    """后台执行体检，避免阻塞 UI。"""

    done = Signal(object)  # core.LauncherReport
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
        except Exception as exc:  # noqa: BLE001
            self.error.emit(repr(exc))


class ConsoleScanWorker(QThread):
    """后台扫描资源占用，避免阻塞 UI。"""

    done = Signal(object)  # list[dict]
    error = Signal(str)

    def run(self) -> None:  # noqa: D102
        try:
            stats = console.scan()
            self.done.emit(stats)
        except Exception as exc:  # noqa: BLE001
            self.error.emit(repr(exc))


class UpdateCheckWorker(QThread):
    """后台检查更新。"""

    done = Signal(object)  # update.UpdateRelease | None
    error = Signal(str)

    def run(self) -> None:  # noqa: D102
        try:
            release = update.check_for_update(timeout=12.0)
            self.done.emit(release)
        except Exception as exc:  # noqa: BLE001
            self.error.emit(repr(exc))


class ProductActionWorker(QThread):
    """后台执行主程序启动 / 停止 / 重启，避免等待回收阻塞 UI。"""

    done = Signal(str, str)  # action, message
    error = Signal(str, str)  # action, message

    def __init__(self, manager: ProductProcess, action: str) -> None:
        super().__init__()
        self._manager = manager
        self._action = action

    def run(self) -> None:  # noqa: D102
        try:
            if self._action == "start":
                ok, message = self._manager.start()
                if ok:
                    self._manager.wait_until_ready()
            elif self._action == "stop":
                ok, message = self._manager.stop()
            else:
                ok, message = self._manager.restart()
        except Exception as exc:  # noqa: BLE001
            self.error.emit(self._action, repr(exc))
            return
        if ok:
            self.done.emit(self._action, message)
        else:
            self.error.emit(self._action, message)


class UpdateDownloadWorker(QThread):
    """后台下载更新。"""

    progress = Signal(int, int, float)
    done = Signal(str)
    error = Signal(str)

    def __init__(self, release: update.UpdateRelease) -> None:
        super().__init__()
        self.release = release

    def run(self) -> None:  # noqa: D102
        try:
            path = update.download_release(
                self.release, on_progress=lambda d, t, p: self.progress.emit(d, t, p)
            )
            self.done.emit(str(path))
        except Exception as exc:  # noqa: BLE001
            self.error.emit(repr(exc))


class LogLoginWorker(QThread):
    """后台登录日志服务器。"""

    done = Signal(tuple)
    error = Signal(str)

    def __init__(self, username: str, password: str) -> None:
        super().__init__()
        self.username = username
        self.password = password

    def run(self) -> None:  # noqa: D102
        try:
            result = logupload.login(self.username, self.password)
            self.done.emit(result)
        except Exception as exc:  # noqa: BLE001
            self.error.emit(repr(exc))


class LogUploadWorker(QThread):
    """后台上传日志。"""

    log = Signal(str)
    done = Signal(dict)
    error = Signal(str)

    def __init__(self, token: str, log_path: Path) -> None:
        super().__init__()
        self.token = token
        self.log_path = log_path

    def run(self) -> None:  # noqa: D102
        try:
            result = logupload.upload_log(
                self.token, self.log_path, on_log=lambda line: self.log.emit(line)
            )
            self.done.emit(result)
        except Exception as exc:  # noqa: BLE001
            self.error.emit(repr(exc))


# --------------------------- 启动动画 ------------------------------------- #


class SplashScreen(QWidget):
    """启动器启动动画：品牌 Logo、进度条、版本号，完成后淡出。"""

    def __init__(self) -> None:
        super().__init__()
        size = 420
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setFixedSize(size, size)

        self._progress = 0.0
        self._dots = ""
        self._dot_timer = QTimer(self)
        self._dot_timer.setInterval(300)
        self._dot_timer.timeout.connect(self._update_dots)
        self._dot_timer.start()

        # 中心显示
        screen = QtWidgets.QApplication.primaryScreen().availableGeometry()
        self.move(
            screen.center().x() - size // 2,
            screen.center().y() - size // 2,
        )

    def _update_dots(self) -> None:
        self._dots = "." * ((len(self._dots) % 3) + 1)
        self.update()

    def set_progress(self, value: float) -> None:
        self._progress = max(0.0, min(100.0, value))
        self.update()

    def paintEvent(self, event: Any = None) -> None:  # noqa: ARG002
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        # 背景卡片
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(255, 255, 255))
        painter.drawRoundedRect(self.rect(), 28, 28)

        # 外圈渐变环
        grad = QLinearGradient(0, 0, self.width(), self.height())
        grad.setColorAt(0, QColor(0, 139, 255))
        grad.setColorAt(0.5, QColor(20, 200, 192))
        grad.setColorAt(1, QColor(0, 139, 255))
        pen = QPen(QBrush(grad), 4)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRoundedRect(self.rect().adjusted(8, 8, -8, -8), 24, 24)

        # 品牌 Logo 圆形背景
        cx, cy = self.width() // 2, self.height() // 2 - 40
        logo_grad = QLinearGradient(cx - 50, cy - 50, cx + 50, cy + 50)
        logo_grad.setColorAt(0, QColor(0, 155, 255))
        logo_grad.setColorAt(1, QColor(22, 215, 192))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(logo_grad)
        painter.drawEllipse(cx - 52, cy - 52, 104, 104)

        # Logo 文字
        painter.setPen(QColor(255, 255, 255))
        font = QFont("Microsoft YaHei", 38, QFont.Weight.Bold)
        painter.setFont(font)
        painter.drawText(self.rect().adjusted(0, cy - 90, 0, cy - 10), Qt.AlignmentFlag.AlignCenter, "界野")

        # 标题
        painter.setPen(QColor(TEXT_PRIMARY))
        font = QFont("Microsoft YaHei", 18, QFont.Weight.Bold)
        painter.setFont(font)
        painter.drawText(self.rect().adjusted(0, cy + 35, 0, cy + 75), Qt.AlignmentFlag.AlignCenter, "MainPG 启动器")

        # 副标题
        painter.setPen(QColor(TEXT_SECONDARY))
        font = QFont("Microsoft YaHei", 11)
        painter.setFont(font)
        painter.drawText(
            self.rect().adjusted(0, cy + 72, 0, cy + 105),
            Qt.AlignmentFlag.AlignCenter,
            f"正在初始化{self._dots}",
        )

        # 进度条背景
        bar_y = cy + 120
        bar_w, bar_h = 260, 8
        bar_x = (self.width() - bar_w) // 2
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(229, 239, 246))
        painter.drawRoundedRect(bar_x, bar_y, bar_w, bar_h, bar_h // 2, bar_h // 2)

        # 进度条填充
        fill_w = int(bar_w * self._progress / 100.0)
        if fill_w:
            fill_grad = QLinearGradient(bar_x, bar_y, bar_x + bar_w, bar_y)
            fill_grad.setColorAt(0, QColor(8, 123, 245))
            fill_grad.setColorAt(1, QColor(20, 200, 192))
            painter.setBrush(fill_grad)
            painter.drawRoundedRect(bar_x, bar_y, fill_w, bar_h, bar_h // 2, bar_h // 2)

        # 版本号
        painter.setPen(QColor(TEXT_MUTED))
        font = QFont("Microsoft YaHei", 10)
        painter.setFont(font)
        painter.drawText(
            self.rect().adjusted(0, bar_y + 20, 0, bar_y + 50),
            Qt.AlignmentFlag.AlignCenter,
            f"v{update.current_version()}",
        )

    def fade_out(self) -> None:
        """执行淡出动画，动画结束后关闭。"""
        self._fade_opacity = 1.0
        self._fade_timer = QTimer(self)
        self._fade_timer.setInterval(30)
        self._fade_timer.timeout.connect(self._fade_step)
        self._fade_timer.start()

    def _fade_step(self) -> None:
        self._fade_opacity -= 0.06
        if self._fade_opacity <= 0.0:
            self._fade_opacity = 0.0
            self._fade_timer.stop()
            self.close()
        else:
            self.setWindowOpacity(self._fade_opacity)


# --------------------------- Hero 动态横幅 -------------------------------- #


class HeroBanner(QFrame):
    """顶部欢迎横幅，带动态渐变背景。"""

    _offset_changed = Signal(float)

    def __init__(self, title: str, subtitle: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("heroBanner")
        self.setFixedHeight(138)
        self.setStyleSheet("QFrame#heroBanner{border-radius:18px;}")

        self._title = title
        self._subtitle = subtitle
        self._offset = 0.0
        self._anim = QVariantAnimation(self)
        self._anim.setDuration(9000)
        self._anim.setStartValue(0.0)
        self._anim.setEndValue(1.0)
        self._anim.setEasingCurve(QEasingCurve.Type.InOutSine)
        self._anim.setLoopCount(-1)
        self._anim.valueChanged.connect(self._set_offset)
        self._anim.start()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 22, 28, 22)
        layout.setSpacing(6)
        self.title_lbl = QLabel(title)
        self.title_lbl.setStyleSheet(
            "font-size:22px;font-weight:800;color:#ffffff;background:transparent;"
        )
        self.sub_lbl = QLabel(subtitle)
        self.sub_lbl.setStyleSheet(
            "font-size:12px;color:rgba(255,255,255,0.82);background:transparent;"
        )
        layout.addWidget(self.title_lbl)
        layout.addWidget(self.sub_lbl)

    @Property(float)
    def gradientOffset(self) -> float:
        return self._offset

    @gradientOffset.setter
    def gradientOffset(self, value: float) -> None:
        self._set_offset(value)

    def _set_offset(self, value: float) -> None:
        self._offset = value
        self.update()

    def paintEvent(self, event: Any = None) -> None:  # noqa: ARG002
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        # 动态渐变背景
        off = self._offset
        grad = QLinearGradient(
            QPointF(self.width() * (0.1 - off * 0.2), 0),
            QPointF(self.width() * (0.9 + off * 0.2), self.height()),
        )
        grad.setColorAt(0, QColor(8, 61, 114))
        grad.setColorAt(0.45, QColor(8, 123, 197))
        grad.setColorAt(1, QColor(27, 189, 183))

        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(grad)
        painter.drawRoundedRect(self.rect(), 18, 18)

        # 装饰性光斑
        painter.setBrush(QColor(255, 255, 255, 26))
        painter.drawEllipse(
            int(self.width() * 0.78), int(-self.height() * 0.3),
            int(self.width() * 0.35), int(self.height() * 1.4)
        )
        painter.drawEllipse(
            int(self.width() * 0.62), int(self.height() * 0.55),
            int(self.width() * 0.18), int(self.width() * 0.18)
        )


class RingGauge(QWidget):
    """环形内存仪表：外环按自适应量程展示占用比例，中心显示当前读数。

    Qt 的 QPen 不接受 QLinearGradient，必须用 QPen(QBrush(grad), width)，
    斜向渐变模拟环上的颜色过渡。
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setMinimumSize(188, 188)
        self.setMaximumWidth(206)
        self._ratio = 0.0
        self._value_text = "—"
        self._scale_text = "未运行"
        self._active = False
        self._scale_max = GAUGE_MIN_SCALE
        self._anim = QVariantAnimation(self)
        self._anim.setDuration(520)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._anim.valueChanged.connect(self._on_tick)

    def _on_tick(self, value: Any) -> None:
        self._ratio = float(value)
        self.update()

    def set_reading(self, value: int | None, peak: int | None) -> None:
        """value / peak 为字节数；value 为 None 表示主程序未运行。"""
        if value is None:
            self._value_text = "—"
            self._scale_text = "未运行"
            self._active = False
            self._scale_max = GAUGE_MIN_SCALE
            target = 0.0
        else:
            if value > self._scale_max:
                self._scale_max = ((value // GAUGE_STEP) + 1) * GAUGE_STEP
            self._value_text = _fmt_bytes(value)
            self._scale_text = f"量程 {_fmt_bytes(self._scale_max)}"
            if peak:
                self._scale_text += f" · 峰值 {_fmt_bytes(peak)}"
            self._active = True
            target = min(1.0, value / self._scale_max)

        self._anim.stop()
        self._anim.setStartValue(self._ratio)
        self._anim.setEndValue(target)
        self._anim.start()

    def paintEvent(self, event: Any = None) -> None:  # noqa: ARG002
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        side = min(self.width(), self.height())
        thickness = max(10, int(side * 0.072))
        inset = thickness / 2 + 6
        box = QRectF(
            (self.width() - side) / 2 + inset,
            (self.height() - side) / 2 + inset,
            side - inset * 2,
            side - inset * 2,
        )

        # 底环
        track = QPen(QBrush(QColor(228, 238, 245)), thickness)
        track.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(track)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawArc(box, 0, 360 * 16)

        # 进度环：从左下到右上的斜向渐变，模拟沿环的颜色过渡
        if self._ratio > 0.001:
            grad = QLinearGradient(box.topLeft(), box.bottomRight())
            grad.setColorAt(0.0, QColor(8, 123, 245))
            grad.setColorAt(0.55, QColor(16, 168, 226))
            grad.setColorAt(1.0, QColor(20, 200, 192))
            arc = QPen(QBrush(grad), thickness)
            arc.setCapStyle(Qt.PenCapStyle.RoundCap)
            painter.setPen(arc)
            painter.drawArc(box, 90 * 16, -int(360 * 16 * self._ratio))

        center = self.rect().center()
        value_rect = QRectF(0, center.y() - 38, self.width(), 42)
        caption_rect = QRectF(0, center.y() + 2, self.width(), 22)
        scale_rect = QRectF(0, center.y() + 24, self.width(), 20)

        painter.setPen(QColor(TEXT_PRIMARY) if self._active else QColor(TEXT_MUTED))
        painter.setFont(QFont("Microsoft YaHei", 17, QFont.Weight.Bold))
        painter.drawText(value_rect, Qt.AlignmentFlag.AlignCenter, self._value_text)

        painter.setPen(QColor(TEXT_MUTED))
        painter.setFont(QFont("Microsoft YaHei", 10))
        painter.drawText(caption_rect, Qt.AlignmentFlag.AlignCenter, "内存占用")

        painter.setFont(QFont("Microsoft YaHei", 8))
        painter.drawText(scale_rect, Qt.AlignmentFlag.AlignCenter, self._scale_text)


# --------------------------- 主窗口 --------------------------------------- #


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("MainPG 启动器")
        self.setMinimumSize(1040, 720)
        self.resize(1180, 780)

        self._worker: CheckWorker | None = None
        self._console_worker: ConsoleScanWorker | None = None
        self._update_worker: UpdateCheckWorker | None = None
        self._update_download_worker: UpdateDownloadWorker | None = None
        self._login_worker: LogLoginWorker | None = None
        self._upload_worker: LogUploadWorker | None = None
        self._proc_worker: ProductActionWorker | None = None
        self._product = ProductProcess()
        self._proc_action = ""
        self._collapsed = False
        self._page_effects: dict[int, QGraphicsOpacityEffect] = {}
        self._page_anim: QVariantAnimation | None = None
        self._sidebar_anim: QVariantAnimation | None = None
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
        central.setStyleSheet(f"background:{PAGE_BG};")
        self.setCentralWidget(central)

        outer = QHBoxLayout(central)
        outer.setContentsMargins(18, 18, 18, 18)
        outer.setSpacing(18)

        outer.addWidget(self._build_sidebar())

        right = QVBoxLayout()
        right.setContentsMargins(0, 0, 0, 0)
        right.setSpacing(18)

        self.stack = QStackedWidget()
        self.stack.setStyleSheet(f"background:{PAGE_BG};")
        self.stack.addWidget(self._build_env_page())
        self.stack.addWidget(self._build_process_page())
        self.stack.addWidget(self._build_resource_page())
        self.stack.addWidget(self._build_update_page())
        self.stack.addWidget(self._build_log_page())
        right.addWidget(self.stack, 1)
        right.addWidget(self._build_status_bar())
        outer.addLayout(right, 1)

        # 构造完成后再做首帧状态同步（此时进程页组件已就绪）
        QTimer.singleShot(0, self._refresh_sidebar_status)

    def _build_sidebar(self) -> QFrame:
        side = QFrame()
        side.setObjectName("sidebar")
        side.setFixedWidth(SIDEBAR_W)
        side.setStyleSheet(
            f"QFrame#sidebar{{background:{SIDEBAR_BG};border:1px solid {CARD_BORDER};"
            f"border-radius:{CARD_RADIUS}px;}}"
        )
        self._sidebar = side
        shadow = QVBoxLayout(side)
        shadow.setContentsMargins(14, 20, 14, 18)
        shadow.setSpacing(10)

        # 品牌区
        brand = QHBoxLayout()
        brand.setSpacing(10)
        brand.addStretch(0)  # 折叠时置为 1，把品牌图标挤到水平居中
        brand_icon = QLabel("界")
        brand_icon.setFont(QFont("Microsoft YaHei", 16, QFont.Weight.Bold))
        brand_icon.setStyleSheet(
            f"color:#ffffff;background:{BRAND_GRADIENT};"
            f"border-radius:12px;padding:6px 8px;"
        )
        brand_icon.setFixedSize(40, 40)
        brand_icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        brand_text_box = QWidget()
        brand_text_box.setStyleSheet("background:transparent;")
        brand_text = QVBoxLayout(brand_text_box)
        brand_text.setContentsMargins(0, 0, 0, 0)
        brand_text.setSpacing(2)
        brand_name = QLabel("MainPG")
        brand_name.setStyleSheet(
            f"font-size:17px;font-weight:800;color:{TEXT_PRIMARY};background:transparent;"
        )
        brand_sub = QLabel("启动器")
        brand_sub.setStyleSheet(
            f"font-size:11px;color:{TEXT_MUTED};background:transparent;"
        )
        brand_text.addWidget(brand_name)
        brand_text.addWidget(brand_sub)
        brand.addWidget(brand_icon)
        brand.addWidget(brand_text_box, 1)
        shadow.addLayout(brand)
        self._brand_row = brand
        self._brand_text_box = brand_text_box

        # 折叠 / 展开开关
        self.btn_collapse = QPushButton("«  收起")
        self.btn_collapse.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_collapse.setFixedHeight(30)
        self.btn_collapse.setStyleSheet(_ghost_button())
        self.btn_collapse.clicked.connect(self._toggle_sidebar)
        shadow.addWidget(self.btn_collapse)
        shadow.addSpacing(8)

        # 导航
        self._nav_buttons: list[QPushButton] = []
        for idx, (icon_key, label, tooltip) in enumerate(NAV_ITEMS):
            btn = QPushButton(f"{ICONS.get(icon_key, '')}   {label}")
            btn.setFont(_icon_font(14))
            btn.setToolTip(tooltip)
            btn.setCheckable(True)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setFixedHeight(44)
            btn.setStyleSheet(_nav_button_style())
            btn.clicked.connect(lambda _=False, i=idx: self._switch_page(i))
            shadow.addWidget(btn)
            self._nav_buttons.append(btn)

        shadow.addStretch(1)

        # 主程序运行状态
        status_box = QFrame()
        status_box.setObjectName("statusBox")
        status_box.setStyleSheet(
            "QFrame#statusBox{background:#f4f9ff;border-radius:12px;border:1px solid #d9ecfa;}"
        )
        status_lay = QVBoxLayout(status_box)
        status_lay.setContentsMargins(12, 12, 12, 12)
        status_lay.setSpacing(6)
        running_lbl = QLabel(f"{ICONS['cloud_server']}  主程序状态")
        running_lbl.setFont(_icon_font(12))
        running_lbl.setStyleSheet(f"color:{TEXT_SECONDARY};font-size:12px;font-weight:700;")
        self.sidebar_status = QLabel("检测中…")
        self.sidebar_status.setWordWrap(True)
        self.sidebar_status.setStyleSheet(f"color:{TEXT_MUTED};font-size:11px;")
        status_lay.addWidget(running_lbl)
        status_lay.addWidget(self.sidebar_status)
        # 主程序启动 / 停止 / 重启期间的进度反馈：等待端口就绪期间保持不确定态动画
        self.sidebar_progress = QProgressBar()
        self.sidebar_progress.setTextVisible(False)
        self.sidebar_progress.setRange(0, 0)
        self.sidebar_progress.setFixedHeight(4)
        self.sidebar_progress.setStyleSheet(
            "QProgressBar{background:#dbe9f5;border:none;border-radius:2px;}"
            f"QProgressBar::chunk{{background:{PRIMARY_GRADIENT};border-radius:2px;}}"
        )
        self.sidebar_progress.setVisible(False)
        status_lay.addWidget(self.sidebar_progress)
        shadow.addWidget(status_box)
        shadow.addSpacing(10)
        self._status_box = status_box
        self._status_caption = running_lbl

        # 进程控制：启动 / 停止 / 重启
        self.btn_launch = QPushButton(f"{ICONS['play']}  启动主程序")
        self.btn_launch.setFont(_icon_font(14))
        self.btn_launch.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_launch.setFixedHeight(46)
        self.btn_launch.setStyleSheet(_primary_button())
        self.btn_launch.clicked.connect(self.on_start)
        # Qt QSS 不支持 box-shadow，用图形效果实现按钮浮起阴影
        launch_shadow = QGraphicsDropShadowEffect(self.btn_launch)
        launch_shadow.setBlurRadius(20)
        launch_shadow.setOffset(0, 5)
        launch_shadow.setColor(QColor(11, 147, 220, 56))
        self.btn_launch.setGraphicsEffect(launch_shadow)
        shadow.addWidget(self.btn_launch)

        ctl_row = QHBoxLayout()
        ctl_row.setSpacing(8)
        self.btn_stop = QPushButton(f"{ICONS['close_circle']}  停止")
        self.btn_restart = QPushButton(f"{ICONS['reload']}  重启")
        for btn, slot in ((self.btn_stop, self.on_stop), (self.btn_restart, self.on_restart)):
            btn.setFont(_icon_font(12))
            btn.setFixedHeight(36)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setStyleSheet(_secondary_button())
            btn.clicked.connect(slot)
            ctl_row.addWidget(btn)
        shadow.addLayout(ctl_row)

        # 默认选中第一页
        if self._nav_buttons:
            self._nav_buttons[0].setChecked(True)

        # 定时刷新主程序状态与进程页
        self._status_timer = QTimer(self)
        self._status_timer.setInterval(2000)
        self._status_timer.timeout.connect(self._refresh_sidebar_status)
        self._status_timer.start()

        return side

    # ------------------------------------------------------------ 侧边栏折叠
    def _toggle_sidebar(self) -> None:
        """折叠 / 展开侧边栏，宽度用 QVariantAnimation 过渡。"""
        self._apply_sidebar_state(not self._collapsed)
        target = SIDEBAR_W_COLLAPSED if self._collapsed else SIDEBAR_W
        anim = QVariantAnimation(self)
        anim.setDuration(SIDEBAR_ANIM_MS)
        anim.setStartValue(float(self._sidebar.width()))
        anim.setEndValue(float(target))
        anim.setEasingCurve(QEasingCurve.Type.InOutCubic)
        anim.valueChanged.connect(lambda v: self._sidebar.setFixedWidth(int(v)))
        self._sidebar_anim = anim
        anim.start()

    def _apply_sidebar_state(self, collapsed: bool) -> None:
        """按折叠状态重写侧边栏内的文案、对齐与可见性。"""
        self._collapsed = collapsed
        self._brand_text_box.setVisible(not collapsed)
        # 折叠时两侧同时伸缩，把品牌图标挤到水平居中
        self._brand_row.setStretch(0, 1 if collapsed else 0)

        self._status_caption.setText(
            ICONS["cloud_server"] if collapsed else f"{ICONS['cloud_server']}  主程序状态"
        )
        self._status_caption.setAlignment(
            Qt.AlignmentFlag.AlignCenter
            if collapsed
            else Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
        )
        self.sidebar_status.setVisible(not collapsed)
        if collapsed:
            self.sidebar_progress.setVisible(False)
        self._status_box.setToolTip(self.sidebar_status.text())

        self.btn_collapse.setText("»" if collapsed else "«  收起")
        self.btn_collapse.setToolTip("展开侧边栏" if collapsed else "收起侧边栏")
        self.btn_collapse.setStyleSheet(_ghost_button(centered=collapsed))

        for (icon_key, label, tooltip), btn in zip(NAV_ITEMS, self._nav_buttons):
            icon = ICONS.get(icon_key, "")
            btn.setText(icon if collapsed else f"{icon}   {label}")
            btn.setToolTip(label if collapsed else tooltip)
            btn.setStyleSheet(_nav_button_style(collapsed))

        self.btn_launch.setText(ICONS["play"] if collapsed else f"{ICONS['play']}  启动主程序")
        self.btn_stop.setText(
            ICONS["close_circle"] if collapsed else f"{ICONS['close_circle']}  停止"
        )
        self.btn_restart.setText(ICONS["reload"] if collapsed else f"{ICONS['reload']}  重启")
        for btn, tip in (
            (self.btn_launch, "启动主程序"),
            (self.btn_stop, "停止主程序"),
            (self.btn_restart, "重启主程序"),
        ):
            btn.setToolTip(tip)

    def _refresh_sidebar_status(self) -> None:
        state = self._product.state()
        # 优先本启动器持有的句柄，否则按端口反查，保证外部启动的实例也能显示 PID / 停止
        pid = self._product.active_pid()
        # 启动 / 停止 / 重启由后台线程执行，期间用进度条给出明确的进行中反馈
        busy = self._proc_worker is not None and self._proc_worker.isRunning()
        self.sidebar_progress.setVisible(busy and not self._collapsed)
        if busy:
            self.sidebar_status.setText(f"正在{self._proc_action}主程序…")
            self.sidebar_status.setStyleSheet(
                f"color:{WARN_TEXT};font-size:11px;font-weight:700;"
            )
        elif state == STATE_RUNNING:
            self.sidebar_status.setText(
                f"主程序运行中（PID {pid}）" if pid is not None else "主程序运行中"
            )
            self.sidebar_status.setStyleSheet(
                f"color:{SUCCESS_TEXT};font-size:11px;font-weight:700;"
            )
        elif state == STATE_STARTING:
            self.sidebar_status.setText("主程序启动中…")
            self.sidebar_status.setStyleSheet(
                f"color:{WARN_TEXT};font-size:11px;font-weight:700;"
            )
        else:
            self.sidebar_status.setText("主程序未运行")
            self.sidebar_status.setStyleSheet(f"color:{TEXT_MUTED};font-size:11px;")

        # 同步控制组可用态：停止 / 重启按端口反查 PID，对非本启动器拉起的实例同样可用
        running = state != STATE_STOPPED
        self.btn_launch.setEnabled(not busy and state == STATE_STOPPED)
        self.btn_stop.setEnabled(not busy and running)
        self.btn_restart.setEnabled(not busy and running)
        if self._collapsed:
            self._status_box.setToolTip(self.sidebar_status.text())
        self._refresh_process_page(state)

    def _switch_page(self, idx: int) -> None:
        for i, b in enumerate(self._nav_buttons):
            b.setChecked(i == idx)
        if idx == self.stack.currentIndex():
            return
        self.stack.setCurrentIndex(idx)
        self._fade_in_page(self.stack.currentWidget())

    def _fade_in_page(self, page: QWidget | None) -> None:
        """页面切换过渡：新页面淡入并轻微上移，避免生硬跳变。

        QStackedWidget 不支持转场动画，这里给页面挂一个常驻的
        QGraphicsOpacityEffect 做透明度过渡，同时把页面顶部内边距
        从 PAGE_SLIDE_OFFSET 收回到 0，形成轻微上移。
        """
        if page is None:
            return
        key = id(page)
        effect = self._page_effects.get(key)
        if effect is None:
            effect = QGraphicsOpacityEffect(page)
            page.setGraphicsEffect(effect)
            self._page_effects[key] = effect

        def _tick(value: object) -> None:
            ratio = float(value)
            effect.setOpacity(ratio)
            self._slide_page(page, ratio)

        anim = QVariantAnimation(self)
        anim.setDuration(PAGE_FADE_MS)
        anim.setStartValue(0.0)
        anim.setEndValue(1.0)
        anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        anim.valueChanged.connect(_tick)
        anim.finished.connect(lambda: _tick(1.0))
        self._page_anim = anim
        anim.start()

    def _slide_page(self, page: QWidget, ratio: float) -> None:
        """过渡期把页面内容从下方推入；``ratio`` 为 1 时完全归位。"""
        layer = page.layout()
        if layer is None:
            return
        offset = max(0, int(PAGE_SLIDE_OFFSET * (1.0 - ratio)))
        layer.setContentsMargins(0, offset, 0, 0)

    def _build_status_bar(self) -> QFrame:
        bar = QFrame()
        bar.setObjectName("statusBar")
        bar.setStyleSheet(
            f"QFrame#statusBar{{background:{CARD_BG};border:1px solid {CARD_BORDER};"
            f"border-radius:16px;}}"
        )
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(18, 10, 18, 10)
        lay.setSpacing(12)

        self.lbl_update = QLabel("启动自检更新…")
        self.lbl_update.setStyleSheet(f"color:{TEXT_SECONDARY};font-size:12px;")
        self.update_progress = QProgressBar()
        self.update_progress.setTextVisible(False)
        self.update_progress.setRange(0, 0)
        self.update_progress.setFixedHeight(6)
        self.update_progress.setFixedWidth(160)
        self.update_progress.setStyleSheet(
            "QProgressBar{background:#e4eef5;border:none;border-radius:3px;}"
            f"QProgressBar::chunk{{background:{PRIMARY_GRADIENT};border-radius:3px;}}"
        )
        lay.addWidget(self.lbl_update, 1)
        lay.addWidget(self.update_progress)
        return bar

    # ------------------------------- 页面容器 ---------------------------- #
    def _page(self, title: str = "", subtitle: str = "") -> tuple[QWidget, QVBoxLayout]:
        """创建一个内容页，内部为垂直排列的卡片。"""
        page = QWidget()
        page.setStyleSheet(f"background:{PAGE_BG};")
        outer = QVBoxLayout(page)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(16)
        outer.setAlignment(Qt.AlignmentFlag.AlignTop)
        if title:
            hero = HeroBanner(title, subtitle)
            outer.addWidget(hero)
        return page, outer

    def _card(self, parent: QWidget, title: str | None = None) -> tuple[QFrame, QVBoxLayout]:
        """创建一个内容卡片。"""
        card = QFrame()
        card.setObjectName("card")
        card.setStyleSheet(_card_style())
        lay = QVBoxLayout(card)
        lay.setContentsMargins(22, 20, 22, 20)
        lay.setSpacing(14)
        if title:
            head = QLabel(title)
            head.setStyleSheet(
                f"font-size:15px;font-weight:800;color:{TEXT_PRIMARY};background:transparent;"
            )
            lay.addWidget(head)
        return card, lay

    # --------------------------- 板块一：环境监测 -------------------------- #
    def _build_env_page(self) -> QWidget:
        page, outer = self._page(
            "工作台",
            "启动前检查依赖、配置与网络连通性，只提示是否就绪。",
        )

        # 状态行卡片
        status_card, status_lay = self._card(page)
        status_row = QHBoxLayout()
        status_row.setSpacing(12)
        self.env_badge = QLabel("检测中…")
        self.env_badge.setStyleSheet(
            f"font-size:13px;font-weight:750;color:{TEXT_MUTED};"
            f"background:#f4f8fb;border-radius:10px;padding:6px 14px;"
        )
        status_row.addWidget(self.env_badge)
        status_row.addStretch(1)
        self.btn_env_run = QPushButton(f"{ICONS['reload']}  重新检测")
        self.btn_env_run.setFont(_icon_font(12))
        self.btn_env_run.setFixedHeight(34)
        self.btn_env_run.setStyleSheet(_secondary_button())
        self.btn_env_run.clicked.connect(self.run_check)
        status_row.addWidget(self.btn_env_run)
        status_lay.addLayout(status_row)
        outer.addWidget(status_card)

        # 日志卡片
        log_card, log_lay = self._card(page, "体检明细")
        self.env_log = QTextEdit()
        self.env_log.setReadOnly(True)
        self.env_log.setFrameShape(QFrame.Shape.NoFrame)
        self.env_log.setStyleSheet(_log_view_style(light=True))
        self.env_log.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        log_lay.addWidget(self.env_log, 1)
        outer.addWidget(log_card, 1)
        return page

    def run_check(self) -> None:
        self._check_lines = []
        self.env_log.clear()
        self._append_env_log("开始环境体检…", TEXT_MUTED)
        self._set_env_badge("检测中…", TEXT_MUTED, "#f4f8fb")
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
            badge, color = f"{ICONS['check_circle']} 环境就绪", SUCCESS_TEXT
            bg = SUCCESS_BG
        elif fail == 0:
            badge, color = f"{ICONS['warning_circle']} 基本可用，有提醒", WARN_TEXT
            bg = WARN_BG
        else:
            badge, color = f"{ICONS['close_circle']} 环境未就绪", ERROR_TEXT
            bg = ERROR_BG
        self._set_env_badge(badge, color, bg)

        lines: list[tuple[str, str]] = []
        for c in report.checks:
            lines.append(self._check_line(c))
        lines.append(("", ""))
        lines.append((f"共 {len(report.checks)} 项：通过 {ok} · 提醒 {warn} · 失败 {fail}", TEXT_MUTED))
        lines.append(("", ""))
        self._pending_lines = lines
        self._check_timer.start()
        self.btn_env_run.setEnabled(True)

    def _check_line(self, c: core.CheckResult) -> tuple[str, str]:
        sym_color = {
            "ok": (ICONS["check_circle"], SUCCESS_TEXT),
            "warn": (ICONS["warning_circle"], WARN_TEXT),
            "fail": (ICONS["close_circle"], ERROR_TEXT),
        }.get(c.status, (ICONS["info_circle"], TEXT_MUTED))
        sym, color = sym_color
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
            self._append_env_log("", TEXT_MUTED)
        if not self._pending_lines:
            self._check_timer.stop()

    def _on_check_error(self, msg: str) -> None:
        self._check_timer.stop()
        self.btn_env_run.setEnabled(True)
        self._set_env_badge("检测失败", ERROR_TEXT, ERROR_BG)
        self._append_env_log(f"环境体检出错：{msg}", ERROR_TEXT)
        QMessageBox.critical(self, "体检出错", msg)

    def _set_env_badge(self, text: str, color: str, bg: str = "") -> None:
        self.env_badge.setText(text)
        self.env_badge.setStyleSheet(
            f"font-size:13px;font-weight:750;color:{color};"
            f"background:{bg or '#f4f8fb'};border-radius:10px;padding:6px 14px;"
        )

    def _append_env_log(self, text: str, color: str) -> None:
        cursor = self.env_log.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        html = f"<span style='color:{color};'>{_esc(text)}</span><br>"
        cursor.insertHtml(html)
        self.env_log.setTextCursor(cursor)
        self.env_log.ensureCursorVisible()

    # --------------------------- 板块二：进程管理 -------------------------- #
    def _build_process_page(self) -> QWidget:
        page, outer = self._page(
            "主程序进程",
            "启动、停止或重启本地主程序，并实时观测内存占用与运行时长。",
        )

        # 运行状态卡片：左侧环形内存仪表，右侧状态文案与操作
        state_card, state_lay = self._card(page, "运行状态")
        state_row = QHBoxLayout()
        state_row.setSpacing(22)

        self.proc_gauge = RingGauge()
        state_row.addWidget(self.proc_gauge)

        right_col = QVBoxLayout()
        right_col.setSpacing(12)
        badge_row = QHBoxLayout()
        badge_row.setSpacing(12)
        self.proc_badge = QLabel("检测中…")
        self.proc_badge.setStyleSheet(
            f"font-size:13px;font-weight:750;color:{TEXT_MUTED};"
            f"background:#f4f8fb;border-radius:10px;padding:6px 14px;"
        )
        badge_row.addWidget(self.proc_badge)
        self.proc_hint = QLabel("正在读取进程状态…")
        self.proc_hint.setWordWrap(True)
        self.proc_hint.setStyleSheet(
            f"font-size:12px;color:{TEXT_MUTED};background:transparent;"
        )
        badge_row.addWidget(self.proc_hint, 1)
        right_col.addLayout(badge_row)
        right_col.addStretch(1)

        # 操作按钮
        action_row = QHBoxLayout()
        action_row.setSpacing(10)
        self.btn_proc_start = QPushButton(f"{ICONS['play']}  启动主程序")
        self.btn_proc_stop = QPushButton(f"{ICONS['close_circle']}  停止")
        self.btn_proc_restart = QPushButton(f"{ICONS['reload']}  重启")
        self.btn_proc_start.setStyleSheet(_primary_button())
        self.btn_proc_stop.setStyleSheet(_danger_button())
        self.btn_proc_restart.setStyleSheet(_secondary_button())
        for btn in (self.btn_proc_start, self.btn_proc_stop, self.btn_proc_restart):
            btn.setFont(_icon_font(12))
            btn.setFixedHeight(36)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_proc_start.clicked.connect(self.on_start)
        self.btn_proc_stop.clicked.connect(self.on_stop)
        self.btn_proc_restart.clicked.connect(self.on_restart)
        action_row.addWidget(self.btn_proc_start)
        action_row.addWidget(self.btn_proc_stop)
        action_row.addWidget(self.btn_proc_restart)
        action_row.addStretch(1)
        self.btn_proc_refresh = QPushButton(f"{ICONS['reload']}  刷新状态")
        self.btn_proc_refresh.setFont(_icon_font(12))
        self.btn_proc_refresh.setFixedHeight(36)
        self.btn_proc_refresh.setStyleSheet(_secondary_button())
        self.btn_proc_refresh.clicked.connect(lambda: self._refresh_process_page())
        action_row.addWidget(self.btn_proc_refresh)
        right_col.addLayout(action_row)

        state_row.addLayout(right_col, 1)
        state_lay.addLayout(state_row)
        outer.addWidget(state_card)

        # 指标卡片：PID / 端口 / 运行时长 / 内存占用 / 内存峰值
        metric_card, metric_lay = self._card(page, "运行指标")
        metric_row = QHBoxLayout()
        metric_row.setSpacing(12)
        self.proc_metrics: dict[str, QLabel] = {}
        for key, caption in (
            ("pid", "进程 PID"),
            ("port", "监听端口"),
            ("uptime", "运行时长"),
            ("memory", "内存占用"),
            ("peak", "内存峰值"),
        ):
            box = QFrame()
            box.setObjectName("metricBox")
            box.setStyleSheet(
                "QFrame#metricBox{background:#f7fbfd;border:1px solid #e4eef5;border-radius:14px;}"
            )
            box_lay = QVBoxLayout(box)
            box_lay.setContentsMargins(14, 12, 14, 12)
            box_lay.setSpacing(4)
            cap = QLabel(caption)
            cap.setStyleSheet(
                f"font-size:11px;color:{TEXT_MUTED};background:transparent;"
            )
            value = QLabel("—")
            value.setStyleSheet(
                f"font-size:15px;font-weight:800;color:{TEXT_PRIMARY};background:transparent;"
            )
            box_lay.addWidget(cap)
            box_lay.addWidget(value)
            metric_row.addWidget(box, 1)
            self.proc_metrics[key] = value
        metric_lay.addLayout(metric_row)
        outer.addWidget(metric_card)

        # 进程事件卡片
        log_card, log_lay = self._card(page, "进程事件")
        self.proc_log = QTextEdit()
        self.proc_log.setReadOnly(True)
        self.proc_log.setFrameShape(QFrame.Shape.NoFrame)
        self.proc_log.setStyleSheet(_log_view_style(light=True))
        log_lay.addWidget(self.proc_log, 1)
        outer.addWidget(log_card, 1)
        return page

    def _refresh_process_page(self, state: str | None = None) -> None:
        """刷新进程页指标与按钮可用态；``state`` 已知时复用，避免重复探测端口。"""
        if not hasattr(self, "proc_badge"):
            return
        if state is None:
            state = self._product.state()
        pid = self._product.active_pid()
        memory = self._product.memory_bytes() if pid is not None else None
        peak = self._product.peak_memory_bytes()
        owned = self._product.is_owned()

        if state == STATE_RUNNING:
            self.proc_badge.setText(f"{ICONS['check_circle']}  运行中")
            self.proc_badge.setStyleSheet(
                f"font-size:13px;font-weight:750;color:{SUCCESS_TEXT};"
                f"background:{SUCCESS_BG};border-radius:10px;padding:6px 14px;"
            )
            if owned:
                self.proc_hint.setText(f"主程序正在监听端口 {self._product.port}。")
            elif pid is not None:
                self.proc_hint.setText(
                    f"主程序在运行（PID {pid}），由端口 {self._product.port} 识别，可直接停止。"
                )
            else:
                self.proc_hint.setText(f"主程序在运行，端口 {self._product.port} 已被占用。")
        elif state == STATE_STARTING:
            self.proc_badge.setText(f"{ICONS['sync']}  启动中")
            self.proc_badge.setStyleSheet(
                f"font-size:13px;font-weight:750;color:{WARN_TEXT};"
                f"background:{WARN_BG};border-radius:10px;padding:6px 14px;"
            )
            self.proc_hint.setText(f"已拉起进程，等待端口 {self._product.port} 就绪…")
        else:
            self.proc_badge.setText(f"{ICONS['close_circle']}  未运行")
            self.proc_badge.setStyleSheet(
                f"font-size:13px;font-weight:750;color:{TEXT_MUTED};"
                f"background:#f4f8fb;border-radius:10px;padding:6px 14px;"
            )
            saved = self._product.saved_memory_bytes()
            self.proc_hint.setText(
                f"主程序未运行，上次退出已释放约 {_fmt_bytes(saved)} 内存。"
                if saved
                else "主程序未运行，点击「启动主程序」开始。"
            )

        uptime = self._product.uptime()
        self.proc_metrics["pid"].setText(str(pid) if pid is not None else "—")
        self.proc_metrics["port"].setText(str(self._product.port))
        self.proc_metrics["uptime"].setText(
            _fmt_duration(uptime) if uptime and (pid is not None or state == STATE_RUNNING) else "—"
        )
        self.proc_metrics["memory"].setText(_fmt_bytes(memory) if memory else "—")
        self.proc_metrics["peak"].setText(_fmt_bytes(peak) if peak else "—")
        self.proc_gauge.set_reading(memory, peak)

        busy = self._proc_worker is not None and self._proc_worker.isRunning()
        if busy:
            self.proc_hint.setText(f"正在{self._proc_action}主程序，请稍候…")
        running = state != STATE_STOPPED
        self.btn_proc_start.setEnabled(not busy and state == STATE_STOPPED)
        self.btn_proc_stop.setEnabled(not busy and running)
        self.btn_proc_restart.setEnabled(not busy and running)

    def _append_proc_log(self, text: str, color: str) -> None:
        if not hasattr(self, "proc_log"):
            return
        stamp = time.strftime("%H:%M:%S")
        cursor = self.proc_log.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        cursor.insertHtml(f"<span style='color:{color};'>[{stamp}] {_esc(text)}</span><br>")
        self.proc_log.setTextCursor(cursor)
        self.proc_log.ensureCursorVisible()

    # --------------------------- 板块三：资源管理 -------------------------- #
    def _build_resource_page(self) -> QWidget:
        page, outer = self._page(
            "本地文件资源",
            "管理产品处理 / POD / 产品库等个人资产，可清理、导出或导入。",
        )

        # 统计概览卡片
        stat_card, stat_lay = self._card(page, "资源概览")
        stat_row = QHBoxLayout()
        stat_row.setSpacing(16)
        self.res_total = QLabel("扫描中…")
        self.res_total.setStyleSheet(f"font-size:16px;font-weight:800;color:{TEXT_PRIMARY};")
        self.res_hint = QLabel("正在统计各模块本地资产占用…")
        self.res_hint.setStyleSheet(f"font-size:12px;color:{TEXT_MUTED};")
        stat_row.addWidget(self.res_total)
        stat_row.addStretch(1)
        stat_row.addWidget(self.res_hint)
        stat_lay.addLayout(stat_row)
        outer.addWidget(stat_card)

        # 表格卡片
        table_card, table_lay = self._card(page, "资产明细")
        self.res_table = QTableWidget(0, 4)
        self.res_table.setHorizontalHeaderLabels(["类别", "文件数", "占用", "数据行"])
        self.res_table.setAlternatingRowColors(True)
        self.res_table.setShowGrid(False)
        self.res_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.res_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.res_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.res_table.verticalHeader().setVisible(False)
        self.res_table.verticalHeader().setDefaultSectionSize(38)
        header = self.res_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        self.res_table.setStyleSheet(_table_style())
        table_lay.addWidget(self.res_table, 1)

        # 操作按钮
        row = QHBoxLayout()
        row.setSpacing(10)
        self.btn_res_refresh = QPushButton(f"{ICONS['reload']}  刷新")
        self.btn_res_clean = QPushButton(f"{ICONS['delete']}  清理选中")
        self.btn_res_export = QPushButton(f"{ICONS['upload']}  导出")
        self.btn_res_import = QPushButton(f"{ICONS['file_text']}  导入")
        self.btn_res_clean.setStyleSheet(_danger_button())
        for b in (self.btn_res_refresh, self.btn_res_export, self.btn_res_import):
            b.setFont(_icon_font(12))
            b.setFixedHeight(34)
            b.setStyleSheet(_secondary_button())
        self.btn_res_clean.setFont(_icon_font(12))
        self.btn_res_clean.setFixedHeight(34)
        row.addWidget(self.btn_res_refresh)
        row.addWidget(self.btn_res_clean)
        row.addWidget(self.btn_res_export)
        row.addWidget(self.btn_res_import)
        row.addStretch(1)
        table_lay.addLayout(row)
        outer.addWidget(table_card, 1)

        self.btn_res_refresh.clicked.connect(self.console_scan)
        self.btn_res_clean.clicked.connect(self.console_clean)
        self.btn_res_export.clicked.connect(self.console_export)
        self.btn_res_import.clicked.connect(self.console_import)

        # 延迟触发首次扫描，避免启动阻塞
        QTimer.singleShot(300, self.console_scan)
        return page

    def console_scan(self) -> None:
        self.btn_res_refresh.setEnabled(False)
        self.res_total.setText("扫描中…")
        self.res_hint.setText("正在统计各模块本地资产占用…")
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
            name_item.setFlags(
                Qt.ItemFlag.ItemIsEnabled
                | Qt.ItemFlag.ItemIsUserCheckable
                | Qt.ItemFlag.ItemIsSelectable
            )
            name_item.setCheckState(Qt.CheckState.Unchecked)
            if not s.get("exists"):
                name_item.setForeground(QColor(TEXT_MUTED))

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
            self,
            "确认清理",
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
        out, _ = QFileDialog.getSaveFileName(
            self, "导出资源备份", "mainpg-resources.zip", "Zip (*.zip)"
        )
        if not out:
            return
        res = console.export_archive(ids, Path(out))
        QMessageBox.information(self, "导出", res.get("message", str(res)))

    def console_import(self) -> None:
        arch, _ = QFileDialog.getOpenFileName(self, "选择备份文件", "", "Zip (*.zip)")
        if not arch:
            return
        ok = QMessageBox.question(
            self,
            "确认导入",
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

    # --------------------------- 板块四：版本更新 -------------------------- #
    def _build_update_page(self) -> QWidget:
        page, outer = self._page(
            "版本更新检查",
            "检测当前是否为最新版本，非最新则从官网下载并启动安装器。",
        )

        # 当前版本卡片
        current_card, current_lay = self._card(page, "当前版本")
        current_row = QHBoxLayout()
        self.update_current = QLabel("当前版本 v" + update.current_version())
        self.update_current.setStyleSheet(f"font-size:15px;font-weight:800;color:{TEXT_PRIMARY};")
        current_row.addWidget(self.update_current)
        current_row.addStretch(1)
        self.btn_update_check = QPushButton(f"{ICONS['sync']}  检查更新")
        self.btn_update_check.setFont(_icon_font(12))
        self.btn_update_check.setFixedHeight(34)
        self.btn_update_check.setStyleSheet(_secondary_button())
        self.btn_update_check.clicked.connect(self.run_update_check)
        current_row.addWidget(self.btn_update_check)
        current_lay.addLayout(current_row)
        outer.addWidget(current_card)

        # 结果卡片
        result_card, result_lay = self._card(page, "检查结果")
        self.update_result = QTextEdit()
        self.update_result.setReadOnly(True)
        self.update_result.setFrameShape(QFrame.Shape.NoFrame)
        self.update_result.setStyleSheet(_log_view_style(light=True))
        self.update_result.setMaximumHeight(200)
        self.update_result.setPlainText("启动时已自动检查更新，正在检测最新版本…")
        result_lay.addWidget(self.update_result, 1)
        outer.addWidget(result_card)

        # 已下载安装包卡片：支持安装 / 重装 / 回滚
        pkg_card, pkg_lay = self._card(page, "已下载安装包")
        pkg_hint = QLabel(
            f"下载过的历史安装包会保留最近 {update.UPDATE_KEEP} 个，"
            f"新版异常时可回滚到旧版本。双击行可直接安装。"
        )
        pkg_hint.setStyleSheet(f"font-size:12px;color:{TEXT_MUTED};background:transparent;")
        pkg_lay.addWidget(pkg_hint)

        self.pkg_table = QTableWidget(0, 4)
        self.pkg_table.setHorizontalHeaderLabels(["版本", "大小", "下载时间", "状态"])
        self.pkg_table.setAlternatingRowColors(True)
        self.pkg_table.setShowGrid(False)
        self.pkg_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.pkg_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.pkg_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.pkg_table.verticalHeader().setVisible(False)
        self.pkg_table.verticalHeader().setDefaultSectionSize(34)
        pkg_header = self.pkg_table.horizontalHeader()
        pkg_header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        pkg_header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        pkg_header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        pkg_header.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        self.pkg_table.setStyleSheet(_table_style())
        self.pkg_table.itemDoubleClicked.connect(lambda _item: self.on_pkg_install())
        pkg_lay.addWidget(self.pkg_table, 1)

        pkg_row = QHBoxLayout()
        pkg_row.setSpacing(10)
        self.btn_pkg_install = QPushButton(f"{ICONS['play']}  安装 / 重装")
        self.btn_pkg_rollback = QPushButton(f"{ICONS['reload']}  回滚到该版本")
        self.btn_pkg_delete = QPushButton(f"{ICONS['delete']}  删除安装包")
        self.btn_pkg_refresh = QPushButton(f"{ICONS['sync']}  刷新列表")
        self.btn_pkg_install.setStyleSheet(_primary_button())
        self.btn_pkg_rollback.setStyleSheet(_secondary_button())
        self.btn_pkg_delete.setStyleSheet(_danger_button())
        self.btn_pkg_refresh.setStyleSheet(_secondary_button())
        for btn in (
            self.btn_pkg_install,
            self.btn_pkg_rollback,
            self.btn_pkg_delete,
            self.btn_pkg_refresh,
        ):
            btn.setFont(_icon_font(12))
            btn.setFixedHeight(34)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_pkg_install.clicked.connect(self.on_pkg_install)
        self.btn_pkg_rollback.clicked.connect(self.on_pkg_rollback)
        self.btn_pkg_delete.clicked.connect(self.on_pkg_delete)
        self.btn_pkg_refresh.clicked.connect(self._refresh_installers)
        pkg_row.addWidget(self.btn_pkg_install)
        pkg_row.addWidget(self.btn_pkg_rollback)
        pkg_row.addWidget(self.btn_pkg_delete)
        pkg_row.addStretch(1)
        pkg_row.addWidget(self.btn_pkg_refresh)
        pkg_lay.addLayout(pkg_row)
        outer.addWidget(pkg_card, 1)

        QTimer.singleShot(200, self._refresh_installers)
        return page

    def _refresh_installers(self) -> None:
        """刷新已下载安装包列表。"""
        if not hasattr(self, "pkg_table"):
            return
        items = update.downloaded_installers()
        self.pkg_table.setRowCount(0)
        for item in items:
            row = self.pkg_table.rowCount()
            self.pkg_table.insertRow(row)
            version_item = QTableWidgetItem("v" + item["version"])
            version_item.setData(Qt.ItemDataRole.UserRole, item["path"])
            self.pkg_table.setItem(row, 0, version_item)
            self.pkg_table.setItem(row, 1, QTableWidgetItem(_fmt_bytes(item["size"])))
            self.pkg_table.setItem(
                row,
                2,
                QTableWidgetItem(
                    time.strftime("%Y-%m-%d %H:%M", time.localtime(item["modified"]))
                ),
            )
            state_item = QTableWidgetItem("当前运行版本" if item["current"] else "可安装 / 回滚")
            if item["current"]:
                state_item.setForeground(QColor(TEXT_MUTED))
            self.pkg_table.setItem(row, 3, state_item)
        if items:
            self.pkg_table.selectRow(0)
        for btn in (
            self.btn_pkg_install,
            self.btn_pkg_rollback,
            self.btn_pkg_delete,
        ):
            btn.setEnabled(bool(items))

    def _selected_installer(self) -> tuple[str, str] | None:
        """返回当前选中行的 (安装包路径, 展示名)。"""
        row = self.pkg_table.currentRow()
        if row < 0:
            return None
        item = self.pkg_table.item(row, 0)
        if item is None:
            return None
        path = item.data(Qt.ItemDataRole.UserRole)
        if not path:
            return None
        return str(path), item.text()

    def on_pkg_install(self) -> None:
        self._launch_selected_installer("安装")

    def on_pkg_rollback(self) -> None:
        self._launch_selected_installer("回滚")

    def _launch_selected_installer(self, action: str) -> None:
        selected = self._selected_installer()
        if selected is None:
            QMessageBox.information(self, action, "请先在列表中选择一个安装包。")
            return
        path, label = selected
        if not Path(path).is_file():
            QMessageBox.warning(self, action, "安装包已不存在，已刷新列表。")
            self._refresh_installers()
            return
        if self._product.is_listening():
            QMessageBox.warning(
                self,
                action,
                "主程序正在运行，请先停止后再安装，避免替换文件失败。",
            )
            return
        ok = QMessageBox.question(
            self,
            f"{action}更新",
            f"将启动 {label} 的安装程序：\n\n{path}\n\n"
            f"安装会替换当前版本的程序文件，请确认已保存工作内容。是否继续？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if ok != QMessageBox.StandardButton.Yes:
            return
        try:
            update.launch_installer(Path(path))
        except OSError as exc:
            QMessageBox.critical(self, f"{action}失败", str(exc))
            return
        self.update_result.setPlainText(f"已启动 {label} 的安装程序：{path}")
        self.lbl_update.setText(f"启动自检更新：正在{action} {label}")

    def on_pkg_delete(self) -> None:
        selected = self._selected_installer()
        if selected is None:
            QMessageBox.information(self, "删除", "请先在列表中选择一个安装包。")
            return
        path, label = selected
        ok = QMessageBox.question(
            self,
            "删除安装包",
            f"确定删除本地保存的 {label} 安装包？\n\n{path}\n\n删除后需要重新下载才能安装。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if ok != QMessageBox.StandardButton.Yes:
            return
        try:
            Path(path).unlink()
        except OSError as exc:
            QMessageBox.warning(self, "删除失败", str(exc))
            return
        self._refresh_installers()

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
            self.update_result.setPlainText(f"{ICONS['check_circle']} 当前已是最新版本，无需更新。")
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
        dlg.setMinimumWidth(420)
        lay = QVBoxLayout(dlg)
        lay.setSpacing(14)
        head = QLabel(f"发现新版本 v{release.version}")
        head.setStyleSheet(
            f"font-size:18px;font-weight:800;color:{TEXT_PRIMARY};"
        )
        lay.addWidget(head)
        if release.mandatory:
            warn = QLabel("此为强制更新，需更新后继续使用。")
            warn.setStyleSheet(f"color:{ERROR_TEXT};font-weight:700;")
            lay.addWidget(warn)
        notes = QTextEdit()
        notes.setReadOnly(True)
        notes.setPlainText(release.release_notes or "本次更新提升了稳定性与性能，建议尽快升级。")
        notes.setFixedHeight(120)
        notes.setStyleSheet(_log_view_style(light=True))
        lay.addWidget(notes)
        meta = QLabel(
            f"发布时间：{release.published_at}　·　"
            f"当前 v{update.current_version()} → v{release.version}"
        )
        meta.setStyleSheet(f"color:{TEXT_MUTED};font-size:11px;")
        lay.addWidget(meta)
        row = QHBoxLayout()
        row.setSpacing(10)
        btn_dl = QPushButton("下载更新")
        btn_dl.setStyleSheet(_primary_button())
        btn_dl.setDefault(True)
        btn_dl.setFixedHeight(36)
        row.addWidget(btn_dl)
        btn_later = QPushButton("稍后")
        btn_later.setStyleSheet(_secondary_button())
        btn_later.setFixedHeight(36)
        row.addWidget(btn_later)
        if not release.mandatory:
            btn_snooze = QPushButton("暂缓")
            btn_snooze.setStyleSheet(_secondary_button())
            btn_snooze.setFixedHeight(36)
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
            self,
            "更新已就绪",
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
                if pct is not None
                else "启动自检更新：正在下载更新…"
            )
        elif phase == "full":
            bar.setRange(0, 1000)
            bar.setValue(1000)
        elif phase == "zero":
            bar.setRange(0, 1000)
            bar.setValue(0)

    # --------------------------- 板块五：日志上传 -------------------------- #
    def _build_log_page(self) -> QWidget:
        page, outer = self._page(
            "日志上报",
            "登录后可将本地 runtime.log 上报到服务器，便于技术支持定位问题。",
        )

        # 登录卡片
        login_card, login_lay = self._card(page, "账号登录")
        login_box = QFrame()
        login_box.setObjectName("loginBox")
        login_box.setStyleSheet(
            f"QFrame#loginBox{{background:#f7fbfd;border-radius:14px;border:1px solid #e4eef5;}}"
            f"QLineEdit{{{_input_style()}}}"
        )
        l_lay = QGridLayout(login_box)
        l_lay.setContentsMargins(16, 16, 16, 16)
        l_lay.setHorizontalSpacing(12)
        l_lay.setVerticalSpacing(12)
        lbl_u = QLabel("账号")
        lbl_u.setStyleSheet(f"font-size:12px;color:{TEXT_SECONDARY};font-weight:700;")
        self.log_username = QLineEdit()
        self.log_username.setPlaceholderText("用户名 / 邮箱")
        self.log_username.setFixedHeight(36)
        self.log_username.setStyleSheet(_input_style())
        lbl_p = QLabel("密码")
        lbl_p.setStyleSheet(f"font-size:12px;color:{TEXT_SECONDARY};font-weight:700;")
        self.log_password = QLineEdit()
        self.log_password.setEchoMode(QLineEdit.EchoMode.Password)
        self.log_password.setPlaceholderText("账户密码")
        self.log_password.setFixedHeight(36)
        self.log_password.setStyleSheet(_input_style())
        self.btn_log_login = QPushButton(f"{ICONS['user']}  登录")
        self.btn_log_login.setFont(_icon_font(12))
        self.btn_log_login.setFixedHeight(36)
        self.btn_log_login.setStyleSheet(_secondary_button())
        self.btn_log_login.clicked.connect(self.on_log_login)
        self.btn_log_upload = QPushButton(f"{ICONS['upload']}  上传日志")
        self.btn_log_upload.setFont(_icon_font(12))
        self.btn_log_upload.setFixedHeight(36)
        self.btn_log_upload.setStyleSheet(_primary_button())
        self.btn_log_upload.setEnabled(False)
        self.btn_log_upload.clicked.connect(self.on_log_upload)
        l_lay.addWidget(lbl_u, 0, 0)
        l_lay.addWidget(self.log_username, 0, 1)
        l_lay.addWidget(lbl_p, 1, 0)
        l_lay.addWidget(self.log_password, 1, 1)
        l_lay.addWidget(self.btn_log_login, 2, 0)
        l_lay.addWidget(self.btn_log_upload, 2, 1)
        login_lay.addWidget(login_box)
        outer.addWidget(login_card)

        # 状态与结果卡片
        result_card, result_lay = self._card(page, "日志状态")
        self.log_status = QLabel("未登录")
        self.log_status.setStyleSheet(f"font-size:12px;color:{TEXT_MUTED};")
        result_lay.addWidget(self.log_status)
        self.log_result = QTextEdit()
        self.log_result.setReadOnly(True)
        self.log_result.setFrameShape(QFrame.Shape.NoFrame)
        self.log_result.setStyleSheet(_log_view_style(light=True))
        log_path = logupload.runtime_log_path()
        self.log_result.setPlainText(
            f"日志文件：{log_path}\n存在："
            + ("是" if log_path.exists() else "否")
            + "，登录后即可上传。"
        )
        result_lay.addWidget(self.log_result, 1)
        outer.addWidget(result_card, 1)
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
        name = (
            account.get("username")
            or account.get("email")
            or account.get("display_name")
            or "用户"
        )
        self.log_status.setText(f"已登录：{name}")
        self.log_status.setStyleSheet(f"font-size:12px;color:{SUCCESS_TEXT};font-weight:700;")
        self.log_result.setPlainText("登录成功，可以上传日志。")

    def _on_log_login_error(self, msg: str) -> None:
        self.btn_log_login.setEnabled(True)
        self.log_status.setText("登录失败")
        self.log_status.setStyleSheet(f"font-size:12px;color:{ERROR_TEXT};")
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
            self,
            "确认上传",
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
        self.log_status.setStyleSheet(f"font-size:12px;color:{SUCCESS_TEXT};font-weight:700;")
        log_id = result.get("upload_id", "") or result.get("id", "")
        self.log_result.append(f"{ICONS['check_circle']} 上传成功，记录ID：{log_id}")

    def _on_log_upload_error(self, msg: str) -> None:
        self.btn_log_upload.setEnabled(True)
        self.btn_log_login.setEnabled(True)
        self.log_status.setText("上传失败")
        self.log_status.setStyleSheet(f"font-size:12px;color:{ERROR_TEXT};")
        self.log_result.append(f"上传失败：{msg}")
        QMessageBox.critical(self, "上传失败", msg)

    # ------------------------------------------------------ 主程序进程控制
    def on_start(self) -> None:
        self._run_product_action("start")

    def on_stop(self) -> None:
        # 句柄不可用时按端口反查，用户手动启动的实例同样可以直接停止
        pid = self._product.active_pid()
        if pid is None:
            if self._product.is_listening():
                QMessageBox.warning(
                    self,
                    "无法停止",
                    f"端口 {self._product.port} 已被占用，但无法定位对应进程，请手动关闭。",
                )
            else:
                QMessageBox.information(self, "停止", "主程序当前未运行。")
            return
        ok = QMessageBox.question(
            self,
            "停止主程序",
            f"将结束主程序（PID {pid}）并回收其内存与子进程。是否继续？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if ok != QMessageBox.StandardButton.Yes:
            return
        self._run_product_action("stop")

    def on_restart(self) -> None:
        self._run_product_action("restart")

    def _run_product_action(self, action: str) -> None:
        """把启动 / 停止 / 重启放到后台线程执行，避免等待进程回收阻塞界面。"""
        if self._proc_worker is not None and self._proc_worker.isRunning():
            QMessageBox.information(self, "请稍候", "上一个进程操作尚未完成，请稍候。")
            return
        if action == "start" and core.find_product() is None:
            QMessageBox.warning(
                self,
                "未找到主程序",
                "未找到 MainPG.exe。请确认产品已安装，或用 WH_APP_EXE 指定路径。",
            )
            return
        if action == "start":
            # 启动预检（适配三件套）：E001 端口占用 / E002 数据目录不可写 → 失败页并阻断；
            # E003 网络不通 → 仅提示，不阻断（离线可启动）。
            preflight = core.startup_preflight()
            blocking = core.first_blocking_error(preflight)
            if blocking is not None:
                code = core.error_code_of(blocking.message) or "E000"
                core.record_startup_failure(code, blocking.message)
                self._append_proc_log(
                    f"{ICONS['close_circle']}  启动预检失败 {code}：{blocking.message}", ERROR_TEXT
                )
                self._show_preflight_failure(code, blocking.message)
                return
            for item in preflight:
                if item.status == "warn":
                    self._append_proc_log(f"⚠ 预检提示：{item.message}", TEXT_MUTED)
        self._proc_action = {"start": "启动", "stop": "停止", "restart": "重启"}[action]
        self._append_proc_log(f"正在{self._proc_action}主程序…", TEXT_MUTED)
        self._proc_worker = ProductActionWorker(self._product, action)
        self._proc_worker.done.connect(self._on_proc_done)
        self._proc_worker.error.connect(self._on_proc_error)
        self._proc_worker.start()
        self._refresh_sidebar_status()

    def _show_preflight_failure(self, code: str, message: str) -> None:
        """启动预检失败页：错误码 + 原因 + 解决办法 + 复制诊断信息。"""
        fix = core.ERR_FIX_HINTS.get(code, "请重启电脑后重试；仍失败请联系客服并附上诊断信息。")
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Critical)
        box.setWindowTitle(f"启动失败（{code}）")
        box.setText(f"环境自检未通过（{code}），已阻止启动以避免白屏。")
        box.setInformativeText(f"{message}\n\n解决办法：{fix}\n\n失败记录已保存，可通过「日志上传」一并上报。")
        box.setDetailedText(f"错误码: {code}\n详情: {message}\n时间: {time.strftime('%Y-%m-%d %H:%M:%S')}")
        copy_btn = box.addButton("复制诊断信息", QMessageBox.ButtonRole.ActionRole)
        box.addButton("关闭", QMessageBox.ButtonRole.RejectRole)
        box.exec()
        if box.clickedButton() is copy_btn:
            QtWidgets.QApplication.clipboard().setText(f"[{code}] {message}\n解决办法：{fix}")

    def _on_proc_done(self, _action: str, message: str) -> None:
        self._proc_worker = None
        self._proc_action = ""
        self._append_proc_log(f"{ICONS['check_circle']}  {message}", SUCCESS_TEXT)
        pending = core.consume_startup_failures()
        if pending:
            last = pending[-1]
            self._append_proc_log(
                f"检测到 {len(pending)} 条历史启动失败记录（最近 {last.get('code', '?')}），"
                f"已并入日志，可通过「日志上传」上报", TEXT_MUTED
            )
        self._refresh_sidebar_status()

    def _on_proc_error(self, _action: str, message: str) -> None:
        self._proc_worker = None
        self._proc_action = ""
        self._append_proc_log(f"{ICONS['close_circle']}  {message}", ERROR_TEXT)
        self._refresh_sidebar_status()
        QMessageBox.warning(self, "进程操作未完成", message)

    def closeEvent(self, event: Any) -> None:  # noqa: N802 - Qt 命名约定
        """退出前处理主程序：本启动器拉起的实例可一并结束并回收内存。"""
        if self._proc_worker is not None and self._proc_worker.isRunning():
            QMessageBox.information(self, "请稍候", "进程操作正在进行，请稍候再退出。")
            event.ignore()
            return
        pid = self._product.active_pid()
        if pid is None and not self._product.is_listening():
            event.accept()
            return

        box = QMessageBox(self)
        box.setWindowTitle("退出启动器")
        box.setIcon(QMessageBox.Icon.Question)
        stop_btn = None
        if pid is not None:
            box.setText("主程序正在运行，退出启动器时如何处理？")
            box.setInformativeText("选择「结束主程序」会回收其内存与子进程。")
            stop_btn = box.addButton("结束主程序并退出", QMessageBox.ButtonRole.AcceptRole)
            box.addButton("仅退出启动器", QMessageBox.ButtonRole.DestructiveRole)
        else:
            box.setText(f"端口 {self._product.port} 已被占用，但无法定位对应进程。")
            box.setInformativeText("退出启动器不会结束该实例。")
            box.addButton("退出启动器", QMessageBox.ButtonRole.AcceptRole)
        cancel_btn = box.addButton("取消", QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(cancel_btn)
        box.exec()

        clicked = box.clickedButton()
        if clicked is cancel_btn:
            event.ignore()
            return
        if stop_btn is not None and clicked is stop_btn:
            self._product.stop()
        event.accept()


# --------------------------- 入口 ----------------------------------------- #


def _use_light_titlebar(window: QWidget) -> None:
    """把原生标题栏固定为浅色。

    系统处于深色模式时 Qt 会把标题栏画成黑色，与浅色界面之间形成一条黑边，
    这里直接关掉 DWM 的深色标题栏标记。
    """
    if sys.platform != "win32":
        return
    try:
        import ctypes

        value = ctypes.c_int(0)  # 0 = 浅色标题栏
        hwnd = int(window.winId())
        for attr in (20, 19):  # 20: Win11 22H2+ / 19: 早期 Win10
            if ctypes.windll.dwmapi.DwmSetWindowAttribute(
                hwnd, attr, ctypes.byref(value), ctypes.sizeof(value)
            ) == 0:
                break
    except Exception:  # noqa: BLE001
        pass


def main() -> int:
    app = QtWidgets.QApplication(sys.argv)
    app.setFont(QFont("Microsoft YaHei", 10))

    # 应用图标（标题栏 + 任务栏，含 splash 与主窗口）
    app.setWindowIcon(_app_icon())

    # 加载图标字体
    _load_iconfont()

    # 启动画面
    splash = SplashScreen()
    splash.show()
    splash.raise_()
    splash.activateWindow()
    app.processEvents()

    # 模拟加载进度
    for pct in (20, 45, 70, 90, 100):
        splash.set_progress(pct)
        app.processEvents()
        # 简单延迟，保持画面刷新
        loop = QEventLoop()
        QTimer.singleShot(180, loop.quit)
        loop.exec()

    # 创建主窗口
    win = MainWindow()
    _use_light_titlebar(win)

    # 淡出 splash 后显示主窗口
    def _show_main() -> None:
        win.show()
        win.raise_()
        win.activateWindow()
        splash.fade_out()

    QTimer.singleShot(1200, _show_main)
    return app.exec()
