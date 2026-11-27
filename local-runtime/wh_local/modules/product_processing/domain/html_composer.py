"""HTML/CSS 版式渲染：用常驻无头浏览器把 HTML 截成图片（默认 JPEG）。

为什么要它：PIL 没有布局引擎——字号、换行、卡片层次、渐变、描边全靠手算坐标，
版式上限很低。这里把「排版」交给浏览器，只保留「数据绑定」在 Python 侧。

设计要点：
- **单浏览器常驻**：Playwright 的 sync API 不能跨线程，所以起一个专属渲染线程，
  线程内 launch 一次浏览器，之后通过队列复用同一个 page（每张约 0.8s）。
- **失败必须降级**：任何异常（浏览器缺失/启动失败/超时）都返回空 bytes，
  调用方回退到 PIL 合成，绝不因为排版引擎问题阻断出图。
- **零外部请求**：图片全部以 data URL 内嵌，字体只用系统字体，离线可渲染。

可用 ``WH_MARKETING_HTML_LAYOUT=0`` 关闭 HTML 排版（强制走 PIL）。
``WH_HTML_RENDER_BROWSER`` 可指定浏览器 channel（默认优先安装包内置的 headless shell，
其次 msedge → chrome）。
"""

from __future__ import annotations

import base64
import html as html_module
import os
import queue
import threading
from typing import Any, Iterable, Sequence

DEFAULT_SIZE = 1600
SHOT_TIMEOUT_SECONDS = float(os.environ.get("WH_HTML_RENDER_TIMEOUT", "30"))
ACCENT = "#ff6a00"
ACCENT_LIGHT = "#ff8a3d"
# 与 PIL 合成（JPEG q90）保持同一格式：下游按 image/jpeg + .jpg 校验和落盘。
JPEG_QUALITY = int(os.environ.get("WH_HTML_RENDER_QUALITY", "92"))

# 字体组：只用系统字体（离线可渲染），都不张扬。
FONT_PAIRS: dict[str, tuple[str, str]] = {
    "industrial": (
        '"Arial Black","Arial Bold",Arial,sans-serif',
        '"Segoe UI",Arial,sans-serif',
    ),
    "editorial": (
        'Georgia,"Times New Roman",serif',
        '"Segoe UI",Arial,sans-serif',
    ),
    "friendly": (
        '"Trebuchet MS","Segoe UI",sans-serif',
        '"Segoe UI",Arial,sans-serif',
    ),
}

# 工具/汽配/园林/户外 → 工业黑体；家居/饰品/美妆 → 衬线；宠物/办公/数码 → 圆润无衬线。
_CATEGORY_FONT_PAIR: dict[str, str] = {
    "hand_tools": "industrial",
    "auto_accessories": "industrial",
    "garden_tools": "industrial",
    "sports_outdoor": "industrial",
    "home_kitchen": "editorial",
    "beauty_tools": "editorial",
    "apparel_accessories": "editorial",
}


def font_pair(category_id: str) -> tuple[str, str]:
    return FONT_PAIRS[_CATEGORY_FONT_PAIR.get(str(category_id or "").strip(), "friendly")]

_ENABLED = os.environ.get("WH_MARKETING_HTML_LAYOUT", "1").strip().lower() not in {
    "0", "false", "off", "no",
}


def html_layout_enabled() -> bool:
    return _ENABLED


# ── 浏览器渲染线程 ────────────────────────────────────────────────


# 在页面里做「字号二分搜索」：元素超过 data-max-height 就缩小，直到放得下。
# 换行/量高都由浏览器完成，Python 侧不需要估算字符宽度。
_AUTOFIT_JS = """() => {
  const fit = (el) => {
    const max = parseFloat(el.dataset.maxHeight || '0');
    if (!max) return;
    const min = parseFloat(el.dataset.minHeight || '20');
    const start = parseFloat(getComputedStyle(el).fontSize) || 100;
    let lo = min, hi = start;
    for (let i = 0; i < 14; i++) {
      const mid = (lo + hi) / 2;
      el.style.fontSize = mid + 'px';
      if (el.getBoundingClientRect().height <= max) lo = mid; else hi = mid;
    }
    el.style.fontSize = Math.floor(lo) + 'px';
  };
  document.querySelectorAll('[data-max-height]').forEach(fit);
}"""


class _RenderWorker(threading.Thread):
    """独占一个 Playwright sync 实例的渲染线程。"""

    def __init__(self) -> None:
        super().__init__(name="marketing-html-renderer", daemon=True)
        self._jobs: queue.Queue = queue.Queue()
        self._ready = threading.Event()
        self._failed: Exception | None = None

    # -- 对外 --
    def submit(
        self,
        document: str,
        width: int,
        height: int,
        *,
        image_type: str = "jpeg",
        quality: int = 92,
        scale: int = 1,
    ) -> bytes:
        if not self.is_alive():
            self.start()
        self._ready.wait(timeout=15)
        if self._failed is not None:
            raise RuntimeError(f"html renderer unavailable: {self._failed}")
        result: dict = {"event": threading.Event(), "png": b"", "error": None}
        self._jobs.put((document, width, height, image_type, quality, max(1, int(scale)), result))
        if not result["event"].wait(timeout=SHOT_TIMEOUT_SECONDS):
            raise TimeoutError("html render timed out")
        if result["error"] is not None:
            raise result["error"]
        content = result["png"]
        if not content:
            raise RuntimeError("html render produced no image")
        return content

    def shutdown(self) -> None:
        self._jobs.put(None)

    # -- 线程体 --
    def run(self) -> None:  # pragma: no cover - 需要真实浏览器
        bundled = _bundled_browsers_path()
        if bundled:
            os.environ["PLAYWRIGHT_BROWSERS_PATH"] = bundled
        try:
            from playwright.sync_api import sync_playwright
        except Exception as exc:  # noqa: BLE001
            self._failed = exc
            self._ready.set()
            return
        try:
            with sync_playwright() as playwright:
                browser = None
                last_error: Exception | None = None
                for channel in _browser_channels():
                    try:
                        browser = playwright.chromium.launch(
                            channel=channel or None,
                            headless=True,
                            args=[
                                "--disable-gpu",
                                "--hide-scrollbars",
                                "--disable-extensions",
                            ],
                        )
                        break
                    except Exception as exc:  # noqa: BLE001
                        last_error = exc
                if browser is None:
                    raise RuntimeError(f"no usable browser ({last_error})")
                pages: dict[int, Any] = {}

                def _page_for(scale: int):
                    """按设备像素比复用页面：scale=2 时截图即 2× 超采样。"""

                    page = pages.get(scale)
                    if page is None:
                        page = browser.new_page(
                            viewport={"width": DEFAULT_SIZE, "height": DEFAULT_SIZE},
                            device_scale_factor=scale,
                        )
                        pages[scale] = page
                    return page

                self._ready.set()
                while True:
                    job = self._jobs.get()
                    if job is None:
                        break
                    document, width, height, image_type, quality, scale, result = job
                    try:
                        page = _page_for(scale)
                        page.set_viewport_size({"width": width, "height": height})
                        page.set_content(document, wait_until="load")
                        # 文案自适应字号：等价于 PIL 的 _fit_lines，保证长标题不压到卡片上。
                        page.evaluate(_AUTOFIT_JS)
                        options: dict = {"type": image_type}
                        if image_type == "jpeg":
                            options["quality"] = max(1, min(100, int(quality)))
                        result["png"] = page.screenshot(**options)
                    except Exception as exc:  # noqa: BLE001
                        result["error"] = exc
                    finally:
                        result["event"].set()
                browser.close()
        except Exception as exc:  # noqa: BLE001
            self._failed = exc
            self._ready.set()
            # 让已在等待的任务立即失败，而不是各等 30 秒
            while True:
                try:
                    job = self._jobs.get_nowait()
                except queue.Empty:
                    break
                if job is None:
                    continue
                job[3]["error"] = exc
                job[3]["event"].set()


_WORKER: _RenderWorker | None = None
_WORKER_LOCK = threading.Lock()


def render_html(
    document: str,
    *,
    width: int = DEFAULT_SIZE,
    height: int = DEFAULT_SIZE,
    image_type: str = "jpeg",
    quality: int = JPEG_QUALITY,
    scale: int = 1,
) -> bytes:
    """把完整 HTML 截图成图片字节；失败抛异常（由调用方决定是否降级）。

    ``scale`` 是设备像素比：``scale=2`` 时输出像素是排版尺寸的 2×（超采样），
    由调用方决定何时降采样。默认输出 JPEG：全链路（GeneratedMedia 的
    content_type/suffix、预览图校验、上传压缩）都按 ``image/jpeg`` + ``.jpg``
    约定，输出 PNG 会被预览校验拒绝。
    """
    global _WORKER
    with _WORKER_LOCK:
        if _WORKER is None:
            _WORKER = _RenderWorker()
    return _WORKER.submit(
        document,
        width,
        height,
        image_type=image_type,
        quality=quality,
        scale=scale,
    )


def shutdown_renderer() -> None:
    """进程退出/测试清理：关闭浏览器线程。"""
    global _WORKER
    with _WORKER_LOCK:
        worker, _WORKER = _WORKER, None
    if worker is not None and worker.is_alive():
        worker.shutdown()


def _bundled_browsers_path() -> str:
    """安装包内置的浏览器目录（``<exe 同目录>/pw-browsers``）。

    打包版把 headless shell 一起塞进安装目录，避免依赖客户机是否装了 Edge；
    源码运行时同目录下的 ``pw-browsers`` 也认（便于本地验证打包链路）。
    已显式设置 ``PLAYWRIGHT_BROWSERS_PATH`` 时不覆盖。
    """
    if os.environ.get("PLAYWRIGHT_BROWSERS_PATH", "").strip():
        return ""
    import sys
    from pathlib import Path

    if getattr(sys, "frozen", False):
        base = Path(sys.executable).resolve().parent
        candidates = (base / "pw-browsers",)
    else:
        cwd = Path.cwd()
        candidates = (cwd / "pw-browsers", cwd.parent / "pw-browsers")
    for candidate in candidates:
        if candidate.is_dir():
            return str(candidate)
    return ""


def _browser_channels() -> list[str]:
    override = os.environ.get("WH_HTML_RENDER_BROWSER", "").strip()
    if override:
        return [override]
    # 内置了浏览器就优先用它（所有客户机渲染结果一致）；否则先用系统浏览器（零体积），
    # 最后才试 Playwright 默认位置。
    if os.environ.get("PLAYWRIGHT_BROWSERS_PATH", "").strip():
        return ["", "msedge", "chrome"]
    return ["msedge", "chrome", ""]


# ── feature_cards 模板 ──────────────────────────────────────────


def _data_url(content: bytes) -> str:
    if not content:
        return ""
    return "data:image/jpeg;base64," + base64.b64encode(content).decode()


def _escape(text: str) -> str:
    return html_module.escape(str(text or ""), quote=True)


