"""启动浮动卡片（PySide6 版）：桌面中间浮一张「田野 + 品牌 + 端口」的小卡片。

为什么改用 Qt 组件
------------------
上一版自己用 ctypes 调 ``UpdateLayeredWindow`` 画，结果在窗口销毁上直接踩坑
（``DestroyWindow`` 只能由创建线程调用，卡片压在浏览器上不消失）。这类活应该
交给成熟组件：

* ``launcher/ui_window.py`` 里本来就有一个 ``SplashScreen(QWidget)``，用的正是
  ``FramelessWindowHint`` + ``WA_TranslucentBackground`` + ``paintEvent`` 这一套；
* Qt 负责窗口生命周期、圆角裁剪、抗锯齿、动画、DPI，代码量少一个数量级。

代价：``MainPG.exe`` 要带上 PySide6（已按 launcher 的做法做裁剪）。

线程模型
--------
Qt 要求 ``QApplication`` 和窗口在同一事件循环里，所以 **Qt 跑在独立线程**，
主线程照常 import 后端 + 起 uvicorn。跨线程一律走 ``Signal``（Qt 自动队列化，
不会跨线程直接碰窗口）。

平台
----
非 Windows 或 Qt 不可用时 ``splash_available()`` 返回 False，调用方回落成
「延迟 2.5 秒开浏览器」的老行为。
"""

from __future__ import annotations

import logging
import os
import random
import sys
import threading
import time
from pathlib import Path
from typing import Callable

logger = logging.getLogger("run_workbench.splash")

_IS_WINDOWS = os.name == "nt"

CARD_WIDTH = 800
CARD_HEIGHT = 500
CARD_RADIUS = 18
PROBE_INTERVAL = 0.6
FADE_MS = 320
#: 端口冲突时等用户选择的上限；没人选就顺延（绝不擅自杀别人的进程）。
CONFLICT_TIMEOUT_SECONDS = 20.0

#: 启动阶段文案池。启动过程是确定性的（绑端口 → import 后端 → uvicorn），
#: 但「正在干什么」对用户没有分辨价值，逐字播报反而像日志。每次启动随机挑
#: 几条、按随机间隔轮播，纯粹让等待不那么死板 —— 别把它当真实进度。
_PHRASE_POOL = (
    "正在唤醒服务",
    "正在加载配置",
    "正在检查更新",
    "正在连接云端",
    "正在恢复工作区",
    "正在同步公告",
    "正在准备素材库",
    "正在校准图像引擎",
    "正在整理商品数据",
    "正在唤醒视频服务",
    "正在检查计费状态",
    "正在加载主题",
    "正在点亮界面",
    "正在准备浏览器",
    "正在整理缓存",
    "正在播种田野",
    "正在连接服务器",
    "正在读取本地数据",
    "正在重建索引",
    "正在预热模型",
    "正在加载字体",
    "正在检查账号状态",
    "正在同步消息",
    "正在恢复上次进度",
    "正在生成预览",
    "正在校验文件",
    "正在准备画布",
    "正在调整色板",
    "正在加载插件",
    "正在收尾",
)
#: 背景动画帧率：与素材抽帧时的 fps 一致（5 秒视频 → 60 帧）。
FRAME_FPS = 12
#: LRU 缓存的解码帧数：12 帧 × 1.5MB ≈ 18MB，足够覆盖一秒画面（全量缓存要 93MB）。
FRAME_CACHE = 12

#: 端口冲突时出现的两个按钮。主按钮用品牌绿强调，次按钮留白。
_BUTTON_STYLE = """
QPushButton {
  background: rgba(10, 26, 18, 185);
  color: rgba(255, 255, 255, 235);
  border: 1px solid rgba(255, 255, 255, 72);
  border-radius: 10px;
  padding: 8px 14px;
  font-family: "Microsoft YaHei";
  font-size: 13px;
}
QPushButton:hover { background: rgba(255, 255, 255, 38); }
QPushButton:pressed { background: rgba(255, 255, 255, 60); }
QPushButton#primary {
  background: rgba(99, 153, 34, 215);
  border-color: rgba(255, 255, 255, 90);
  font-weight: 600;
}
QPushButton#primary:hover { background: rgba(116, 172, 43, 235); }
"""


