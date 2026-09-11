"""启动器核心逻辑：纯 Python 实现，不依赖 PySide6，可用 CLI 单独运行测试。

职责：
  - 定位产品数据目录与 workbench.sqlite3
  - 读取本机 system_config（仅非密钥字段）
  - 加载 golden（服务器 > 本地文件 > 内置 default_golden.json 兜底）
  - 配置对齐比对 / 连通性探测
  - 生成体检报告；一键同步（带备份、不触碰密钥）
  - CLI: --check / --export-golden / --sync / --start
"""
from __future__ import annotations

import json
import os
import socket
import ssl
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib import error, request
from urllib.parse import urlparse

CONFIG_KEY = "system_config"
DEFAULT_PORT = 8010
DEFAULT_AUTH_BASE_URL = "https://workbench.haocoming.top/auth-api"
# 产品处理固定模型/规格/超时（provider_config），用于“参考值”展示与异常提示。
PROVIDER_REFERENCE: dict[str, Any] = {
    "text_model": "gpt-5.6-terra",
    "image_model": "image_gpt",
    "image_size": "2048x2048",
    "image_quality": "medium",
    "premium_image_mode_model": "gpt-image-2-4k",
    "premium_image_size": "4096x4096",
    "text_timeout_seconds": 300.0,
    "image_timeout_seconds": 600.0,
}
# 产品处理的关键依赖（engine_status 的 diagnostics.dependencies 会逐项校验）。
# key 为接口返回的字段名，value 为展示名。
REQUIRED_DEP_LABELS: dict[str, str] = {
    "openpyxl": "openpyxl",
    "python_multipart": "python-multipart",
    "pillow": "Pillow",
    "opencv": "OpenCV",
    "rapidocr": "RapidOCR",
}


@dataclass
class CheckResult:
    key: str
    group: str
    status: str  # ok | warn | fail | skip
    message: str
    local: Any = None
    expected: Any = None


@dataclass
class LauncherReport:
    checks: list[CheckResult] = field(default_factory=list)
    golden_source: str = "unavailable"
    golden_version: str = ""
    db_path: str = ""
    config_found: bool = False

    @property
    def failed(self) -> list[CheckResult]:
        return [c for c in self.checks if c.status == "fail"]

    @property
    def warned(self) -> list[CheckResult]:
        return [c for c in self.checks if c.status == "warn"]

    @property
    def ok(self) -> list[CheckResult]:
        return [c for c in self.checks if c.status == "ok"]

    def as_dict(self) -> dict[str, Any]:
        return {
            "overall": "pass" if not self.failed else ("warn" if self.warned else "fail"),
            "golden_source": self.golden_source,
            "golden_version": self.golden_version,
            "db_path": self.db_path,
            "config_found": self.config_found,
            "checks": [c.__dict__ for c in self.checks],
            "stats": {
                "ok": len(self.ok),
                "warn": len(self.warned),
                "fail": len(self.failed),
                "total": len(self.checks),
            },
        }


# --------------------------------------------------------------------------- #
# 路径与 DB 读取
# --------------------------------------------------------------------------- #
def data_dir_from_env() -> Path | None:
    """按 wh_local/config 同款优先级定位数据目录。"""
    db_override = os.environ.get("WH_LOCAL_DATABASE_PATH")
    if db_override:
        return Path(db_override).parent
    data_override = os.environ.get("WH_LOCAL_DATA_DIR")
    if data_override:
        return Path(data_override)
    if getattr(sys, "frozen", False):
        appdata = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
        return Path(appdata) / "MainPG" / "outputs" / "wh-local"
    return Path.cwd() / "outputs" / "wh-local"


def resolve_db_path() -> Path:
    db_override = os.environ.get("WH_LOCAL_DATABASE_PATH")
    if db_override:
        return Path(db_override)
    return data_dir_from_env() / "workbench.sqlite3"


def read_local_config(db_path: Path) -> dict[str, Any] | None:
    """读取 workbench_settings 中 key=system_config 的 value_json。

    只返回非密钥字段（真实密钥保存在 secret_values 表，不在这里）。"""
    if not db_path or not db_path.is_file():
        return None
    try:
        import sqlite3

        with sqlite3.connect(str(db_path)) as conn:
            row = conn.execute(
                "SELECT value_json FROM workbench_settings WHERE key = ?", (CONFIG_KEY,)
            ).fetchone()
    except sqlite3.Error:
        return None
    if row is None or not row[0]:
        return None
    try:
        value = json.loads(row[0])
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else None