def _headline_html(headline: str) -> str:
    """标题按显式换行拆行，最后一行用主色强调。"""
    lines = [line.strip() for line in str(headline or "").split("\n") if line.strip()]
    if not lines:
        lines = ["Premium Quality"]
    if len(lines) == 1:
        return _escape(lines[0])
    return "<br>".join(
        [*(_escape(line) for line in lines[:-1]), f"<span>{_escape(lines[-1])}</span>"]
    )


_TEMPLATE = """<!doctype html>
<html><head><meta charset="utf-8">
<style>
  * { margin:0; padding:0; box-sizing:border-box; }
  html, body { width:@@SIZE@@px; height:@@SIZE@@px; overflow:hidden; background:#07090c; }
  .stage { position:relative; width:@@SIZE@@px; height:@@SIZE@@px; overflow:hidden;
           font-family:@@HEAD_FONT@@; }
  .hero { position:absolute; inset:0; width:100%; height:100%; object-fit:cover; }
  /* 分区版式：主图模糊提亮做整页底（照片背景自然延伸，无方块接缝），
     清晰照片为圆角卡片，细节卡落在下方浅色留白里。 */
  .stage.light { background:#f5f2ec; }
  .bg { position:absolute; inset:-14%; width:128%; height:128%; object-fit:cover;
        filter:blur(56px) saturate(1.12) brightness(1.04); }
  .veil { position:absolute; inset:0;
          background:linear-gradient(165deg, rgba(255,255,255,.80), rgba(255,255,255,.68)); }
  .hero.card { top:22%; left:29%; width:42%; height:42%; border-radius:40px;
               box-shadow:0 38px 90px rgba(38,30,20,.22); }
  .stage.dark h1 { color:#ffffff; text-shadow:0 6px 24px rgba(0,0,0,.35); }
  .stage.dark .sub { color:rgba(255,255,255,.82); }
  .scrim { position:absolute; inset:0;
           background:
             radial-gradient(1200px 620px at 18% 8%, rgba(255,106,0,.14), transparent 62%),
             linear-gradient(180deg, rgba(8,10,14,.62) 0%, rgba(8,10,14,.12) 34%,
                             rgba(8,10,14,.30) 62%, rgba(8,10,14,.70) 100%); }
  .top { position:absolute; top:72px; left:76px; right:76px; }
  .kicker { font-size:22px; letter-spacing:9px; color:@@ACCENT_LIGHT@@;
            font-family:@@BODY_FONT@@; font-weight:700;
            text-transform:uppercase; margin-bottom:18px; }
  h1 { font-size:118px; line-height:.92; letter-spacing:-2.5px; color:#fff;
       text-shadow:0 8px 30px rgba(0,0,0,.65); text-transform:uppercase; }
  h1 span { color:@@ACCENT@@; }
  .stage.light h1 { color:#1c1e22; text-shadow:none; }
  .stage.light .sub { color:rgba(28,30,34,.66); text-shadow:none; }
  .stage.light .kicker { color:#b4470a; }
  .sub { margin-top:22px; font-size:30px; font-weight:400; line-height:1.35;
         color:rgba(255,255,255,.80); font-family:@@BODY_FONT@@;
         max-width:900px; }
  .accent { width:132px; height:7px; margin-top:28px; border-radius:99px;
            background:linear-gradient(90deg,@@ACCENT_LIGHT@@,@@ACCENT@@); }
  .cards { position:absolute; left:64px; right:64px; top:66%; bottom:44px;
           display:grid; grid-template-columns:repeat(@@COLUMNS@@,1fr);
           grid-template-rows:repeat(@@ROWS@@,1fr); gap:22px; }
  .card { display:flex; align-items:stretch; overflow:hidden; padding:0;
          border-radius:24px; border:1px solid rgba(28,30,34,.06);
          background:#ffffff;
          box-shadow:0 14px 32px rgba(66,54,38,.10); }
  .thumb { width:44%; flex:0 0 44%; object-fit:cover; display:block; }
  .card-copy { flex:1; display:flex; flex-direction:column; justify-content:center;
               padding:0 30px; }
  .card-copy h3 { font-size:34px; line-height:1.08; color:#1c1e22; letter-spacing:.2px;
                  text-transform:uppercase; }
  .rule { margin-top:14px; width:64px; height:5px; border-radius:99px;
          background:linear-gradient(90deg,@@ACCENT@@,rgba(255,106,0,.15)); }
</style></head>
<body><div class="stage @@STAGE_CLASS@@"@@STAGE_STYLE@@>
  <img class="bg" src="@@HERO@@" alt=""><div class="veil"></div>
  <img class="hero card" src="@@HERO@@" alt="">
  <div class="top">
    @@KICKER@@
    <h1 data-max-height="200">@@HEADLINE@@</h1>
    @@SUB@@
    <div class="accent"></div>
  </div>
  <div class="cards">@@CARDS@@</div>
</div></body></html>"""

_CARD_TEMPLATE = """
    <div class="card">
      <img class="thumb" src="@@IMAGE@@" alt="">
      <div class="card-copy"><h3>@@LABEL@@</h3><p class="rule"></p></div>
    </div>"""