_splash_probe_error: str | None = None


def splash_available() -> bool:
    """当前环境能否使用 Qt 启动卡片。失败原因见 :func:`splash_unavailable_reason`。"""
    global _splash_probe_error
    if not _IS_WINDOWS:
        _splash_probe_error = "非 Windows 平台"
        return False
    try:  # noqa: SIM105 - 只是探测，装不上就退回老行为
        from PySide6 import QtWidgets  # noqa: F401
    except Exception as exc:  # noqa: BLE001
        # 这里绝不能只 `return False`：打包版是 windowed 模式，静默降级等于
        # 「卡片莫名不出现」，2026-10-21 排查 1.5.8 安装包时就卡在这上面。
        _splash_probe_error = f"{type(exc).__name__}: {exc}"
        logger.warning("PySide6 不可用：%s", _splash_probe_error, exc_info=True)
        return False
    _splash_probe_error = None
    return True


def splash_unavailable_reason() -> str | None:
    """上一次 :func:`splash_available` 返回 False 的原因；可用时为 None。"""
    return _splash_probe_error


def splash_asset_path() -> Path | None:
    """定位卡片底图（源码运行 vs PyInstaller 打包）。找不到返回 None。"""
    if not _IS_WINDOWS:
        return None
    try:
        if getattr(sys, "frozen", False):
            base = Path(getattr(sys, "_MEIPASS", "")) / "launcher" / "assets" / "splash"
        else:
            base = Path(__file__).resolve().parent / "assets" / "splash"
    except Exception:  # noqa: BLE001
        return None
    for name in ("splash-card.jpg", "splash-anime-poster.jpg"):
        candidate = base / name
        if candidate.is_file():
            return candidate
    return None


if _IS_WINDOWS:  # pragma: no cover - 平台相关，导入期即可确定
    try:
        from PySide6.QtCore import (
            QEasingCurve,
            QPropertyAnimation,
            QRectF,
            Qt,
            QTimer,
            Signal,
        )
        from PySide6.QtGui import (
            QColor,
            QFont,
            QImage,
            QLinearGradient,
            QPainter,
            QPainterPath,
        )
        from PySide6.QtWidgets import QApplication, QPushButton, QWidget

        _QT_OK = True
    except Exception:  # noqa: BLE001 - Qt 不可用时整套卡片跳过
        _QT_OK = False
else:  # pragma: no cover
    _QT_OK = False