def normalize_runtime(config: dict[str, Any] | None) -> dict[str, Any]:
    """抽取“需与 golden 对齐”的非密钥运行时字段子集。"""
    if not isinstance(config, dict):
        return {"limits": {}, "cos": {}, "updates": {}}
    limits = config.get("limits") or {}
    cos = config.get("cos") or {}
    updates = config.get("updates") or {}
    return {
        "limits": {
            "text_workers": limits.get("text_workers"),
            "image_workers": limits.get("image_workers"),
            "text_request_limit": limits.get("text_request_limit"),
            "image_request_limit": limits.get("image_request_limit"),
            "image_retry_attempts": limits.get("image_retry_attempts"),
            "image_provider_strategy": limits.get("image_provider_strategy"),
            "provider_backup_share_percent": limits.get("provider_backup_share_percent"),
            "image_stop_after_billable_failure": limits.get("image_stop_after_billable_failure"),
        },
        "cos": {"bucket": cos.get("bucket"), "region": cos.get("region")},
        "updates": {
            "cos_prefix": updates.get("cos_prefix"),
            "public_base_url": updates.get("public_base_url"),
        },
    }


# --------------------------------------------------------------------------- #
# golden 加载：服务器 > 本地文件 > 内置兜底
# --------------------------------------------------------------------------- #
def bundled_golden_path() -> Path:
    """内置兜底基准。PyInstaller onefile 时资源在 sys._MEIPASS；源码运行时在 launcher 目录。"""
    if getattr(sys, "frozen", False):
        meipass = getattr(sys, "_MEIPASS", "")
        if meipass:
            p = Path(meipass) / "default_golden.json"
            if p.is_file():
                return p
        return Path(sys.executable).resolve().parent / "default_golden.json"
    return Path(__file__).resolve().parent / "default_golden.json"


def load_golden_file(path: Path) -> dict[str, Any] | None:
    if not path or not path.is_file():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def fetch_golden_server(url: str, timeout: float = 12.0) -> tuple[dict[str, Any] | None, str]:
    """尝试从服务器下载 golden，失败返回 (None, 原因)。

    兼容两种返回结构：
      - 顶层即 golden：{"runtime": {...}, "version": ...}
      - 包裹结构：{"version": ..., "updated_at": ..., "golden": {"runtime": {...}, ...}}
    """
    if not url:
        return None, "未配置服务器 golden 地址"
    try:
        req = request.Request(url, headers={"Accept": "application/json"}, method="GET")
        with request.urlopen(req, timeout=timeout) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except error.HTTPError as exc:
        return None, f"服务器返回 HTTP {exc.code}"
    except (error.URLError, TimeoutError, OSError, ValueError) as exc:
        return None, f"服务器不可达：{exc}"
    if not isinstance(payload, dict):
        return None, "服务器返回的 golden 结构无效"
    inner = payload.get("golden") if isinstance(payload.get("golden"), dict) else payload
    if not isinstance(inner, dict) or not isinstance(inner.get("runtime"), dict):
        return None, "服务器返回的 golden 结构无效"
    return inner, "server"


def load_golden(server_url: str = "", local_file: str = "", timeout: float = 12.0) -> tuple[dict[str, Any], str]:
    """按优先级选取 golden：服务器 > 指定文件 > 内置兜底。返回 (golden, source_desc)。"""
    if server_url:
        payload, source = fetch_golden_server(server_url, timeout)
        if payload is not None:
            return payload, f"server({source})"
    if local_file:
        payload = load_golden_file(Path(local_file))
        if payload is not None:
            return payload, f"file:{local_file}"
    payload = load_golden_file(bundled_golden_path())
    if payload is not None:
        return payload, "bundled"
    return {"runtime": {}, "provider": PROVIDER_REFERENCE, "auth_base_url": DEFAULT_AUTH_BASE_URL}, "builtin-empty"