def build_feature_cards_html(
    *,
    hero: bytes,
    cards: Sequence[tuple[bytes, str]],
    headline: str,
    subline: str = "",
    kicker: str = "",
    size: int = DEFAULT_SIZE,
    category_id: str = "",
) -> str:
    """拼出 feature_cards 的完整 HTML（纯字符串，不启动浏览器，便于单测）。"""
    items = [(content, label) for content, label in cards if content][:4]
    if len(items) < 1:
        raise ValueError("feature_cards needs at least 1 detail image")
    columns = 2 if len(items) <= 4 else 1
    # 行数按真实张数收：2 张 = 1 行大卡、4 张 = 2×2，不给空格子留位。
    rows = max(1, -(-len(items) // columns))
    cards_html = "".join(
        _CARD_TEMPLATE.replace("@@IMAGE@@", _data_url(content)).replace(
            "@@LABEL@@", _escape(label)
        )
        for content, label in items
    )
    subline_html = f'<p class="sub" data-max-height="140">{_escape(subline)}</p>' if str(subline or "").strip() else ""
    kicker_html = f'<div class="kicker">{_escape(kicker)}</div>' if str(kicker or "").strip() else ""
    head_font, body_font = font_pair(category_id)
    page, _tone = _backdrop(hero)
    accent, accent_light = _palette(hero, page)
    return (
        _TEMPLATE.replace("@@SIZE@@", str(int(size)))
        .replace("@@COLUMNS@@", str(columns))
        .replace("@@ROWS@@", str(rows))
        .replace("@@STAGE_CLASS@@", "light")
        .replace("@@STAGE_STYLE@@", "")
        .replace("@@HEAD_FONT@@", head_font)
        .replace("@@BODY_FONT@@", body_font)
        .replace("@@HERO@@", _data_url(hero))
        .replace("@@HEADLINE@@", _headline_html(headline))
        .replace("@@SUB@@", subline_html)
        .replace("@@KICKER@@", kicker_html)
        .replace("@@CARDS@@", cards_html)
        .replace("@@ACCENT_LIGHT@@", accent_light)
        .replace("@@ACCENT@@", accent)
    )


def render_feature_cards(
    *,
    hero: bytes,
    cards: Sequence[tuple[bytes, str]],
    headline: str,
    subline: str = "",
    kicker: str = "",
    size: int = DEFAULT_SIZE,
    category_id: str = "",
) -> bytes:
    """渲染 feature_cards；浏览器不可用时抛异常，调用方回退 PIL。"""
    if not html_layout_enabled():
        raise RuntimeError("html layout disabled")
    document = build_feature_cards_html(
        hero=hero, cards=cards, headline=headline, subline=subline, kicker=kicker,
        size=size, category_id=category_id,
    )
    return render_html(document, width=size, height=size)


# ── 上图下信息带：icon_grid 专用 ─────────────────────────────────
#
# 旧版把照片缩成 58% 的圆角卡片浮在柔和底页中间，商品只剩画面两成多，四周
# 大片空背景——用户反馈「一个方框在中间，旁边全是空的太丑」。现在照片铺满
# 宽度、顶到画布上沿（只裁掉上方空墙），标题与短词卡放进底部白色信息带。

ICON_GRID_BAND = 0.20

_BAND_TEMPLATE = """<!doctype html>
<html><head><meta charset="utf-8">
<style>
  * { margin:0; padding:0; box-sizing:border-box; }
  html, body { width:@@SIZE@@px; height:@@SIZE@@px; overflow:hidden; background:#fff; }
  .stage { position:relative; width:@@SIZE@@px; height:@@SIZE@@px; overflow:hidden;
           background:#fff; font-family:@@HEAD_FONT@@; color:#1c1e22; }
  .photo { position:absolute; left:0; top:0; width:100%; height:@@PHOTO_H@@%;
           object-fit:fill; display:block; }
  .band { position:absolute; left:0; right:0; bottom:0; height:@@BAND_H@@%;
          background:#fff; padding:34px 64px; display:flex; flex-direction:column;
          justify-content:space-between; }
  .eyebrow { font-size:18px; letter-spacing:7px; font-weight:700; color:#b4470a;
             margin-bottom:6px; font-family:@@BODY_FONT@@; text-transform:uppercase; }
  h1 { font-family:@@HEAD_FONT@@; font-size:56px; line-height:.94; letter-spacing:-1.5px;
       color:#1c1e22; text-transform:uppercase; }
  h1 span { color:@@ACCENT@@; }
  .accent { width:112px; height:6px; border-radius:99px; margin-top:10px;
            background:linear-gradient(90deg,@@ACCENT_LIGHT@@,@@ACCENT@@); }
  .chips { display:grid; grid-template-columns:repeat(6,1fr); gap:12px; }
  .chip { display:flex; align-items:center; gap:10px; height:62px; padding:0 12px;
          border-radius:15px; background:#fbf9f5; border:1px solid #eae3d8; overflow:hidden; }
  .badge { width:38px; height:38px; flex:0 0 38px; border-radius:50%;
           display:flex; align-items:center; justify-content:center;
           background:linear-gradient(160deg,@@ACCENT_LIGHT@@,@@ACCENT@@);
           box-shadow:0 5px 12px rgba(66,54,38,.16); }
  .badge svg { width:22px; height:22px; }
  .chip b { font-size:17px; line-height:1.06; letter-spacing:.1px; color:#1c1e22;
            font-family:@@BODY_FONT@@; text-transform:uppercase; }
</style></head>
<body><div class="stage">
  <img class="photo" src="@@HERO@@" alt="">
  <div class="band">
    <div>
      <div class="eyebrow">Key Features</div>
      <h1 data-max-height="112" data-min-height="32">@@HEADLINE@@</h1>
      <div class="accent"></div>
    </div>
    <div class="chips">@@CHIPS@@</div>
  </div>
</div></body></html>"""

_BAND_CHIP = (
    '<div class="chip"><div class="badge">{icon}</div><b>{label}</b></div>'
)


def build_icon_grid_html(
    *,
    hero: bytes,
    headline: str,
    subline: str = "",
    bullets: Sequence[str] = (),
    size: int = DEFAULT_SIZE,
    category_id: str = "",
) -> str:
    """拼出 icon_grid 的「上图 + 底部信息带」HTML（纯字符串，不启动浏览器）。"""
    from .premium_composer import band_photo_bytes  # noqa: PLC0415

    if not hero:
        raise ValueError("icon_grid layout needs a hero image")
    if not str(headline or "").strip():
        raise ValueError("icon_grid layout needs a headline")
    labels = _bullets_for(bullets, subline, 6)
    chips = "".join(
        _BAND_CHIP.format(icon=_icon_svg(index), label=_escape(label))
        for index, label in enumerate(labels)
    )
    head_font, body_font = font_pair(category_id)
    page, _tone = _backdrop(hero)
    accent, accent_light = _palette(hero, page)
    band_pct = int(round(ICON_GRID_BAND * 100))
    return (
        _BAND_TEMPLATE.replace("@@SIZE@@", str(int(size)))
        .replace("@@PHOTO_H@@", str(100 - band_pct))
        .replace("@@BAND_H@@", str(band_pct))
        .replace("@@HEAD_FONT@@", head_font)
        .replace("@@BODY_FONT@@", body_font)
        .replace("@@HERO@@", _data_url(band_photo_bytes(hero, ICON_GRID_BAND, int(size))))
        .replace("@@HEADLINE@@", _headline_html(headline))
        .replace("@@CHIPS@@", chips)
        .replace("@@ACCENT_LIGHT@@", accent_light)
        .replace("@@ACCENT@@", accent)
    )



# ── 照片+文案家族：大字卖点 / 细节特写 / 材质条 / 场景应用 / 侧栏 ─────────────
#
# 这几个版式在 PIL 里是「同一张照片 + 不同位置的文字块」，差异只在：
# 标题对齐（居中/左上）、是否带副文案、底部放 chips / pills / 四角卡片。
# 所以共用一套模板，用 variant class 控制布局，避免五份 CSS 各写一遍。

# 六个圆形图标（白描边/白填充），对应 PIL 版 _icon() 的 6 种图形。
_ICONS = (
    '<path d="M14 25l7 8 13-16"/>',
    '<path d="M24 10l12 5v9c0 8-5 12-12 15-7-3-12-7-12-15v-9z"/>',
    '<path d="M24 10l3.6 9.4L37 23l-9.4 3.6L24 36l-3.6-9.4L11 23l9.4-3.6z" fill="#fff" stroke="none"/>',
    '<path d="M24 11c5 7 9 11 9 16a9 9 0 1 1-18 0c0-5 4-9 9-16z"/>',
    '<circle cx="24" cy="24" r="7"/><path d="M24 9v6M24 33v6M9 24h6M33 24h6"/>',
    '<rect x="12" y="19" width="24" height="10" rx="2"/><path d="M18 19v5M24 19v6M30 19v5"/>',
)


def _icon_svg(index: int) -> str:
    path = _ICONS[index % len(_ICONS)]
    return (
        '<svg viewBox="0 0 48 48" fill="none" stroke="#fff" stroke-width="3.2" '
        f'stroke-linecap="round" stroke-linejoin="round">{path}</svg>'
    )


_LAYOUT_TEMPLATE = """<!doctype html>
<html><head><meta charset="utf-8">
<style>
  * { margin:0; padding:0; box-sizing:border-box; }
  html, body { width:@@SIZE@@px; height:@@SIZE@@px; overflow:hidden; background:#07090c; }
  .stage { position:relative; width:@@SIZE@@px; height:@@SIZE@@px; overflow:hidden;
           font-family:@@HEAD_FONT@@; }
  .hero { position:absolute; inset:0; width:100%; height:100%; object-fit:cover; }
  /* 分区版式的底：主图自身的高斯模糊 + 提亮，作为「照片背景向整页延伸」。
     比取一个纯色更可靠——任何照片、任何光线都不会露出方块接缝。
     照片本体另以圆角卡片呈现（.hero.card），边界是刻意的设计，而不是糊掉的切口。 */
  .bg { position:absolute; inset:-14%; width:128%; height:128%; object-fit:cover;
        filter:blur(56px) saturate(1.12) brightness(1.04); }
  .veil { position:absolute; inset:0;
          background:linear-gradient(165deg, rgba(255,255,255,.80), rgba(255,255,255,.68)); }
  .hero.card { border-radius:40px; object-fit:cover;
               box-shadow:0 38px 90px rgba(38,30,20,.22); }
  .stage.dark h1 { color:#ffffff; text-shadow:0 6px 24px rgba(0,0,0,.35); }
  .stage.dark .sub { color:rgba(255,255,255,.82); }
  .scrim { position:absolute; inset:0;
           background:
             radial-gradient(1200px 620px at 18% 8%, rgba(255,106,0,.14), transparent 62%),
             linear-gradient(180deg, rgba(8,10,14,.62) 0%, rgba(8,10,14,.12) 34%,
                             rgba(8,10,14,.30) 62%, rgba(8,10,14,.70) 100%); }
  .top { position:absolute; top:74px; left:76px; right:76px; }
  .top.center { left:0; right:0; padding:0 96px; text-align:center; }
  h1 { font-size:@@H1@@px; line-height:.94; letter-spacing:-2px; color:#fff;
       text-shadow:0 8px 30px rgba(0,0,0,.65); text-transform:uppercase; }
  h1 span { color:@@ACCENT@@; }
  /* 分区版式是暖白底：标题深色、卡片改白色，避免一片黑色块压得又重又土。 */
  .stage.light h1 { color:#1c1e22; text-shadow:none; }
  .stage.light .sub { color:rgba(28,30,34,.66); text-shadow:none; }
  .stage.light .kicker { color:#b4470a; }
  .sub { margin-top:20px; font-size:@@SUB@@px; font-weight:600; line-height:1.3;
         color:rgba(255,255,255,.82); font-family:@@BODY_FONT@@;
         max-width:980px; }
  .top.center .sub { margin-left:auto; margin-right:auto; }
  .accent { width:132px; height:7px; margin-top:26px; border-radius:99px;
            background:linear-gradient(90deg,@@ACCENT_LIGHT@@,@@ACCENT@@); }
  .top.center .accent { margin-left:auto; margin-right:auto; }
  .chips { position:absolute; left:5%; right:5%; bottom:4.5%;
           display:grid; grid-template-columns:repeat(3,1fr); gap:14px; }
  .chip { display:flex; align-items:center; gap:15px; padding:16px 18px;
          border-radius:20px; border:1px solid rgba(28,30,34,.06);
          background:#ffffff;
          box-shadow:0 14px 30px rgba(66,54,38,.10); }
  .badge { width:46px; height:46px; flex:0 0 46px; border-radius:50%;
           background:linear-gradient(160deg,@@ACCENT_LIGHT@@,@@ACCENT@@);
           display:flex; align-items:center; justify-content:center;
           box-shadow:0 6px 14px rgba(38,30,20,.18); }
  .badge svg { width:26px; height:26px; }
  .chip b { font-size:22px; color:#1c1e22; text-transform:uppercase; letter-spacing:.2px;
            line-height:1.12; font-family:@@BODY_FONT@@; }
  .pills { position:absolute; left:6%; right:6%; bottom:4.5%; display:flex; gap:1.4%; }
  .pill { flex:1; height:112px; border-radius:999px; display:flex; align-items:center;
          justify-content:center; text-align:center; padding:0 18px;
          border:1px solid rgba(28,30,34,.06);
          background:#ffffff;
          box-shadow:0 14px 30px rgba(66,54,38,.10);
          font-size:25px; color:#1c1e22; text-transform:uppercase; font-family:@@BODY_FONT@@;
          font-weight:700; }
  .case { position:absolute; width:44%; height:112px; display:flex; align-items:center; gap:16px;
          padding:0 18px; border-radius:20px; border:1px solid rgba(28,30,34,.06);
          background:#ffffff;
          box-shadow:0 14px 30px rgba(66,54,38,.10); }
  .case .badge { width:46px; height:46px; flex:0 0 46px; }
  .case .badge svg { width:26px; height:26px; }
  .case b { font-size:23px; color:#1c1e22; text-transform:uppercase;
            font-family:@@BODY_FONT@@; line-height:1.1; }
  .c0 { left:5%; bottom:17.5%; } .c1 { left:51%; bottom:17.5%; }
  .c2 { left:5%; bottom:4.5%; } .c3 { left:51%; bottom:4.5%; }
  /* 侧栏版式：左侧竖向照片卡 + 右侧整块信息面板（标题就在面板里，不另起一栏）。 */
  .rail { position:absolute; left:61.5%; right:4.5%; top:5%; bottom:5%;
          border-radius:34px; background:#ffffff; border:1px solid rgba(28,30,34,.06);
          box-shadow:0 30px 70px rgba(38,30,20,.16); padding:44px 36px;
          display:flex; flex-direction:column; }
  .rail .kicker { font-size:19px; letter-spacing:6px; color:#b4470a;
                  font-family:@@BODY_FONT@@; font-weight:700; text-transform:uppercase;
                  margin-bottom:16px; }
  .rail h1 { font-size:64px; line-height:.98; letter-spacing:-1px; }
  .rail-sub { margin-top:18px; font-size:25px; line-height:1.36;
              color:rgba(28,30,34,.62); font-family:@@BODY_FONT@@; }
  .rail .accent { margin-top:22px; }
  .rail-cards { margin-top:36px; display:flex; flex-direction:column; gap:16px; }
  .rail-card { display:flex; align-items:center; gap:15px; padding:19px 16px;
               border-radius:18px; background:#f7f4ee; }
  .rail-card .badge { width:44px; height:44px; flex:0 0 44px; }
  .rail-card .badge svg { width:25px; height:25px; }
  .rail-card b { font-size:21px; color:#1c1e22; text-transform:uppercase; line-height:1.15;
                 font-family:@@BODY_FONT@@; }
  .rail-pills { margin-top:auto; display:flex; flex-direction:column; gap:13px; }
  .rail-pill { border-radius:999px; background:#1c1e22; color:#fff; text-align:center;
               padding:18px 8px; font-size:21px; font-weight:700;
               font-family:@@BODY_FONT@@; }
</style></head>
<body><div class="stage @@STAGE_CLASS@@"@@STAGE_STYLE@@>
  @@BG_BLOCK@@
  @@HERO_BLOCK@@
  @@TOP_BLOCK@@
  @@EXTRA@@
</div></body></html>"""


# 与 PIL 版字号/留白对齐：h1/base 分别为画布宽度的 7.4% / 6% / 5.8%。
_LAYOUT_SPECS: dict[str, dict] = {
    "headline_hero": {"align": "center", "h1": 118, "h1max": 300, "sub": 46, "submax": 130, "chips": 0},
    "action_proof": {"align": "", "h1": 96, "h1max": 250, "sub": 40, "submax": 120, "chips": 0},
    # 分区版式：整页 = 主图模糊提亮底（无接缝），照片是圆角卡片。
    # 照片卡一律正方形，与生图 1:1 同比例 —— cover 不再裁掉商品。
    "icon_grid": {
        "align": "center", "h1": 86, "h1max": 210, "sub": 0, "submax": 0, "chips": 6,
        "card": (0.20, 0.21, 0.58, 0.58),
    },
    "material_strip": {
        "align": "", "h1": 88, "h1max": 200, "sub": 38, "submax": 88, "chips": 4,
        "card": (0.225, 0.22, 0.56, 0.56),
    },
    "use_cases": {
        "align": "center", "h1": 88, "h1max": 200, "sub": 38, "submax": 100, "chips": 4,
        "card": (0.215, 0.24, 0.52, 0.52),
    },
    # 侧栏卖点：左侧竖向照片卡 + 右侧信息面板（标题在面板内，照片与面板同高）。
    "side_rail": {
        "align": "", "h1": 64, "h1max": 300, "sub": 34, "submax": 110, "chips": 6,
        "card": (0.05, 0.045, 0.55, 0.90), "rail": True,
    },
}

LAYOUT_TEMPLATES = tuple(_LAYOUT_SPECS)


def _bullets_for(bullets: Sequence[str], subline: str, count: int) -> list[str]:
    """短词不做「凑数」也不编造：文案给几个就画几个；没有就留空由版式降级。"""
    items = [str(item).strip() for item in (bullets or ()) if str(item or "").strip()][:count]
    if not items and str(subline or "").strip():
        items = [" ".join(str(subline).split()[:2])]
    return items


def _extra_html(
    template: str, bullets: Sequence[str], subline: str, headline_html: str = ""
) -> str:
    spec = _LAYOUT_SPECS[template]
    count = int(spec["chips"])
    if not count:
        return ""
    labels = _bullets_for(bullets, subline, count)
    if template == "side_rail":
        # 标题直接进面板：左图右栏，各自占满整高，不再留一块死白页脚。
        sub_html = (
            f'<p class="rail-sub">{_escape(subline)}</p>' if str(subline or "").strip() else ""
        )
        cards = "".join(
            f'<div class="rail-card"><div class="badge">{_icon_svg(index)}</div>'
            f"<b>{_escape(label)}</b></div>"
            for index, label in enumerate(labels[:3])
        )
        pills = "".join(
            f'<div class="rail-pill">{_escape(label)}</div>' for label in labels[3:6]
        )
        return (
            f'<div class="rail"><div class="kicker">Key Features</div>'
            f'<h1 data-max-height="{spec["h1max"]}" data-min-height="40">{headline_html}</h1>'
            f'<div class="accent"></div>'
            f'{sub_html}'
            f'<div class="rail-cards">{cards}</div>'
            f'<div class="rail-pills">{pills}</div></div>'
        )
    if template == "icon_grid":
        chips = "".join(
            f'<div class="chip"><div class="badge">{_icon_svg(index)}</div>'
            f"<b>{_escape(label)}</b></div>"
            for index, label in enumerate(labels)
        )
        return f'<div class="chips">{chips}</div>'
    if template == "material_strip":
        pills = "".join(f'<div class="pill">{_escape(label)}</div>' for label in labels)
        return f'<div class="pills">{pills}</div>'
    cases = "".join(
        f'<div class="case c{index}"><div class="badge">{_icon_svg(index + 2)}</div>'
        f"<b>{_escape(label)}</b></div>"
        for index, label in enumerate(labels[:4])
    )
    return cases


def _backdrop(hero: bytes) -> tuple[tuple[int, int, int], str]:
    """取照片四周边缘的中位色，作为照片背景向外延伸的整页底色。

    返回 ((r,g,b), "light"|"dark")；浅底用深色标题，深底用白字。
    """

    from io import BytesIO

    from PIL import Image

    with Image.open(BytesIO(hero)) as image:
        small = image.convert("RGB").resize((80, 80), Image.LANCZOS)
        pixels = small.load()
        samples = [pixels[x, y] for x in range(80) for y in (0, 1, 78, 79)]
        samples += [pixels[x, y] for y in range(80) for x in (0, 1, 78, 79)]
    r = sorted(item[0] for item in samples)[len(samples) // 2]
    g = sorted(item[1] for item in samples)[len(samples) // 2]
    b = sorted(item[2] for item in samples)[len(samples) // 2]
    tone = "light" if (0.299 * r + 0.587 * g + 0.114 * b) > 150 else "dark"
    return (r, g, b), tone


def _palette(hero: bytes, page: tuple[int, int, int]) -> tuple[str, str]:
    """从商品主图取一个高饱和主色做强调色，作为 (accent, accent_light)。

    强调色跟着商品走，同品类不同商品不会撞色；与页底对比不足时自动压暗/提亮。
    主图没有可用彩色（纯白/纯黑/灰）时回落到默认橙色。
    """

    import colorsys
    from io import BytesIO

    from PIL import Image

    with Image.open(BytesIO(hero)) as image:
        small = image.convert("RGB").resize((48, 48), Image.LANCZOS)
        pixels = [small.getpixel((x, y)) for y in range(48) for x in range(48)]
    best: tuple[float, float, float, float] | None = None
    for r, g, b in pixels:
        hue, sat, val = colorsys.rgb_to_hsv(r / 255, g / 255, b / 255)
        if val < 0.15 or val > 0.98 or sat < 0.18:
            continue
        score = sat * (1 - abs(val - 0.55))
        if best is None or score > best[0]:
            best = (score, hue, sat, val)
    if best is None:
        return ACCENT, ACCENT_LIGHT
    _, hue, sat, _ = best
    page_lum = (0.299 * page[0] + 0.587 * page[1] + 0.114 * page[2]) / 255
    value = 0.62 if page_lum > 0.55 else 0.95
    sat = max(0.62, min(0.92, sat))
    r, g, b = colorsys.hsv_to_rgb(hue, sat, value)
    accent = f"#{int(r * 255):02x}{int(g * 255):02x}{int(b * 255):02x}"
    r2, g2, b2 = colorsys.hsv_to_rgb(hue, max(0.35, sat - 0.2), min(1.0, value + 0.16))
    light = f"#{int(r2 * 255):02x}{int(g2 * 255):02x}{int(b2 * 255):02x}"
    return accent, light


def build_marketing_html(
    *,
    template: str,
    hero: bytes,
    headline: str,
    subline: str = "",
    bullets: Sequence[str] = (),
    size: int = DEFAULT_SIZE,
    category_id: str = "",
) -> str:
    """拼出「照片+文案」版式的完整 HTML（纯字符串，不启动浏览器）。"""
    key = str(template or "").strip()
    spec = _LAYOUT_SPECS.get(key)
    if spec is None:
        raise ValueError(f"unknown marketing template: {template}")
    if not hero:
        raise ValueError("marketing layout needs a hero image")
    if not str(headline or "").strip():
        raise ValueError("marketing layout needs a headline")
    # 图标网格走「上图 + 底部信息带」，不再套用浮空照片卡的老模板。
    if key == "icon_grid":
        return build_icon_grid_html(
            hero=hero,
            headline=headline,
            subline=subline,
            bullets=bullets,
            size=size,
            category_id=category_id,
        )
    subline_html = ""
    if spec["sub"] and str(subline or "").strip():
        subline_html = (
            f'<p class="sub" data-max-height="{spec["submax"]}">{_escape(subline)}</p>'
        )
    hero_url = _data_url(hero)
    head_font, body_font = font_pair(category_id)
    page, page_tone = _backdrop(hero)
    accent, accent_light = _palette(hero, page)
    headline_html = _headline_html(headline)
    card = spec.get("card")
    if card:
        top_ratio, left_ratio, width_ratio, height_ratio = (float(v) for v in card)
        # 整页 = 主图模糊提亮底；照片为圆角卡片。底色永远是浅色，标题一律深色。
        stage_class = "light"
        stage_style = ""
        bg_block = (
            f'<img class="bg" src="{hero_url}" alt=""><div class="veil"></div>'
        )
        hero_block = (
            f'<img class="hero card" style="top:{top_ratio * 100:.2f}%;'
            f'left:{left_ratio * 100:.2f}%;width:{width_ratio * 100:.2f}%;'
            f'height:{height_ratio * 100:.2f}%" src="{hero_url}" alt="">'
        )
        top_block = (
            ""
            if spec.get("rail")
            else (
                f'<div class="top {spec["align"]}">'
                f'<h1 data-max-height="{spec["h1max"]}" data-min-height="40">{headline_html}</h1>'
                f"{subline_html}"
                f'<div class="accent"></div></div>'
            )
        )
    else:
        stage_class = ""
        stage_style = ""
        bg_block = ""
        hero_block = f'<img class="hero" src="{hero_url}" alt=""><div class="scrim"></div>'
        top_block = (
            f'<div class="top {spec["align"]}">'
            f'<h1 data-max-height="{spec["h1max"]}" data-min-height="40">{headline_html}</h1>'
            f"{subline_html}"
            f'<div class="accent"></div></div>'
        )
    return (
        _LAYOUT_TEMPLATE.replace("@@SIZE@@", str(int(size)))
        .replace("@@STAGE_CLASS@@", stage_class)
        .replace("@@STAGE_STYLE@@", stage_style)
        .replace("@@BG_BLOCK@@", bg_block)
        .replace("@@HERO_BLOCK@@", hero_block)
        .replace("@@TOP_BLOCK@@", top_block)
        .replace("@@HEAD_FONT@@", head_font)
        .replace("@@BODY_FONT@@", body_font)
        .replace("@@ALIGN@@", spec["align"])
        .replace("@@H1@@", str(spec["h1"]))
        .replace("@@H1MAX@@", str(spec["h1max"]))
        .replace("@@SUB@@", str(spec["sub"]))
        .replace("@@HERO@@", hero_url)
        .replace("@@HEADLINE@@", headline_html)
        .replace("@@SUBHTML@@", subline_html)
        .replace("@@EXTRA@@", _extra_html(key, bullets, subline, headline_html))
        .replace("@@ACCENT_LIGHT@@", accent_light)
        .replace("@@ACCENT@@", accent)
    )


def render_marketing(
    *,
    template: str,
    hero: bytes,
    headline: str,
    subline: str = "",
    bullets: Sequence[str] = (),
    size: int = DEFAULT_SIZE,
    category_id: str = "",
) -> bytes:
    """渲染「照片+文案」版式；浏览器不可用时抛异常，调用方回退 PIL。"""
    if not html_layout_enabled():
        raise RuntimeError("html layout disabled")
    document = build_marketing_html(
        template=template, hero=hero, headline=headline, subline=subline,
        bullets=bullets, size=size, category_id=category_id,
    )
    return render_html(document, width=size, height=size)


# ── 多结构骨架：S1 上下分栏 / S2 左右分栏 / S3 满版叠加 / S4 贴边大卡 ──────────
#
# 内容与结构解耦：模板只决定「有哪些块」（短词 / 药丸 / 细节卡 / 圆细节 / 编号），
# 结构决定这些块放在哪、多大。字号按「大字号优先」定：标题 64-96，标签 34-38。
# 同一商品内的多个模板由 layout_variants 分配不同结构，避免一批图全部一个样。

_SKELETON_SPEC: dict[str, dict] = {
    "S1": {"h1": 84, "h1max": 160, "label": 36, "item_h": 104, "gap": 14, "max": 4},
    "S2": {"h1": 64, "h1max": 220, "label": 38, "item_h": 98, "gap": 13, "max": 6},
    "S3": {"h1": 96, "h1max": 190, "label": 34, "item_h": 104, "gap": 14, "max": 4},
    "S4": {"h1": 80, "h1max": 160, "label": 34, "item_h": 104, "gap": 12, "max": 4},
}

_SKELETON_CONTENT_STYLE: dict[str, str] = {
    "headline_hero": "none",
    "action_proof": "none",
    "icon_grid": "chips",
    "material_strip": "pills",
    "use_cases": "cards",
    "side_rail": "chips",
    "feature_cards": "cards",
    "feature_strip": "circles",
    "callout_ring": "callouts",
    "white_main": "none",
}


def _skeleton_items(
    template: str,
    bullets: Sequence[str],
    subline: str,
    details: Sequence[tuple[bytes, str]],
    callout_parts: Sequence[dict],
    max_items: int,
) -> tuple[str, list[dict[str, Any]]]:
    """把模板内容统一成 (样式, 条目列表)，供四种结构复用。"""

    style = _SKELETON_CONTENT_STYLE.get(str(template or ""), "none")
    if style == "none":
        # 纯标题版式（大字卖点 / 细节特写）：信息带只放标题与副文案。
        # 旧实现会在这里落进 else 分支生成 4 个图标卡，导致它和 side_rail
        # 排出来几乎一样，同一商品的多张图看着像重复图。
        return style, []
    if style in {"cards", "circles"} and details:
        items = [
            {"label": str(label or ""), "thumb": content}
            for content, label in list(details)[:max_items]
            if str(label or "").strip()
        ]
    elif style in {"cards", "circles"}:
        # 照片卡版式没有真实细节图就不摆卡：以前会拿「图标 + 短词」硬凑卡位，
        # 出来是四张没有照片的空壳卡（用户直接判垃圾）。宁可整版降级成满幅大图。
        return style, []
    elif style == "callouts":
        items = [
            {"label": str(part.get("label") or "").strip(), "number": index + 1}
            for index, part in enumerate(list(callout_parts)[:max_items])
            if str(part.get("label") or "").strip()
        ]
        if not items:
            labels = _bullets_for(bullets, subline, max_items)
            items = [{"label": label, "number": index + 1} for index, label in enumerate(labels)]
    else:
        labels = _bullets_for(bullets, subline, max_items)
        items = [{"label": label} for label in labels]
    return style, items


def _sk_item_html(
    style: str,
    item: dict[str, Any],
    index: int,
    *,
    label_size: int,
    item_h: int,
    glass: bool = False,
) -> str:
    bg = "rgba(255,255,255,.90)" if glass else "#fbf9f5"
    edge = "rgba(255,255,255,.55)" if glass else "#eae3d8"
    shadow = "0 16px 34px rgba(20,16,10,.16)" if glass else "none"
    label = _escape(str(item.get("label") or ""))
    common = (
        f'height:{item_h}px;border-radius:{int(item_h * 0.22)}px;background:{bg};'
        f'border:1px solid {edge};box-shadow:{shadow};overflow:hidden;'
    )
    if style == "pills":
        return (
            f'<div style="flex:1;{common}border-radius:999px;display:flex;align-items:center;'
            f'justify-content:center;padding:0 16px">'
            f'<b style="font-size:{label_size}px;text-transform:uppercase;line-height:1.05;'
            f'text-align:center">{label}</b></div>'
        )
    if style == "circles":
        thumb = _data_url(item.get("thumb") or b"")
        return (
            f'<div style="flex:1;{common}display:flex;align-items:center;gap:14px;padding:0 14px">'
            f'<img src="{thumb}" style="width:{int(item_h * 0.68)}px;height:{int(item_h * 0.68)}px;'
            f'border-radius:50%;object-fit:cover;border:4px solid #ff6a00">'
            f'<b style="font-size:{label_size}px;text-transform:uppercase;line-height:1.05">{label}</b></div>'
        )
    if style == "cards" and item.get("thumb"):
        thumb = _data_url(item["thumb"])
        thumb_size = int(item_h * 0.70)
        return (
            f'<div style="flex:1;{common}display:flex;align-items:center;gap:14px;padding:0 16px">'
            f'<img src="{thumb}" style="width:{thumb_size}px;height:{thumb_size}px;'
            f'border-radius:12px;object-fit:cover">'
            f'<b style="font-size:{label_size}px;text-transform:uppercase;line-height:1.05">{label}</b></div>'
        )
    if style == "callouts":
        number = int(item.get("number") or index + 1)
        dot = int(item_h * 0.36)
        return (
            f'<div style="flex:1;{common}display:flex;align-items:center;gap:12px;padding:0 14px">'
            f'<span style="width:{dot}px;height:{dot}px;flex:0 0 {dot}px;border-radius:50%;'
            f'background:#ff6a00;color:#fff;font-size:{int(dot * 0.52)}px;font-weight:700;'
            f'display:flex;align-items:center;justify-content:center">{number}</span>'
            f'<b style="font-size:{label_size}px;text-transform:uppercase;line-height:1.05">{label}</b></div>'
        )
    badge = int(item_h * 0.52)
    return (
        f'<div style="flex:1;{common}display:flex;align-items:center;gap:12px;padding:0 14px">'
        f'<div style="width:{badge}px;height:{badge}px;flex:0 0 {badge}px;border-radius:50%;'
        f'display:flex;align-items:center;justify-content:center;color:#fff;'
        f'background:linear-gradient(160deg,@@ACCENT_LIGHT@@,@@ACCENT@@)">'
        f'<span style="width:{int(badge * 0.56)}px;height:{int(badge * 0.56)}px;display:flex">'
        f'{_icon_svg(index)}</span></div>'
        f'<b style="font-size:{label_size}px;text-transform:uppercase;line-height:1.05">{label}</b></div>'
    )


def _sk_callout_markers(
    parts: Sequence[dict], rect: tuple[float, float, float, float]
) -> str:
    """把 0-1000 归一化坐标映射进照片可见区域，画编号小圆点。"""

    left, top, width, height = rect
    marks = []
    for index, part in enumerate(list(parts)[:4]):
        try:
            x = float(part.get("x"))
            y = float(part.get("y"))
        except (TypeError, ValueError):
            continue
        nx = (x / 1000 - left) / max(width, 0.01)
        ny = (y / 1000 - top) / max(height, 0.01)
        if not (0 <= nx <= 1 and 0 <= ny <= 1):
            continue
        marks.append(
            f'<span style="position:absolute;left:{nx * 100:.2f}%;top:{ny * 100:.2f}%;'
            f'transform:translate(-50%,-50%);width:52px;height:52px;border-radius:50%;'
            f'background:#ff6a00;color:#fff;font-size:26px;font-weight:700;'
            f'display:flex;align-items:center;justify-content:center;'
            f'box-shadow:0 10px 22px rgba(0,0,0,.30)">{index + 1}</span>'
        )
    return "".join(marks)


# ── 工业促销主题：暗色工坊 + 橙色强调 + 斜体描边标题 ─────────────────────
#
# 对照参考图（工具类 listing）：黑/深灰背景、橙色渐变标签、白色粗斜体大写标题、
# 圆形细节特写 + 引线、底部功能卡/编号清单。只改变「排版视觉」，照片仍是
# 本次生成的 hero（和它的裁切），不复用任何原图像素。

# 所有营销图统一走促销排版；强调色按「商品主图主色 → 品类兜底色」决定，
# 所以工具可以是橙/蓝/绿，箱包是酒红，美妆是玫瑰色，不再固定橙色。
PROMO_CATEGORY_ACCENTS: dict[str, str] = {
    "hand_tools": "#ff5a00",
    "auto_accessories": "#ff5a00",
    "garden_tools": "#4c9a52",
    "sports_outdoor": "#1f9bd1",
    "home_kitchen": "#c06a3a",
    "apparel_accessories": "#a8476b",
    "beauty_tools": "#c9788a",
    "passive_tech": "#22a6c4",
    "office_stationery": "#4a63c8",
    "pet_supplies": "#3d9a7a",
    "adult_games": "#8a5a9a",
}
PROMO_DEFAULT_ACCENT = "#d9822b"
def promo_theme(
    category_id: str, text: str = "", style_template: str = "auto"
) -> bool:
    """暗色促销排版只给没选亮色模板的品类用；共通模板优先于品类默认。"""
    from .marketing_gen import resolve_style_template, style_is_bright  # noqa: PLC0415

    return not style_is_bright(resolve_style_template(style_template, category_id))


def _hex_rgb(value: str) -> tuple[int, int, int]:
    text = str(value or "").strip().lstrip("#")
    if len(text) != 6:
        return (255, 90, 0)
    try:
        return (int(text[0:2], 16), int(text[2:4], 16), int(text[4:6], 16))
    except ValueError:
        return (255, 90, 0)


def _mix_hex(value: str, target: tuple[int, int, int], ratio: float) -> str:
    r, g, b = _hex_rgb(value)
    ratio = max(0.0, min(1.0, float(ratio)))
    return "#{:02x}{:02x}{:02x}".format(
        int(round(r + (target[0] - r) * ratio)),
        int(round(g + (target[1] - g) * ratio)),
        int(round(b + (target[2] - b) * ratio)),
    )


def _rgba(value: str, alpha: float) -> str:
    r, g, b = _hex_rgb(value)
    return f"rgba({r},{g},{b},{max(0.0, min(1.0, alpha)):.2f})"


def _promo_palette(category_id: str, hero: bytes) -> dict[str, str]:
    """强调色：优先商品自身的高饱和主色，中性商品回落到品类色。

    这样同一套工业促销排版，工具可以是橙/蓝/绿，箱包是酒红，美妆是玫瑰色，
    而不是所有分类都强制橙色。
    """

    base = PROMO_CATEGORY_ACCENTS.get(
        str(category_id or "").strip().lower(), PROMO_DEFAULT_ACCENT
    )
    try:
        derived, _light = _palette(hero, (10, 10, 12))
    except Exception:  # noqa: BLE001 - 取色失败就用品类色
        derived = ""
    # _palette 找不到彩色时会回落到全局橙色；此时用品类色更合理。
    accent = base if (not derived or derived == ACCENT) else derived
    return {
        "accent": accent,
        "dark": _mix_hex(accent, (0, 0, 0), 0.42),
        "light": _mix_hex(accent, (255, 255, 255), 0.38),
        "soft": _rgba(accent, 0.22),
        "border": _rgba(accent, 0.92),
        "line": _rgba(accent, 0.30),
        "tint": _rgba(accent, 0.18),
        "glow": _rgba(accent, 0.16),
    }


def _promo_headline(headline: str) -> str:
    """大写标题：数字/尺寸词自动变橙，其余保持白色（描边在标题样式里）。

    文案里的 ``\\n`` 是模型给的断行建议，原样保留为 ``<br>``——参考图的标题
    基本都是两行（第一行白、第二行带橙色数字/尺寸）。
    """

    lines: list[str] = []
    for line in str(headline or "").split("\n"):
        words: list[str] = []
        for token in _escape(line).split():
            if any(char.isdigit() for char in token):
                words.append(f'<span style="color:var(--acc)">{token}</span>')
            else:
                words.append(token)
        if words:
            lines.append(" ".join(words))
    return "<br>".join(lines)


def _promo_title_block(
    headline: str,
    subline: str,
    body_font: str,
    *,
    top: int,
    left: int,
    width: str,
    h1: int,
    h1max: int,
    sub_size: int = 28,
    kicker: str = "PREMIUM DETAILS",
) -> str:
    sub_html = (
        f'<p style="margin-top:14px;font-family:{body_font};font-size:{sub_size}px;'
        f'font-style:italic;line-height:1.35;color:#e9e3d7;'
        f'text-shadow:0 3px 14px rgba(0,0,0,.7)">{_escape(subline)}</p>'
        if str(subline or "").strip()
        else ""
    )
    return (
        f'<div style="position:absolute;left:{left}px;top:{top}px;width:{width};z-index:5">'
        f'<div style="display:flex;align-items:center;gap:14px;margin-bottom:14px">'
        f'<span style="display:inline-block;width:64px;height:9px;background:var(--acc);'
        f'transform:skewX(-26deg)"></span>'
        f'<span style="font-family:{body_font};font-size:20px;letter-spacing:6px;'
        f'color:var(--acc-light);text-transform:uppercase">{_escape(kicker)}</span></div>'
        f'<h1 data-max-height="{h1max}" data-min-height="40" style="font-size:{h1}px;'
        f'line-height:.9;letter-spacing:-2px;text-transform:uppercase;font-style:italic;'
        f'color:#fff;-webkit-text-stroke:2px rgba(0,0,0,.9);paint-order:stroke fill;'
        f'text-shadow:0 7px 0 rgba(0,0,0,.35),0 16px 34px rgba(0,0,0,.55)">'
        f"{_promo_headline(headline)}</h1>{sub_html}</div>"
    )


def _promo_badge(index: int, size: int = 48) -> str:
    clip = "clip-path:polygon(0 0,100% 0,100% 72%,72% 100%,0 100%);"
    return (
        f'<span style="width:{size}px;height:{size}px;flex:none;display:flex;'
        f'align-items:center;justify-content:center;{clip}'
        f'background:linear-gradient(145deg,var(--acc-light),var(--acc2));color:#fff">'
        f'<span style="width:60%;height:60%;display:flex">{_icon_svg(index)}</span></span>'
    )


def _promo_thumb(item: dict[str, Any]) -> str:
    raw = item.get("thumb") or b""
    if isinstance(raw, str):
        return raw
    return _data_url(raw)


def _promo_card(item: dict[str, Any], index: int, *, height: int = 360) -> str:
    label = _escape(str(item.get("label") or ""))
    thumb = _promo_thumb(item)
    if thumb:
        media_inner = (
            f'<img src="{thumb}" style="width:100%;height:100%;object-fit:cover;'
            f'display:block">'
        )
    else:
        media_inner = (
            f'<div style="width:100%;height:100%;display:flex;align-items:center;'
            f'justify-content:center;background:radial-gradient(62% 62% at 50% 45%,'
            f'rgba(0,0,0,.55),var(--acc-soft))">'
            f'<span style="width:{int(height * 0.24)}px;height:{int(height * 0.24)}px;'
            f'display:flex">{_icon_svg(index)}</span></div>'
        )
    # 编号标注（callouts）带 number：徽章画编号圆而不是图标，否则 1./2./3. 丢失。
    number = item.get("number")
    if number:
        badge = (
            f'<span style="width:42px;height:42px;flex:none;border-radius:50%;'
            f'display:flex;align-items:center;justify-content:center;'
            f'background:var(--acc);color:#fff;font-size:22px;font-weight:700">'
            f'{int(number)}</span>'
        )
    else:
        badge = _promo_badge(index, 42)
    return (
        f'<div style="flex:1;min-width:0;display:flex;flex-direction:column;'
        f'background:linear-gradient(180deg,#1a1b1f,#0a0b0d);'
        f'border:2px solid var(--acc-border);overflow:hidden;'
        f'clip-path:polygon(0 0,100% 0,100% calc(100% - 22px),'
        f'calc(100% - 22px) 100%,0 100%)">'
        f'<div style="flex:1;min-height:0;overflow:hidden">{media_inner}</div>'
        f'<div style="display:flex;align-items:center;gap:10px;padding:12px 14px;'
        f'background:linear-gradient(90deg,var(--acc-tint),transparent)">'
        f'{badge}'
        f'<b style="font-size:24px;line-height:1.02;text-transform:uppercase;'
        f'letter-spacing:.2px;color:var(--acc-light)">{label}</b></div></div>'
    )


def _promo_row(item: dict[str, Any], index: int) -> str:
    label = _escape(str(item.get("label") or ""))
    thumb = _promo_thumb(item)
    number = item.get("number")
    if thumb:
        lead = (
            f'<img src="{thumb}" style="width:88px;height:88px;flex:none;object-fit:cover;'
            f'border:2px solid var(--acc);border-radius:8px">'
        )
    elif number:
        lead = (
            f'<span style="width:64px;height:64px;flex:none;border-radius:50%;'
            f'display:flex;align-items:center;justify-content:center;'
            f'background:var(--acc);color:#fff;font-size:30px;font-weight:700">'
            f'{int(number)}</span>'
        )
    else:
        lead = _promo_badge(index, 64)
    return (
        f'<div style="display:flex;align-items:center;gap:16px;padding:16px 0;'
        f'border-bottom:1px solid var(--acc-line)">{lead}'
        f'<b style="font-size:27px;line-height:1.05;text-transform:uppercase;'
        f'color:#fff;letter-spacing:.2px">{label}</b></div>'
    )


def _promo_callout(
    item: dict[str, Any],
    index: int,
    thumb_url: str,
    *,
    top: int,
    side: str,
) -> str:
    label = _escape(str(item.get("label") or ""))
    reverse = side == "right"
    edge = "right:3%" if reverse else "left:3%"
    if thumb_url:
        lead = (
            f'<img src="{thumb_url}" style="width:154px;height:154px;flex:none;'
            f'border-radius:50%;object-fit:cover;border:6px solid var(--acc);'
            f'box-shadow:0 12px 30px rgba(0,0,0,.55)">'
        )
    else:
        lead = _promo_badge(index, 132)
    clip = (
        "polygon(0 0,92% 0,100% 100%,8% 100%)"
        if reverse
        else "polygon(8% 0,100% 0,92% 100%,0 100%)"
    )
    return (
        f'<div style="position:absolute;{edge};top:{top}px;width:560px;z-index:6;'
        f'display:flex;flex-direction:{"row-reverse" if reverse else "row"};'
        f'align-items:center;gap:14px">'
        f"{lead}"
        f'<div style="flex:1;min-width:0;padding:13px 20px;'
        f'background:linear-gradient(90deg,var(--acc),var(--acc2));'
        f'clip-path:{clip};box-shadow:0 10px 24px rgba(0,0,0,.45)">'
        f'<b style="font-size:25px;line-height:1.05;text-transform:uppercase;'
        f'color:#fff;letter-spacing:.2px">{label}</b></div></div>'
    )


def _promo_markers(parts: Sequence[dict], rect: tuple[float, float, float, float]) -> str:
    left, top, width, height = rect
    marks: list[str] = []
    for index, part in enumerate(list(parts)[:5]):
        try:
            x = float(part.get("x"))
            y = float(part.get("y"))
        except (TypeError, ValueError):
            continue
        nx = (x / 1000 - left) / max(width, 0.01)
        ny = (y / 1000 - top) / max(height, 0.01)
        if not (0 <= nx <= 1 and 0 <= ny <= 1):
            continue
        marks.append(
            f'<span style="position:absolute;left:{nx * 100:.2f}%;top:{ny * 100:.2f}%;'
            f'transform:translate(-50%,-50%);width:64px;height:64px;border-radius:50%;'
            f'background:var(--acc);border:5px solid #fff;color:#fff;'
            f'font-size:28px;font-weight:800;display:flex;align-items:center;'
            f'justify-content:center;box-shadow:0 10px 24px rgba(0,0,0,.55)">'
            f"{index + 1}</span>"
        )
    return "".join(marks)


def _promo_crop_urls(hero: bytes, parts: Sequence[dict], limit: int = 4) -> list[str]:
    try:
        from .detail_specs import (  # noqa: PLC0415
            crop_reference_bytes,
            png_data_url,
            specs_from_callout_parts,
        )

        urls: list[str] = []
        for spec in specs_from_callout_parts(parts, limit=limit, crop_side=0.26):
            urls.append(png_data_url(crop_reference_bytes(hero, spec.crop)))
        return urls
    except Exception:  # noqa: BLE001 - 裁切失败只是少几张圆图，不影响版式
        return []


def _promo_tagline(items: Sequence[dict[str, Any]], subline: str) -> str:
    # 底条不再回退成副标题：副标题已经在标题区展示过，再复读一遍很廉价。
    labels = [str(item.get("label") or "").strip().upper() for item in items if item.get("label")]
    if len(labels) >= 3:
        return " · ".join(labels[:3])
    return "PRECISION BUILT · EVERYDAY RELIABLE"


def _promo_hero_fit(hero_url: str, contain: bool) -> tuple[str, str]:
    """照片在可见窗口里用 cover 还是 contain。

    成图终检发现商品被裁时，调用方传 ``contain=True``：整图完整缩放，
    窗口内的空白用同一张照片的模糊放大版补底（不露白色硬块）。
    返回 ``(object-fit, 背景层 HTML)``。
    """

    if not contain:
        return "cover", ""
    backdrop = (
        f'<img src="{hero_url}" style="position:absolute;inset:0;width:100%;height:100%;'
        f'object-fit:cover;filter:blur(34px) brightness(.78);transform:scale(1.08)">'
    )
    return "contain", backdrop


def _promo_document(
    *,
    template: str,
    skeleton: str,
    hero: bytes,
    headline: str,
    subline: str,
    bullets: Sequence[str],
    details: Sequence[tuple[bytes, str]],
    callout_parts: Sequence[dict],
    size: int,
    category_id: str,
    contain: bool = False,
) -> str:
    head_font, body_font = font_pair(category_id)
    palette = _promo_palette(category_id, hero)
    spec = _SKELETON_SPEC.get(skeleton, _SKELETON_SPEC["S1"])
    _style, items = _skeleton_items(
        template, bullets, subline, details, callout_parts, int(spec["max"])
    )
    if not items and skeleton != "S3":
        # 内容只有标题时，S2 会留出空右栏、S4 会留出空卡片带。统统改满版叠加：
        # 大字版式要的是一整张商品大图，宁可结构统一，也不留空洞。
        skeleton = "S3"
    hero_url = _data_url(hero)
    tagline = _promo_tagline(items, subline)
    crops = _promo_crop_urls(hero, callout_parts, 4) if callout_parts else []
    if crops:
        # 有部位裁剪就把小图补进卡片/清单：参考图的每张卡都有真实细节特写。
        items = [
            {
                **item,
                "thumb": item.get("thumb") or (crops[index] if index < len(crops) else ""),
            }
            for index, item in enumerate(items)
        ]
    markers = (
        _promo_markers(callout_parts, (0, 0, 1, 1))
        if template == "callout_ring" and callout_parts
        else ""
    )
    photo_veil = (
        "background:"
        "radial-gradient(120% 90% at 72% 26%, rgba(0,0,0,0) 38%, rgba(0,0,0,.55) 100%),"
        "linear-gradient(180deg, rgba(0,0,0,.55) 0%, rgba(0,0,0,.08) 32%,"
        " rgba(0,0,0,.10) 62%, rgba(0,0,0,.66) 100%);"
    )
    grain = (
        "position:absolute;inset:0;pointer-events:none;z-index:2;"
        "background:"
        "repeating-linear-gradient(115deg, rgba(255,255,255,.022) 0 2px,"
        " transparent 2px 30px),"
        "radial-gradient(80% 55% at 82% 8%, var(--acc-glow), transparent 62%);"
    )

    if skeleton == "S1":
        header = _promo_title_block(
            headline, subline, body_font, top=44, left=56, width="88%", h1=88, h1max=250
        )
        cards = "".join(
            _promo_card(item, index, height=372) for index, item in enumerate(items[:4])
        )
        band_top = 64 if items else 82
        hero_fit, hero_backdrop = _promo_hero_fit(hero_url, contain)
        if items:
            band = (
                f'<div style="position:absolute;left:0;right:0;bottom:0;height:{100 - band_top}%;'
                f'background:linear-gradient(180deg,#15161a 0%,#08090b 100%);'
                f'border-top:3px solid var(--acc);padding:24px 44px 0;display:flex;'
                f'flex-direction:column;gap:14px">'
                f'<div style="display:flex;justify-content:space-between;align-items:center">'
                f'<span style="font-family:{body_font};font-size:20px;letter-spacing:5px;'
                f'color:var(--acc-light)">PRODUCT DETAILS</span>'
                f'<span style="font-family:{body_font};font-size:18px;letter-spacing:3px;'
                f'color:rgba(255,255,255,.55)">// BUILT TO PERFORM</span></div>'
                f'<div style="display:flex;gap:16px;flex:1;min-height:0">{cards}</div>'
                f'<div style="height:64px;margin:0 -44px;background:linear-gradient(90deg,'
                f'var(--acc),var(--acc2));display:flex;align-items:center;'
                f'padding:0 44px;gap:16px">'
                f'<span style="font-family:{body_font};font-size:20px;letter-spacing:2px;'
                f'color:#fff;text-transform:uppercase">{_escape(tagline)}</span></div></div>'
            )
        else:
            # 纯标题版式没有细节卡：照片直接铺到 82%，底部只留一条品牌色横条。
            # 横条不重复副文案（标题块里已经有一份），保持干净。
            band = (
                f'<div style="position:absolute;left:0;right:0;bottom:0;height:{100 - band_top}%;'
                f'background:linear-gradient(90deg,var(--acc),var(--acc2))"></div>'
            )
        body = (
            f'<div style="position:absolute;left:0;top:0;width:100%;height:{band_top}%;'
            f'overflow:hidden">'
            f'{hero_backdrop}'
            f'<img src="{hero_url}" style="position:absolute;inset:0;width:100%;'
            f'height:100%;object-fit:{hero_fit};display:block"></div>'
            f'<div style="position:absolute;left:0;top:0;width:100%;height:{band_top}%;'
            f'{photo_veil}"></div>'
            f'<div style="position:absolute;left:0;top:0;width:100%;height:{band_top}%;'
            f'overflow:hidden">{markers}</div>'
            f"{header}"
            f"{band}"
        )
    elif skeleton == "S2":
        header = _promo_title_block(
            headline, subline, body_font, top=64, left=1000, width="560px", h1=64, h1max=250
        )
        rows = "".join(_promo_row(item, index) for index, item in enumerate(items[:6]))
        hero_fit, hero_backdrop = _promo_hero_fit(hero_url, contain)
        body = (
            f'<div style="position:absolute;left:0;top:0;width:58%;height:100%;'
            f'overflow:hidden">'
            f'{hero_backdrop}'
            f'<img src="{hero_url}" style="position:absolute;inset:0;width:100%;'
            f'height:100%;object-fit:{hero_fit};display:block"></div>'
            f'<div style="position:absolute;left:0;top:0;width:58%;height:100%;'
            f'{photo_veil}"></div>'
            f'<div style="position:absolute;left:0;top:0;width:58%;height:100%;'
            f'overflow:hidden">{markers}</div>'
            f'<div style="position:absolute;left:58%;right:0;top:0;bottom:0;'
            f'background:linear-gradient(160deg,#131418 0%,#08090b 100%);'
            f'border-left:4px solid var(--acc);padding:0 44px"></div>'
            f"{header}"
            f'<div style="position:absolute;left:1000px;right:44px;top:430px;bottom:120px;'
            f'overflow:hidden">{rows}</div>'
            f'<div style="position:absolute;left:1000px;right:44px;bottom:44px;height:64px;'
            f'background:linear-gradient(90deg,var(--acc),var(--acc2));'
            f'display:flex;align-items:center;padding:0 20px">'
            f'<span style="font-family:{body_font};font-size:18px;letter-spacing:2px;'
            f'color:#fff;text-transform:uppercase">{_escape(tagline)}</span></div>'
        )
    elif skeleton == "S4":
        header = _promo_title_block(
            headline, subline, body_font, top=950, left=96, width="86%",
            h1=72, h1max=150, sub_size=24,
        )
        cards = "".join(
            _promo_card(item, index, height=304) for index, item in enumerate(items[:4])
        )
        cards_row = (
            f'<div style="position:absolute;left:6%;right:6%;bottom:3%;height:19%;'
            f'display:flex;gap:14px">{cards}</div>'
            if cards
            else ""
        )
        hero_fit, hero_backdrop = _promo_hero_fit(hero_url, contain)
        body = (
            f'<div style="position:absolute;inset:0;background:'
            f'radial-gradient(80% 60% at 80% 6%,var(--acc-glow),transparent 60%),'
            f'#0a0b0d"></div>'
            f'<div style="position:absolute;left:6%;top:6%;width:88%;height:50%;'
            f'overflow:hidden;border:3px solid var(--acc);'
            f'clip-path:polygon(0 0,100% 0,100% calc(100% - 34px),'
            f'calc(100% - 34px) 100%,0 100%);'
            f'box-shadow:0 26px 60px rgba(0,0,0,.55)">'
            f'{hero_backdrop}'
            f'<img src="{hero_url}" style="position:absolute;inset:0;width:100%;height:100%;'
            f'object-fit:{hero_fit};display:block">'
            f'<div style="position:absolute;inset:0;{photo_veil}"></div>'
            f'<div style="position:absolute;inset:0">{markers}</div></div>'
            f"{header}"
            f"{cards_row}"
        )
    else:  # S3 满版叠加：标题 + 四角圆形细节 + 底部橙色条
        header = _promo_title_block(
            headline, subline, body_font, top=44, left=56, width="92%", h1=88, h1max=280
        )
        positions = [(34, "left"), (58, "left"), (34, "right"), (58, "right")]
        callouts = ""
        for index, item in enumerate(items[:4]):
            top_pct, side = positions[index]
            thumb = _promo_thumb(item)
            callouts += _promo_callout(
                item,
                index,
                thumb,
                top=int(size * top_pct / 100),
                side=side,
            )
        body = (
            f'<img src="{hero_url}" style="position:absolute;inset:0;width:100%;'
            f'height:100%;object-fit:cover;display:block">'
            f'<div style="position:absolute;inset:0;{photo_veil}"></div>'
            f"{markers}{header}{callouts}"
            f'<div style="position:absolute;left:0;right:0;bottom:0;height:78px;'
            f'background:linear-gradient(90deg,var(--acc),var(--acc2));'
            f'display:flex;align-items:center;gap:18px;padding:0 56px">'
            f'<span style="font-family:{body_font};font-size:22px;letter-spacing:2.4px;'
            f'color:#fff;text-transform:uppercase">{_escape(tagline)}</span></div>'
        )

    return (
        f'<!doctype html><html><head><meta charset="utf-8"><style>'
        f"*{{margin:0;padding:0;box-sizing:border-box}}"
        f"html,body{{width:{int(size)}px;height:{int(size)}px;overflow:hidden;background:#08090b}}"
        f'.stage{{position:relative;width:{int(size)}px;height:{int(size)}px;overflow:hidden;'
        f'background:#08090b;font-family:{head_font};color:#fff;'
        f'--acc:{palette["accent"]};--acc2:{palette["dark"]};'
        f'--acc-light:{palette["light"]};--acc-soft:{palette["soft"]};'
        f'--acc-border:{palette["border"]};--acc-line:{palette["line"]};'
        f'--acc-tint:{palette["tint"]};--acc-glow:{palette["glow"]}}}'
        f"</style></head><body><div class=\"stage\">{body}"
        f'<div style="{grain}"></div></div></body></html>'
    )


def build_skeleton_html(
    *,
    template: str,
    skeleton: str,
    hero: bytes,
    headline: str,
    subline: str = "",
    bullets: Sequence[str] = (),
    details: Sequence[tuple[bytes, str]] = (),
    callout_parts: Sequence[dict] = (),
    size: int = DEFAULT_SIZE,
    category_id: str = "",
    contain: bool = False,
    style_template: str = "auto",
) -> str:
    """按结构骨架拼出完整 HTML（纯字符串，不启动浏览器）。"""

    from .premium_composer import band_photo_bytes_safe  # noqa: PLC0415

    key = str(template or "").strip()
    sk = str(skeleton or "S1").strip().upper()
    if sk not in _SKELETON_SPEC:
        sk = "S1"
    if not hero:
        raise ValueError("skeleton layout needs a hero image")
    if not str(headline or "").strip():
        raise ValueError("skeleton layout needs a headline")

    if key != "white_main" and promo_theme(
        category_id, f"{headline} {subline}", style_template=style_template
    ):
        return _promo_document(
            template=key,
            skeleton=sk,
            hero=hero,
            headline=headline,
            subline=subline,
            bullets=bullets,
            details=details,
            callout_parts=callout_parts,
            size=int(size),
            category_id=category_id,
            contain=contain,
        )

    spec = _SKELETON_SPEC[sk]
    if sk == "S1":
        band_desired = 0.32
        photo, band = band_photo_bytes_safe(
            hero, band_desired, int(size), contain=contain
        )
        band_pct = round(band * 100, 2)
        photo_h = round(100 - band_pct, 2)
        top_crop = min(0.18, band)
        visible = (0.0, top_crop, 1.0, max(0.01, 1.0 - band))
    elif sk == "S2":
        band_pct = 0.0
        photo = b""
        photo_h = 100.0
        # 照片卡 56% 宽 × 90% 高 → cover 裁掉左右，保留中间 62.2% 宽。
        visible = (0.189, 0.0, 0.622, 1.0)
    elif sk == "S4":
        band_pct = 0.0
        photo = b""
        photo_h = 100.0
        # 照片卡 86% 宽 × 62% 高 → cover 裁掉上下，保留中间 72.1% 高。
        visible = (0.0, 0.1395, 1.0, 0.721)
    else:
        band_pct = 0.0
        photo = b""
        photo_h = 100.0
        visible = (0.0, 0.0, 1.0, 1.0)

    style, items = _skeleton_items(
        key, bullets, subline, details, callout_parts, int(spec["max"])
    )
    if not items and sk != "S3":
        # 同 promo 主题：没有清单内容时改满版叠加，避免侧栏/卡片带留大块空页。
        sk = "S3"
        visible = (0.0, 0.0, 1.0, 1.0)
    head_font, body_font = font_pair(category_id)
    page, page_tone = _backdrop(hero)
    accent, accent_light = _palette(hero, page)
    hero_url = _data_url(hero)
    headline_html = _headline_html(headline)
    sub_html = (
        f'<p style="margin-top:14px;font-size:30px;line-height:1.32;color:rgba(28,30,34,.66);'
        f'font-family:{body_font}">{_escape(subline)}</p>'
        if str(subline or "").strip()
        else ""
    )
    markers = (
        _sk_callout_markers(callout_parts, visible)
        if key == "callout_ring"
        else ""
    )
    # 商品包围盒参与 S2/S4 的 cover 裁切定位：按商品真实中心对齐，
    # 而不是一律居中（否则商品偏一侧时会被裁掉边缘）。
    s2_position = "center center"
    s4_position = "center center"
    try:
        from .premium_composer import content_bbox_ratios  # noqa: PLC0415

        bbox = content_bbox_ratios(hero)
    except Exception:  # noqa: BLE001
        bbox = None
    if bbox:
        left_ratio, top_ratio, right_ratio, bottom_ratio = bbox
        center_x = (left_ratio + right_ratio) / 2
        center_y = (top_ratio + bottom_ratio) / 2
        # S2 照片卡可见宽度 62.2%，S4 可见高度 72.1%
        pos_x = 0.5 + (center_x - 0.5) / 0.622
        pos_y = 0.5 + (center_y - 0.5) / 0.721
        s2_position = f"{max(0.0, min(1.0, pos_x)) * 100:.1f}% center"
        s4_position = f"center {max(0.0, min(1.0, pos_y)) * 100:.1f}%"

    def render_items(glass: bool = False) -> str:
        return "".join(
            _sk_item_html(
                style,
                item,
                index,
                label_size=int(spec["label"]),
                item_h=int(spec["item_h"]),
                glass=glass,
            )
            for index, item in enumerate(items)
        ).replace("@@ACCENT_LIGHT@@", accent_light).replace("@@ACCENT@@", accent)

    if sk == "S1":
        content = (
            f'<div style="display:flex;gap:{spec["gap"]}px">{render_items()}</div>'
            if items
            else ""
        )
        body = (
            f'<img src="{_data_url(photo)}" style="position:absolute;left:0;top:0;width:100%;'
            f'height:{photo_h}%;object-fit:fill;display:block">'
            f'<div style="position:absolute;left:0;top:0;width:100%;height:{photo_h}%">{markers}</div>'
            f'<div style="position:absolute;left:0;right:0;bottom:0;height:{band_pct}%;background:#fff;'
            f'padding:34px 56px;display:flex;flex-direction:column;justify-content:space-between">'
            f'<div>{f"<div style=\"font-size:22px;letter-spacing:8px;font-weight:700;color:#b4470a;text-transform:uppercase;font-family:{body_font};margin-bottom:8px\">Key Features</div>" if items else ""}'
            f'<h1 data-max-height="{spec["h1max"]}" data-min-height="40" '
            f'style="font-size:{spec["h1"]}px;line-height:.94;letter-spacing:-1.8px;text-transform:uppercase;color:#1c1e22">'
            f"{headline_html}</h1>"
            f'<div style="width:150px;height:8px;border-radius:99px;margin-top:12px;'
            f'background:linear-gradient(90deg,{accent_light},{accent})"></div>'
            f'{sub_html if style == "none" else ""}'
            f"</div>{content}</div>"
        )
    elif sk == "S2":
        rows = "".join(
            _sk_item_html(
                style,
                item,
                index,
                label_size=int(spec["label"]),
                item_h=int(spec["item_h"]),
            ).replace('style="flex:1;', 'style="width:100%;')
            for index, item in enumerate(items)
        ).replace("@@ACCENT_LIGHT@@", accent_light).replace("@@ACCENT@@", accent)
        body = (
            f'<div style="position:absolute;inset:0;background:#f3efe7"></div>'
            f'<div style="position:absolute;left:4%;top:5%;width:56%;height:90%;border-radius:44px;'
            f'overflow:hidden;box-shadow:0 40px 90px rgba(60,48,32,.18)">'
            f'<img src="{hero_url}" style="width:100%;height:100%;object-fit:cover;'
            f'object-position:{s2_position}">'
            f"{markers}</div>"
            f'<div style="position:absolute;left:63%;right:4%;top:5%;height:90%;background:#fff;'
            f'border-radius:40px;padding:38px 34px;box-shadow:0 30px 70px rgba(60,48,32,.12);'
            f'display:flex;flex-direction:column">'
            f'<h1 data-max-height="{spec["h1max"]}" data-min-height="40" '
            f'style="font-size:{spec["h1"]}px;line-height:.96;letter-spacing:-1.5px;text-transform:uppercase;color:#1c1e22">'
            f"{headline_html}</h1>"
            f'<div style="width:120px;height:7px;border-radius:99px;margin:16px 0 22px;'
            f'background:linear-gradient(90deg,{accent_light},{accent})"></div>'
            f'<div style="display:flex;flex-direction:column;gap:{spec["gap"]}px">{rows}</div>'
            f"</div>"
        )
    elif sk == "S3":
        content = (
            f'<div style="display:flex;gap:{spec["gap"]}px">{render_items(glass=True)}</div>'
            if items
            else ""
        )
        body = (
            f'<img src="{hero_url}" style="position:absolute;inset:0;width:100%;height:100%;object-fit:cover">'
            f'<div style="position:absolute;inset:0;background:linear-gradient(180deg,'
            f'rgba(8,10,14,.66) 0%,rgba(8,10,14,.10) 34%,rgba(8,10,14,.22) 66%,rgba(8,10,14,.72) 100%)"></div>'
            f'<div style="position:absolute;left:60px;right:60px;top:54px">'
            f'<div style="font-size:22px;letter-spacing:8px;font-weight:700;color:#ffb27a;'
            f'text-transform:uppercase;font-family:{body_font};margin-bottom:12px">Key Features</div>'
            f'<h1 data-max-height="{spec["h1max"]}" data-min-height="40" '
            f'style="font-size:{spec["h1"]}px;line-height:.94;letter-spacing:-2px;text-transform:uppercase;'
            f'color:#fff;text-shadow:0 8px 30px rgba(0,0,0,.45)">{headline_html}</h1>'
            f'<div style="width:150px;height:8px;border-radius:99px;margin-top:14px;'
            f'background:linear-gradient(90deg,{accent_light},{accent})"></div></div>'
            f'{markers}'
            f'<div style="position:absolute;left:48px;right:48px;bottom:48px">{content}</div>'
        )
    else:  # S4
        content = (
            f'<div style="display:flex;gap:{spec["gap"]}px">{render_items()}</div>'
            if items
            else ""
        )
        body = (
            f'<div style="position:absolute;inset:0;background:#f4f0e8"></div>'
            f'<div style="position:absolute;left:7%;top:4%;width:86%;height:62%;border-radius:44px;'
            f'overflow:hidden;box-shadow:0 34px 80px rgba(60,48,32,.16)">'
            f'<img src="{hero_url}" style="width:100%;height:100%;object-fit:cover;'
            f'object-position:{s4_position}">'
            f"{markers}</div>"
            f'<div style="position:absolute;left:7%;right:7%;top:69%">'
            f'<h1 data-max-height="{spec["h1max"]}" data-min-height="40" '
            f'style="font-size:{spec["h1"]}px;line-height:.94;letter-spacing:-1.8px;text-transform:uppercase;color:#1c1e22">'
            f"{headline_html}</h1>{sub_html}"
            f'<div style="width:130px;height:7px;border-radius:99px;margin-top:14px;'
            f'background:linear-gradient(90deg,{accent_light},{accent})"></div></div>'
            f'<div style="position:absolute;left:7%;right:7%;bottom:4%">{content}</div>'
        )

    return (
        f'<!doctype html><html><head><meta charset="utf-8"><style>'
        f"*{{margin:0;padding:0;box-sizing:border-box}}"
        f"html,body{{width:{int(size)}px;height:{int(size)}px;overflow:hidden;background:#fff}}"
        f'.stage{{position:relative;width:{int(size)}px;height:{int(size)}px;overflow:hidden;'
        f'background:#fff;font-family:{head_font};color:#1c1e22}}'
        f"</style></head><body><div class=\"stage\">{body}</div></body></html>"
    )


def render_skeleton(
    *,
    template: str,
    skeleton: str,
    hero: bytes,
    headline: str,
    subline: str = "",
    bullets: Sequence[str] = (),
    details: Sequence[tuple[bytes, str]] = (),
    callout_parts: Sequence[dict] = (),
    size: int = DEFAULT_SIZE,
    category_id: str = "",
    scale: float = 2.0,
    contain: bool = False,
    style_template: str = "auto",
) -> bytes:
    """渲染结构骨架版式；浏览器不可用时抛异常，调用方回退 PIL。

    默认 2× 超采样：按 ``size*scale`` 视口渲染再降采样回 ``size``，
    文字/线条抗锯齿明显更干净，只多一点渲染耗时。
    """

    if not html_layout_enabled():
        raise RuntimeError("html layout disabled")
    factor = max(1, int(round(float(scale or 1.0))))
    document = build_skeleton_html(
        template=template,
        skeleton=skeleton,
        hero=hero,
        headline=headline,
        subline=subline,
        bullets=bullets,
        details=details,
        callout_parts=callout_parts,
        size=size,
        category_id=category_id,
        contain=contain,
        style_template=style_template,
    )
    content = render_html(document, width=int(size), height=int(size), scale=factor)
    if factor > 1:
        content = _downscale_jpeg(content, int(size))
    return content


def _downscale_jpeg(content: bytes, size: int) -> bytes:
    """把超采样渲染结果降采样到目标尺寸（不改变最终格式）。"""

    from io import BytesIO

    from PIL import Image

    with Image.open(BytesIO(content)) as opened:
        image = opened.convert("RGB")
    if image.size != (size, size):
        image = image.resize((size, size), Image.LANCZOS)
    buffer = BytesIO()
    image.save(buffer, format="JPEG", quality=94)
    return buffer.getvalue()