if _QT_OK:

    class SplashCardWidget(QWidget):
        """无边框、置顶、背景透明的启动卡片。参考 launcher 的 SplashScreen。"""

        #: 跨线程触发的状态更新（Qt 自动转成队列连接，线程安全）。
        sig_set_port = Signal(int)
        sig_set_status = Signal(str, str)
        sig_finished = Signal()
        #: 进度（0.0~1.0）。关键节点由 run_workbench 打点，中间由卡片自己爬。
        sig_set_progress = Signal(float)
        #: 端口冲突询问（端口 / 占用者 / PID）；点击后由 sig_decision 回传。
        sig_conflict = Signal(int, str, int)
        sig_clear_conflict = Signal()
        sig_decision = Signal(str)

        def __init__(
            self,
            *,
            background: Path,
            version: str,
            host: str,
            port: int,
            width: int = CARD_WIDTH,
            height: int = CARD_HEIGHT,
        ) -> None:
            super().__init__()
            self._version = version or "unknown"
            self._host = host
            self._port = port
            self._status = "正在启动"
            self._note = ""
            self._finished = False
            # 启动没有真实百分比可拿（import 后端是黑盒），所以这里只做
            # 「关键节点打点 + 节点之间平滑插值」，见 set_progress()。
            self._progress = 0.08
            self._target_progress = 0.08
            # 每次启动随机挑 5~8 条轮播（纯装饰，见 _PHRASE_POOL 注释）。
            # 一旦有真实状态要显示（冲突/超时），_auto_phrase 关掉，文案不再被顶掉。
            self._phrases = random.sample(_PHRASE_POOL, random.randint(5, 8))
            self._phrase_index = 0
            self._auto_phrase = True
            self._status = self._phrases[0]

            self.setWindowFlags(
                Qt.WindowType.FramelessWindowHint
                | Qt.WindowType.WindowStaysOnTopHint
                | Qt.WindowType.Tool
            )
            self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
            # 鼠标穿透：启动画面不该挡住用户点别的东西
            self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
            self.setFixedSize(width, height)

            self._bg = QImage(str(background))
            self._frame_paths: list = []
            self._frame_cache: dict = {}
            self._frame_index = 0
            self._begin_load_frames(background.parent / "frames")

            screen = self.screen()
            area = screen.availableGeometry() if screen else None
            if area is None:
                from PySide6.QtGui import QGuiApplication

                primary = QGuiApplication.primaryScreen()
                area = primary.availableGeometry() if primary else QRectF(0, 0, 1920, 1080)
            self._base_x = int(area.center().x() - width / 2)
            self._base_y = int(area.center().y() - height / 2)
            self.move(self._base_x, self._base_y)

            self.sig_set_port.connect(self._apply_port)
            self.sig_set_status.connect(self._apply_status)
            self.sig_set_progress.connect(self._apply_progress)
            self.sig_finished.connect(self._fade_and_close)
            self.sig_conflict.connect(self._apply_conflict)
            self.sig_clear_conflict.connect(self._clear_conflict)

            # 端口冲突时才出现的两个选择按钮。平时隐藏、卡片保持鼠标穿透；
            # 真有冲突时才让卡片接住点击（用户 2026-10-19 选的折中方案）。
            self._conflict: dict | None = None
            self._btn_kill = QPushButton(f"关掉它，继续用 {self._port}", self)
            self._btn_kill.setObjectName("primary")
            self._btn_fallback = QPushButton("换个端口启动", self)
            for btn in (self._btn_kill, self._btn_fallback):
                btn.setStyleSheet(_BUTTON_STYLE)
                btn.setCursor(Qt.CursorShape.PointingHandCursor)
                btn.hide()
            self._btn_kill.clicked.connect(lambda: self.sig_decision.emit("kill"))
            self._btn_fallback.clicked.connect(lambda: self.sig_decision.emit("fallback"))

            # 卡片位置固定不动（用户 2026-10-19 要求），动的是背景里的田野：
            # 从 5 秒视频拆出的帧序列按 12fps 循环播放，云在走、稻子被风吹。
            # 定时器无条件启动：帧是按需解码的，此刻可能只有 1 帧。
            self._frame_timer = QTimer(self)
            self._frame_timer.setInterval(int(1000 / FRAME_FPS))
            self._frame_timer.timeout.connect(self._advance_frame)
            self._frame_timer.start()

            # 文案按随机间隔轮播（1.2~2.8 秒），和进度条脱钩。挂在进度上就成了
            # 「每 25% 换一句」，一眼看出是排好的。
            self._phrase_timer = QTimer(self)
            self._phrase_timer.setSingleShot(True)
            self._phrase_timer.timeout.connect(self._rotate_phrase)
            self._schedule_phrase()
            self.move(self._base_x, self._base_y)

        def _schedule_phrase(self) -> None:
            self._phrase_timer.start(random.randint(1200, 2800))

        def _rotate_phrase(self) -> None:
            if not self._auto_phrase or not self._phrases:
                return
            self._phrase_index = (self._phrase_index + 1) % len(self._phrases)
            self._status = self._phrases[self._phrase_index]
            self.update()
            self._schedule_phrase()

        def _apply_conflict(self, port: int, process_name: str, pid: int) -> None:
            """把卡片切成「请选择」形态：显示两个按钮，并允许点击。"""
            self._conflict = {"port": port, "name": process_name, "pid": pid}
            self._status = "等你选一下"
            self._auto_phrase = False
            self._note = f"{port} 被「{process_name}」占用，要关掉它还是换个端口？"
            self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, False)
            self._btn_kill.setText(f"关掉它，继续用 {port}")
            self._layout_buttons()
            self._btn_kill.show()
            self._btn_fallback.show()
            self.update()

        def _clear_conflict(self) -> None:
            self._conflict = None
            self._btn_kill.hide()
            self._btn_fallback.hide()
            # 恢复穿透：卡片不该挡住用户点别的东西。
            self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
            self.update()

        def _layout_buttons(self) -> None:
            w, h = self.width(), self.height()
            gap = 14
            btn_w = (w - 44 - gap) // 2
            btn_h = 40
            y = h - 58
            self._btn_kill.setGeometry(22, y, btn_w, btn_h)
            self._btn_fallback.setGeometry(22 + btn_w + gap, y, btn_w, btn_h)

        def _begin_load_frames(self, directory: Path) -> None:
            """只登记帧路径，解码交给按需进行。

            61 帧 ``QImage`` 全解码会常驻 **93MB**（800x500 未压缩位图），而启动
            画面只活几秒 —— 为它占掉上百兆内存不划算。改成按需解码 + 小缓存：
            常驻约 18MB，代价是每帧解码一次（约 15ms，12fps 下预算 83ms，够用）。

            注意：61 帧循环远大于 ``FRAME_CACHE``，所以播放一圈几乎每帧都是
            miss —— 缓存只在「同一帧被重复绘制」（窗口激活重绘等）时才省事，
            不要指望它把整轮播放都缓存住。
            """
            self._frame_paths = sorted(directory.glob("*.jpg")) if directory.is_dir() else []
            if not self._frame_paths:
                return
            first = self._frame_at(0)
            if first is not None:
                self._bg = first
            logger.info("启动卡片背景动画共 %d 帧", len(self._frame_paths))

        def _frame_at(self, index: int) -> QImage | None:
            cached = self._frame_cache.get(index)
            if cached is not None:
                return cached
            if not (0 <= index < len(self._frame_paths)):
                return None
            image = QImage(str(self._frame_paths[index]))
            if image.isNull():
                return None
            self._frame_cache[index] = image
            while len(self._frame_cache) > FRAME_CACHE:
                self._frame_cache.pop(next(iter(self._frame_cache)))
            return image

        def _advance_frame(self) -> None:
            # 顺便把进度往目标值推一格（指数逼近，约半秒到位，避免跳变）
            if self._progress < self._target_progress:
                self._progress = min(
                    self._target_progress,
                    self._progress + (self._target_progress - self._progress) * 0.15,
                )
                self.update()
            if len(self._frame_paths) < 2:
                return
            self._frame_index = (self._frame_index + 1) % len(self._frame_paths)
            self.update()

        # ------------------------------------------------------------- 绘制

        def paintEvent(self, event: object) -> None:  # noqa: ARG002
            painter = QPainter(self)
            try:
                painter.setRenderHint(QPainter.RenderHint.Antialiasing)
                w, h = self.width(), self.height()

                clip = QPainterPath()
                clip.addRoundedRect(QRectF(0, 0, w, h), CARD_RADIUS, CARD_RADIUS)
                painter.setClipPath(clip)

                # 封面图：object-fit: cover
                # 背景动画：按需解码 + LRU 缓存；没有帧就退回静态封面。
                frame = self._frame_at(self._frame_index) if self._frame_paths else self._bg
                painter.drawImage(self.rect(), frame or self._bg)

                # 底部压暗：只在下半部分做渐变，上面保持通透
                grad = QLinearGradient(0, h * 0.5, 0, h)
                grad.setColorAt(0.0, QColor(6, 20, 13, 0))
                grad.setColorAt(1.0, QColor(6, 20, 13, 155))
                painter.fillRect(self.rect(), grad)

                self._draw_content(painter, w, h)
            finally:
                painter.end()

        def _draw_content(self, painter: QPainter, w: int, h: int) -> None:
            painter.setFont(QFont("Microsoft YaHei", 27, QFont.Weight.DemiBold))
            # 整块内容往上提 22px：备注行原本贴着卡片下边缘，只剩 4px 留白。
            # 冲突态还要再上移 70px，把底部让给两个选择按钮。
            brand_y = h - (230 if self._conflict else 160)
            self._outlined(painter, QRectF(0, brand_y, w, 40), "界野电商平台", Qt.AlignmentFlag.AlignCenter)

            painter.setFont(QFont("Microsoft YaHei", 14))
            self._outlined(
                painter,
                QRectF(0, brand_y + 42, w, 22),
                self._status,
                Qt.AlignmentFlag.AlignCenter,
            )

            # 端口条：圆角胶囊 + 状态点
            painter.setFont(QFont("Consolas", 12))
            pill_text = f"{self._host}:{self._port}   ·   v{self._version}"
            metrics = painter.fontMetrics()
            text_w = metrics.horizontalAdvance(pill_text)
            pill_w = text_w + 58
            pill_h = 30
            px = (w - pill_w) / 2
            py = brand_y + 76

            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(8, 22, 14, 158))
            painter.drawRoundedRect(QRectF(px, py, pill_w, pill_h), pill_h / 2, pill_h / 2)
            painter.setPen(QColor(255, 255, 255, 62))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(QRectF(px, py, pill_w, pill_h), pill_h / 2, pill_h / 2)

            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(151, 196, 89, 255))
            painter.drawEllipse(int(px + 15), int(py + pill_h / 2 - 3), 7, 7)

            painter.setPen(QColor(255, 255, 255, 232))
            painter.drawText(QRectF(px + 30, py, pill_w - 34, pill_h),
                             Qt.AlignmentFlag.AlignVCenter, pill_text)

            # 进度条：进度本身是「关键节点 + 平滑插值」，不是真实百分比。
            # 冲突态底部留给两个按钮，这里不画。
            if not self._conflict:
                bar_w, bar_h = 260, 3
                bx = (w - bar_w) / 2
                by = py + pill_h + 14
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(QColor(255, 255, 255, 46))
                painter.drawRoundedRect(QRectF(bx, by, bar_w, bar_h), bar_h / 2, bar_h / 2)
                painter.setBrush(QColor(151, 196, 89, 235))
                painter.drawRoundedRect(
                    QRectF(bx, by, max(bar_h, bar_w * self._progress), bar_h), bar_h / 2, bar_h / 2
                )

            if self._note:
                painter.setFont(QFont("Microsoft YaHei", 11))
                # 备注压在亮草地上，颜色要够亮（原来 215 透明度的暖黄在绿地上发暗）。
                painter.setPen(QColor(255, 242, 208, 245))
                painter.drawText(
                    QRectF(0, py + pill_h + 26, w, 22),
                    Qt.AlignmentFlag.AlignCenter,
                    self._note,
                )

        @staticmethod
        def _outlined(painter: QPainter, rect: QRectF, text: str, align: Qt.AlignmentFlag) -> None:
            """先描边后填白：压在亮草地上也能看清。"""
            painter.setPen(QColor(0, 0, 0, 140))
            for dx, dy in ((-1, 0), (1, 0), (0, -1), (0, 1)):
                shifted = QRectF(rect.x() + dx, rect.y() + dy, rect.width(), rect.height())
                painter.drawText(shifted, int(align), text)
            painter.setPen(QColor(255, 255, 255, 255))
            painter.drawText(rect, int(align), text)

        # ------------------------------------------------------------- 状态

        def _apply_port(self, port: int) -> None:
            self._port = port
            self.update()

        def _apply_status(self, status: str, note: str) -> None:
            self._status = status or self._status
            self._note = note
            # 有真实状态了（冲突 / 超时 / 顺延说明），别让随机文案再顶掉它。
            self._auto_phrase = False
            self.update()

        def _apply_progress(self, value: float) -> None:
            # 只推进进度本身：文案由 _rotate_phrase 按随机间隔轮播，两者脱钩。
            self._target_progress = max(0.0, min(1.0, float(value)))

        def _fade_and_close(self) -> None:
            if self._finished:
                return
            self._finished = True
            self._frame_timer.stop()
            self._phrase_timer.stop()
            self.setWindowOpacity(1.0)
            self._fade = QPropertyAnimation(self, b"windowOpacity", self)
            self._fade.setDuration(FADE_MS)
            self._fade.setStartValue(1.0)
            self._fade.setEndValue(0.0)
            self._fade.setEasingCurve(QEasingCurve.Type.InQuad)
            self._fade.finished.connect(self.close)
            self._fade.start()