# --------------------------------------------------------------------------- #
# 比对
# --------------------------------------------------------------------------- #
def _value_equal(local: Any, expected: Any) -> bool:
    if expected is None:
        return True  # golden 未定义为约束，跳过比对
    if isinstance(expected, bool):
        return bool(local) is expected
    if isinstance(expected, (int, float)):
        try:
            return float(local) == float(expected)
        except (TypeError, ValueError):
            return False
    return str(local).strip() == str(expected).strip()


def compare(local: dict[str, Any] | None, golden: dict[str, Any]) -> list[CheckResult]:
    golden_runtime = golden.get("runtime") or {}
    local_rt = normalize_runtime(local)
    checks: list[CheckResult] = []

    # 对齐 1: limits
    gl_limits = golden_runtime.get("limits") or {}
    if gl_limits:
        for key, expected in gl_limits.items():
            actual = (local_rt["limits"] or {}).get(key)
            if not _value_equal(actual, expected):
                checks.append(
                    CheckResult(
                        key=f"limit.{key}",
                        group="配置对齐",
                        status="fail",
                        message=f"并发/限流参数 {key} 与基准不一致",
                        local=actual,
                        expected=expected,
                    )
                )
    # 对齐 2: updates
    gu = golden_runtime.get("updates") or {}
    if gu:
        for key, expected in gu.items():
            actual = (local_rt["updates"] or {}).get(key)
            if not _value_equal(actual, expected):
                checks.append(
                    CheckResult(
                        key=f"update.{key}",
                        group="配置对齐",
                        status="fail",
                        message=f"更新配置 {key} 与基准不一致",
                        local=actual,
                        expected=expected,
                    )
                )
    # 对齐 3: cos bucket/region（golden 里 bucket 为空则只校验“已配置”）
    gc = golden_runtime.get("cos") or {}
    local_cos = local_rt["cos"] or {}
    if str(gc.get("bucket") or "").strip():
        if str(local_cos.get("bucket") or "").strip() != str(gc.get("bucket")).strip():
            checks.append(
                CheckResult(
                    key="cos.bucket",
                    group="配置对齐",
                    status="fail",
                    message="COS 发布桶与基准不一致",
                    local=local_cos.get("bucket"),
                    expected=gc.get("bucket"),
                )
            )
    if local is None:
        checks.append(
            CheckResult(
                key="config.present",
                group="配置对齐",
                status="fail",
                message="未找到本机 system_config，可能从未保存过配置",
            )
        )
        return checks
    if not local_cos.get("bucket") or not local_cos.get("region"):
        checks.append(
            CheckResult(
                key="cos.configured",
                group="配置对齐",
                status="warn",
                message="COS 未配置（bucket/region 缺失），发布成品图会失败",
                local=f"bucket={local_cos.get('bucket')!r}, region={local_cos.get('region')!r}",
                expected="bucket 与 region 均非空",
            )
        )
    return checks


# --------------------------------------------------------------------------- #
# 连通性
# --------------------------------------------------------------------------- #
def _parse_host(url: str) -> tuple[str, int]:
    parsed = urlparse(url)
    host = parsed.hostname or ""
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    return host, port


def check_tcp_tls(url: str, timeout: float = 6.0) -> CheckResult:
    host, port = _parse_host(url)
    if not host:
        return CheckResult("net.tcp_tls", "连通性", "fail", f"无法解析地址：{url}", expected=url)
    start = time.perf_counter()
    try:
        with socket.create_connection((host, port), timeout=timeout) as sock:
            if port == 443:
                ctx = ssl.create_default_context()
                with ctx.wrap_socket(sock, server_hostname=host) as tls:
                    pass
        ms = int((time.perf_counter() - start) * 1000)
        return CheckResult("net.tcp_tls", "连通性", "ok", f"已建立连接，延迟约 {ms}ms",
                           local=ms, expected=url)
    except (OSError, ssl.SSLError) as exc:
        return CheckResult("net.tcp_tls", "连通性", "fail", f"无法连通 {url}：{exc}", expected=url)


def check_http(url: str, timeout: float = 8.0) -> CheckResult:
    start = time.perf_counter()
    try:
        req = request.Request(url, headers={"Accept": "application/json", "User-Agent": "MainPG-Launcher"})
        with request.urlopen(req, timeout=timeout) as resp:
            ms = int((time.perf_counter() - start) * 1000)
            body = resp.read(64).decode("utf-8", errors="replace")
            return CheckResult("net.http", "连通性", "ok",
                               f"HTTP {resp.status} 可访问，延迟约 {ms}ms",
                               local=resp.status, expected=url)
    except error.HTTPError as exc:
        ms = int((time.perf_counter() - start) * 1000)
        # 服务器有响应即说明网关可达（含 4xx）；仅 5xx 视为服务端异常。
        status = "warn" if exc.code >= 500 else "ok"
        if status == "ok":
            return CheckResult("net.http", "连通性", "ok",
                               f"HTTP {exc.code} 可达（{ms}ms），网关服务正常响应", local=exc.code, expected=url)
        return CheckResult("net.http", "连通性", "warn",
                           f"服务器返回 HTTP {exc.code}（{ms}ms），疑似服务端异常", local=exc.code, expected=url)
    except (error.URLError, TimeoutError, OSError) as exc:
        return CheckResult("net.http", "连通性", "fail", f"HTTP 请求失败：{exc}", expected=url)


def port_free(port: int = DEFAULT_PORT) -> CheckResult:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=1.0):
            return CheckResult("port.8010", "运行时", "warn",
                               f"端口 {port} 已被占用（可能有残留实例），请先退出旧实例后再启动")
    except OSError:
        return CheckResult("port.8010", "运行时", "ok", f"端口 {port} 空闲")


def _get_json(base_url: str, path: str, timeout: float = 6.0) -> dict[str, Any]:
    """GET 一个本地/远端 JSON 端点，返回 dict。"""
    req = request.Request(base_url.rstrip("/") + path, method="GET",
                          headers={"Accept": "application/json", "User-Agent": "MainPG-Launcher"})
    with request.urlopen(req, timeout=timeout) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    return payload if isinstance(payload, dict) else {"_raw": payload}


# --------------------------------------------------------------------------- #
# 体检报告
# --------------------------------------------------------------------------- #
def build_report(
    db_path: Path | None = None,
    server_golden_url: str = "",
    golden_file: str = "",
    auth_base_url: str = "",
    ping_http: bool = True,
    check_port: bool = True,
) -> LauncherReport:
    db_path = db_path or resolve_db_path()
    report = LauncherReport(db_path=str(db_path))
    report.config_found = db_path.is_file()

    golden, source = load_golden(server_golden_url, golden_file)
    report.golden_source = source
    report.golden_version = str(golden.get("version") or "")

    # 数据库/配置存在性
    if not report.config_found:
        report.checks.append(
            CheckResult("db.present", "配置", "fail",
                        f"未找到数据文件：{db_path}", expected=str(db_path))
        )
    local = read_local_config(db_path)
    if report.config_found and local is None:
        report.checks.append(
            CheckResult("db.config", "配置", "warn",
                        "数据库存在但未写入 system_config（可能从未在系统配置页保存）")
        )

    # 配置对齐
    report.checks.extend(compare(local, golden))

    # 连通性
    auth = auth_base_url or str(golden.get("auth_base_url") or DEFAULT_AUTH_BASE_URL)
    report.checks.append(check_tcp_tls(auth))
    if ping_http:
        report.checks.append(check_http(auth))

    if check_port:
        report.checks.append(port_free())

    return report