class SplashCard:
    """跨线程安全的启动卡片门面。

    ``start()`` 立即返回（卡片在自己的 Qt 线程里绘制/浮动），主线程继续起后端；
    探活通过后淡出关闭并回调 ``on_ready``（一般打开浏览器）。
    """

    def __init__(self, *, background: Path, version: str, host: str, port: int) -> None:
        if not _QT_OK:
            raise RuntimeError("PySide6 不可用，无法显示启动卡片")
        self._background = background
        self._version = version
        self._host = host
        self._port = port
        self._widget: "SplashCardWidget | None" = None
        self._thread: threading.Thread | None = None
        self._started = threading.Event()
        self._probe: Callable[[], bool] | None = None
        self._on_ready: Callable[[], None] | None = None
        self._closed = threading.Event()
        self._watchdog: threading.Thread | None = None
        self._decision = ""
        self._decision_event = threading.Event()
        self._progress = 0.08

    # ------------------------------------------------------------- 生命周期

    def start(self, *, probe: Callable[[], bool] | None = None,
              on_ready: Callable[[], None] | None = None, timeout: float = 90.0) -> bool:
        self._probe = probe
        self._on_ready = on_ready
        self._thread = threading.Thread(
            target=self._run_qt, args=(timeout,), name="splash-qt", daemon=True
        )
        self._thread.start()
        # Qt 首次初始化（加载 Qt DLL、建事件循环）在慢机器上可能超过 3 秒；
        # 超时就静默回落成「等 2.5 秒开浏览器」，白屏空窗又回来了。给足 6 秒。
        if not self._started.wait(6.0):
            logger.warning("启动卡片在 6 秒内没有起来，回落为延迟开浏览器")
            self._closed.set()
            return False
        return self._widget is not None

    def _run_qt(self, timeout: float) -> None:
        try:
            app = QApplication.instance() or QApplication([])
            widget = SplashCardWidget(
                background=self._background,
                version=self._version,
                host=self._host,
                port=self._port,
            )
            # 主线程可能已经等超时、回落成「延迟开浏览器」了。此时若照样 show()，
            # 窗口会在半分钟后才亮出来，而 _watch 早已因为 _closed 退出 ——
            # 没人会关它，卡片就一直压在屏幕上（2026-10-20 复查发现）。
            if self._closed.is_set():
                widget.deleteLater()
                return
            self._widget = widget
            widget.show()
            self._started.set()
            self._watchdog = threading.Thread(
                target=self._watch, args=(timeout,), name="splash-watch", daemon=True
            )
            self._watchdog.start()
            app.exec()
        except Exception:  # noqa: BLE001 - 启动画面绝不能拖垮启动
            logger.warning("启动卡片渲染线程异常退出", exc_info=True)
            self._started.set()
        finally:
            self._closed.set()

    def _watch(self, timeout: float) -> None:
        """按节奏探活：就绪就淡出关卡片，超时则把状态换成「启动超时」。"""
        started = time.monotonic()
        last_probe = 0.0
        timed_out = False
        while not self._closed.is_set():
            now = time.monotonic()
            if self._probe is not None and now - last_probe >= PROBE_INTERVAL:
                last_probe = now
                try:
                    if self._probe():
                        self._finish()
                        return
                    # 还没就绪：缓慢爬向 95%（探活每 0.6 秒一次 → 每 3 秒 +10%），
                    # 避免进度条长时间钉在一个数字上像卡死。
                    if self._progress < 0.95:
                        self.set_progress(min(0.95, self._progress + 0.02))
                except Exception:  # noqa: BLE001
                    pass
            if not timed_out and now - started > timeout:
                timed_out = True
                self.set_status("启动超时，请查看日志", "")
                logger.warning("启动卡片等待超过 %s 秒仍未就绪", int(timeout))
            time.sleep(0.1)

    def _finish(self) -> None:
        logger.info("后端已就绪，收起启动卡片并打开工作台")
        self.set_progress(1.0)
        widget = self._widget
        if widget is not None:
            try:
                widget.sig_finished.emit()
            except Exception:  # noqa: BLE001
                pass
        if self._on_ready is not None:
            try:
                self._on_ready()
            except Exception:  # noqa: BLE001
                logger.warning("打开工作台失败", exc_info=True)

    # ------------------------------------------------------------- 跨线程接口

    def set_port(self, port: int) -> None:
        self._port = port
        widget = self._widget
        if widget is not None:
            try:
                widget.sig_set_port.emit(port)
            except Exception:  # noqa: BLE001
                pass

    def watch(self, probe: Callable[[], bool], on_ready: Callable[[], None]) -> None:
        """补挂探活：卡片必须比 import 后端更早出现，所以探活可以晚一点接上。"""
        self._probe = probe
        self._on_ready = on_ready

    def ask_port_conflict(
        self,
        *,
        port: int,
        process_name: str,
        pid: int,
        timeout: float = CONFLICT_TIMEOUT_SECONDS,
    ) -> str:
        """在卡片上问「关掉它 / 换个端口」，阻塞到用户选择或超时。

        返回 ``"kill"`` 或 ``"fallback"``；超时按 ``"fallback"`` 处理 —— 用户
        可能开了卡片就走开了，**顺延端口总比杀掉别人的进程安全**。
        """
        self._decision = ""
        self._decision_event.clear()
        widget = self._widget
        if widget is None:
            return "fallback"
        try:
            widget.sig_decision.connect(self._record_decision)
            widget.sig_conflict.emit(port, process_name, pid)
        except Exception:  # noqa: BLE001
            logger.warning("无法显示端口冲突选择，改为直接顺延端口", exc_info=True)
            return "fallback"
        logger.info("等待用户在启动卡片上决定端口冲突处理方式（%s 被 %s 占用）", port, process_name)
        try:
            self._decision_event.wait(timeout)
        finally:
            try:
                widget.sig_clear_conflict.emit()
                widget.sig_decision.disconnect(self._record_decision)
            except Exception:  # noqa: BLE001
                pass
        choice = self._decision or "fallback"
        logger.info("用户选择：%s", choice)
        return choice

    def _record_decision(self, choice: str) -> None:
        if choice not in ("kill", "fallback"):
            return
        self._decision = choice
        self._decision_event.set()

    def set_status(self, status: str, note: str = "") -> None:
        widget = self._widget
        if widget is not None:
            try:
                widget.sig_set_status.emit(status, note)
            except Exception:  # noqa: BLE001
                pass

    def set_progress(self, value: float) -> None:
        """推进进度（0.0~1.0）。只在关键节点调用；中间的平滑由卡片自己做。"""
        self._progress = max(0.0, min(1.0, float(value)))
        widget = self._widget
        if widget is not None:
            try:
                widget.sig_set_progress.emit(self._progress)
            except Exception:  # noqa: BLE001
                pass

    def close(self) -> None:
        """立刻收起（启动失败等场景）。"""
        self._closed.set()
        widget = self._widget
        if widget is not None:
            try:
                widget.sig_finished.emit()
            except Exception:  # noqa: BLE001
                pass