# --------------------------------------------------------------------------- #
# 启动后体检：探测产品自身 /health 与 /engine/status（依赖/能力由产品自报）
# --------------------------------------------------------------------------- #
def probe_product(base_url: str = "http://127.0.0.1:%d" % DEFAULT_PORT, timeout: float = 6.0) -> tuple[list[CheckResult], dict[str, Any] | None]:
    """探测运行中的主程序。/engine/status 会给出依赖与 text/image/ocr 能力。

    主程序未启动时返回一个 fail 检查项，不抛异常。"""
    results: list[CheckResult] = []
    try:
        req = request.Request(f"{base_url.rstrip('/')}/health", method="GET", headers={"Accept": "application/json"})
        with request.urlopen(req, timeout=timeout) as resp:
            health = json.loads(resp.read().decode("utf-8"))
    except error.HTTPError as exc:
        results.append(CheckResult("engine.boot", "依赖/能力", "fail", f"主程序响应异常：HTTP {exc.code}"))
        return results, None
    except (error.URLError, TimeoutError, OSError) as exc:
        results.append(CheckResult("engine.boot", "依赖/能力", "fail",
                                   f"主程序未在 {base_url} 响应（{exc}），请先启动主程序"))
        return results, None

    status = "ok" if bool(health.get("ok")) else "fail"
    results.append(CheckResult("engine.boot", "依赖/能力", status,
                               f"主程序已就绪，数据库：{health.get('database_path') or '未知'}"))

    try:
        engine = _get_json(base_url.rstrip("/"), "/product-processing/engine/status")
    except (error.HTTPError, error.URLError, TimeoutError, OSError):
        try:
            engine = _get_json(base_url.rstrip("/"), "/api/product-processing/engine/status")
        except (error.HTTPError, error.URLError, TimeoutError, OSError) as exc:
            results.append(CheckResult("engine.status", "依赖/能力", "warn", f"无法获得引擎状态（{exc}）"))
            return results, None

    diag = engine.get("diagnostics") or {}
    deps = diag.get("dependencies") or {}
    if isinstance(deps, dict) and deps:
        for key, label in REQUIRED_DEP_LABELS.items():
            present = bool(deps.get(key))
            results.append(CheckResult(f"dep.{key}", "依赖/能力", "ok" if present else "fail",
                                       f"依赖 {label} {'已就绪' if present else '缺失'}"))
    capabilities = diag.get("capabilities") or {}
    for cap in ("text_ai", "image_ai", "ocr"):
        info = capabilities.get(cap) or {}
        if not info:
            continue
        enabled = bool(info.get("enabled", True))
        ready = bool(info.get("ready"))
        reason = (info.get("reason") or "").strip()
        if not enabled:
            # 主动关闭（如 OCR 质量门），不算故障，仅提示
            results.append(CheckResult(f"cap.{cap}", "依赖/能力", "warn",
                                       f"{cap} 已关闭：{reason or '未启用'}" if reason else f"{cap} 已关闭（未启用）"))
            continue
        if ready:
            results.append(CheckResult(f"cap.{cap}", "依赖/能力", "ok", f"{cap} 已就绪"))
        else:
            results.append(CheckResult(f"cap.{cap}", "依赖/能力", "fail",
                                       f"{cap} 未就绪：{reason}" if reason else f"{cap} 未就绪"))
            if reason:
                results.append(CheckResult(f"cap.{cap}.reason", "依赖/能力", "warn", reason))
    return results, engine


# --------------------------------------------------------------------------- #
# 一键同步：仅回写非密钥运行时字段，不触碰 secret_values，带头备份
# --------------------------------------------------------------------------- #
def apply_sync(db_path: Path, golden: dict[str, Any]) -> CheckResult:
    if not db_path or not db_path.is_file():
        return CheckResult("sync", "同步", "fail", f"数据文件不存在：{db_path}")
    if db_path.stat().st_size == 0:
        return CheckResult("sync", "同步", "fail", "数据文件为空")
    import sqlite3

    golden_runtime = golden.get("runtime") or {}
    gl_limits = golden_runtime.get("limits") or {}
    gl_cos = golden_runtime.get("cos") or {}
    gl_updates = golden_runtime.get("updates") or {}

    try:
        with sqlite3.connect(str(db_path)) as conn:
            row = conn.execute(
                "SELECT value_json FROM workbench_settings WHERE key = ?", (CONFIG_KEY,)
            ).fetchone()
            current: dict[str, Any] = {}
            if row and row[0]:
                try:
                    current = json.loads(row[0])
                except json.JSONDecodeError:
                    current = {}
            if not isinstance(current, dict):
                current = {}

            # 备份当前配置到 action_logs（可回滚）
            now = time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime())  # 近似 ISO
            conn.execute(
                """
                INSERT INTO action_logs(actor_id, action, target_type, target_id,
                                        request_json, result_json, created_at)
                VALUES(?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "launcher",
                    "launcher.config_sync_backup",
                    "system_config",
                    CONFIG_KEY,
                    json.dumps(current, ensure_ascii=False, sort_keys=True),
                    json.dumps({"ok": True}, ensure_ascii=False),
                    now,
                ),
            )

            # 回写非密钥字段
            limits = current.setdefault("limits", {})
            if gl_limits:
                limits.update({k: v for k, v in gl_limits.items() if v is not None})
            cos = current.setdefault("cos", {})
            for k in ("bucket", "region"):
                if str(gl_cos.get(k) or "").strip():
                    cos[k] = gl_cos[k]
            updates = current.setdefault("updates", {})
            if gl_updates:
                for k in ("cos_prefix", "public_base_url"):
                    if gl_updates.get(k) is not None:
                        updates[k] = gl_updates[k]

            conn.execute(
                """
                INSERT INTO workbench_settings(key, value_json, updated_by, updated_at)
                VALUES(?, ?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET
                    value_json = excluded.value_json,
                    updated_by = excluded.updated_by,
                    updated_at = excluded.updated_at
                """,
                (
                    CONFIG_KEY,
                    json.dumps(current, ensure_ascii=False, sort_keys=True),
                    "launcher",
                    now,
                ),
            )
            conn.commit()
    except sqlite3.Error as exc:
        return CheckResult("sync", "同步", "fail", f"同步失败：{exc}")
    return CheckResult("sync", "同步", "ok",
                       "已按基准同步非密钥配置，原值已备份到 action_logs，请重启主程序生效")


# --------------------------------------------------------------------------- #
# 导出 golden（供运营上传到服务器）
# --------------------------------------------------------------------------- #
def export_golden(local: dict[str, Any] | None, out_path: Path) -> CheckResult:
    if local is None:
        return CheckResult("export", "导出", "fail", "读取本地配置失败，无法导出")
    runtime = normalize_runtime(local)
    golden = {
        "version": time.strftime("%Y%m%d"),
        "config_name": "system_config.json",
        "runtime": runtime,
        "provider": PROVIDER_REFERENCE,
        "auth_base_url": DEFAULT_AUTH_BASE_URL,
        "required_deps": list(REQUIRED_DEP_LABELS.keys()),
    }
    try:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(golden, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError as exc:
        return CheckResult("export", "导出", "fail", f"写入失败：{exc}")
    return CheckResult("export", "导出", "ok", f"已导出 golden 到 {out_path}", local=str(out_path))


# --------------------------------------------------------------------------- #
# 主程序定位与 golden 地址解析 / 上传基准
# --------------------------------------------------------------------------- #
GOLDEN_URL_DEFAULT = "https://workbench.haocoming.top/update-admin/api/launcher/golden"


def product_exe_candidates() -> list[Path]:
    """可能的已安装主程序位置（按优先级）。"""
    appdata_local = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    cands: list[Path] = [
        Path(appdata_local) / "MainPG" / "MainPG.exe",
    ]
    if getattr(sys, "frozen", False):
        cands.append(Path(sys.executable).resolve().parent / "MainPG.exe")
    if os.environ.get("WH_APP_EXE"):
        cands.insert(0, Path(os.environ["WH_APP_EXE"]))
    return cands


def find_product() -> Path | None:
    for cand in product_exe_candidates():
        if cand.is_file():
            return cand
    return None


def golden_url_resolve(explicit: str = "") -> str:
    """golden 下载地址优先级：显式参数 > 环境变量 > 默认服务器端点。"""
    return explicit or os.environ.get("LAUNCHER_GOLDEN_URL", GOLDEN_URL_DEFAULT)


def build_golden_dict(local: dict[str, Any] | None) -> dict[str, Any] | None:
    if local is None:
        return None
    return {
        "version": time.strftime("%Y%m%d"),
        "config_name": "system_config.json",
        "runtime": normalize_runtime(local),
        "provider": PROVIDER_REFERENCE,
        "auth_base_url": DEFAULT_AUTH_BASE_URL,
        "required_deps": list(REQUIRED_DEP_LABELS.keys()),
    }


def upload_golden(local: dict[str, Any] | None, url: str, timeout: float = 15.0) -> CheckResult:
    """把本机非密钥配置作为基准上传到服务器（供运营手动发布新版本号）。"""
    golden = build_golden_dict(local)
    if golden is None:
        return CheckResult("upload", "上传", "fail", "读取本地配置失败，无法上传基准")
    if not url:
        return CheckResult("upload", "上传", "fail", "未配置服务器上传地址")
    try:
        import requests  # 仅在需要时引入，保持 core 依赖最小
    except ImportError as exc:
        return CheckResult("upload", "上传", "fail", f"缺少 requests 库：{exc}")
    # 服务器 schema: {version, payload}；payload 为完整 golden 内容
    body = {"version": golden["version"], "payload": golden}
    try:
        resp = requests.post(url, json=body, timeout=timeout)
        if resp.status_code >= 400:
            return CheckResult("upload", "上传", "fail",
                               f"服务器返回 HTTP {resp.status_code}：{resp.text[:200]}")
    except (OSError, ValueError) as exc:
        return CheckResult("upload", "上传", "fail", f"上传失败：{exc}")
    return CheckResult("upload", "上传", "ok",
                       f"已上传基准（路径 {url}），待运营侧手动发布新版本号后生效")


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def _cli() -> int:
    argv = sys.argv[1:]
    if "--check" in argv:
        data_dir = _arg_of(argv, "--data-dir")
        report = build_report(
            db_path=Path(data_dir) / "workbench.sqlite3" if data_dir else None,
            server_golden_url=_arg_of(argv, "--server-golden-url"),
            golden_file=_arg_of(argv, "--golden"),
            auth_base_url=_arg_of(argv, "--auth"),
        )
        print(json.dumps(report.as_dict(), ensure_ascii=False, indent=2))
        return 0 if not report.failed else 1
    if "--export-golden" in argv:
        db_path = Path(_arg_of(argv, "--data-dir") or "") / "workbench.sqlite3" if _arg_of(argv, "--data-dir") else resolve_db_path()
        local = read_local_config(db_path)
        out = Path(_arg_of(argv, "--out") or "golden.json")
        res = export_golden(local, out)
        print(json.dumps(res.__dict__, ensure_ascii=False, indent=2))
        return 0 if res.status == "ok" else 1
    if "--sync" in argv:
        db_path = resolve_db_path()
        golden, source = load_golden(_arg_of(argv, "--server-golden-url"), _arg_of(argv, "--golden"))
        res = apply_sync(db_path, golden)
        print(json.dumps(res.__dict__, ensure_ascii=False, indent=2))
        return 0 if res.status == "ok" else 1
    if "--update-check" in argv:
        from . import update
        try:
            release = update.check_for_update(timeout=float(_arg_of(argv, "--timeout") or 12))
            if release is None:
                print(json.dumps({"state": "idle", "current_version": update.current_version()},
                                 ensure_ascii=False))
                return 0
            print(json.dumps({"state": "available", "current_version": update.current_version(),
                              "release": release.as_dict()}, ensure_ascii=False))
            return 0
        except update.UpdateCheckError as exc:
            print(json.dumps({"state": "failed", "current_version": update.current_version(),
                              "error": str(exc)}, ensure_ascii=False))
            return 1
    if any(a in argv for a in
           ("--console-stats", "--console-clean", "--console-export", "--console-import")):
        from . import console  # 延迟导入，避免无谓加载
        return console.run_cli(argv)
    print(__doc__ or __file__)
    print("用法: launcher[.exe] --check [--server-golden-url U] [--golden F] [--auth U]")
    print("      launcher[.exe] --update-check [--timeout S]")
    print("      launcher[.exe] --export-golden [--data-dir D] [--out F]")
    print("      launcher[.exe] --sync [--server-golden-url U] [--golden F]")
    print("      launcher[.exe] --console-stats [--cats a,b]")
    print("      launcher[.exe] --console-clean --cats a,b")
    print("      launcher[.exe] --console-export --cats a,b [--out F]")
    print("      launcher[.exe] --console-import --archive F")
    return 0


def _arg_of(argv: list[str], flag: str) -> str:
    if flag not in argv:
        return ""
    idx = argv.index(flag)
    if idx + 1 < len(argv):
        return argv[idx + 1]
    return ""


if __name__ == "__main__":
    raise SystemExit(_cli())
