"""店铺命名、店铺码与员工标识的规则。

三条硬约束：
1. 店名最多 20 个汉字宽（即 40 个半角字符），允许中文、英文字母、数字、空格、``-``、``_``
   以及 ``. & ' ( )``——Temu 后台真实的店铺名经常带空格和英文，按真实名字登记；
2. 店名全局唯一（本地按账号唯一，服务器按全局唯一），比较前先规范化；
3. 店铺码由服务器按「日期 + 当天创建顺序」发号，客户端只读。

规则放在这里集中实现，本地建店和远端登记都调用同一份代码，
避免出现「本地建得出来、服务器拒绝」的两套口径。
"""

from __future__ import annotations

import re
import sqlite3
import unicodedata
from dataclasses import dataclass
from datetime import date
from typing import Final, Iterable, Mapping

# 店名长度按显示宽度算：1 个汉字 = 2，1 个英文/数字 = 1，
# 也就是「最多 20 个汉字」，纯英文时上限是 40 个字符。
MAX_STORE_NAME_WIDTH: Final = 40
MAX_STORE_NAME_CJK: Final = 20
MAX_STORE_SEQ_PER_DAY: Final = 99
MIN_STAFF_NO_LENGTH: Final = 2
MAX_STAFF_NO_LENGTH: Final = 20

_NAME_PATTERN: Final = re.compile(r"^[0-9A-Za-z\u4e00-\u9fff _\-.&'()（）]+$")
# 员工标识不允许空格，便于和店铺名区分。
_STAFF_PATTERN: Final = re.compile(r"^[0-9A-Za-z\u4e00-\u9fff_-]+$")
_STORE_CODE_PATTERN: Final = re.compile(r"^(\d{4})(\d{2})(\d{2})-(\d{2})$")


class StoreNameError(ValueError):
    """店铺名不符合命名规范。"""


class StaffNoError(ValueError):
    """员工标识不符合规范。"""


def _to_halfwidth(text: str) -> str:
    """全角字母/数字/空格转半角，避免「ＡＢＣ」和「ABC」被当成两个店名。"""
    chars: list[str] = []
    for char in text:
        code = ord(char)
        if code == 0x3000:
            chars.append(" ")
        elif 0xFF01 <= code <= 0xFF5E:
            chars.append(chr(code - 0xFEE0))
        else:
            chars.append(char)
    return "".join(chars)


def _char_width(char: str) -> int:
    """单个字符的显示宽度：全角/宽字符记 2，其余记 1。"""
    return 2 if unicodedata.east_asian_width(char) in ("W", "F") else 1


def store_name_width(name: object) -> int:
    """店名的显示宽度，汉字按 2 计。"""
    return sum(_char_width(char) for char in str(name or ""))


def _compact_spaces(text: str) -> str:
    """把连续空白压成一个空格，并去掉首尾空白。"""
    return re.sub(r"\s+", " ", text).strip()


def validate_store_name(name: object) -> str:
    """校验店名，返回半角化之后的店名；不合规抛 :class:`StoreNameError`。"""
    text = _compact_spaces(_to_halfwidth(str(name or "")))
    if not text:
        raise StoreNameError("店铺名不能为空")
    width = store_name_width(text)
    if width > MAX_STORE_NAME_WIDTH:
        raise StoreNameError(
            f"店铺名最多 {MAX_STORE_NAME_CJK} 个汉字（{MAX_STORE_NAME_WIDTH} 个半角字符），"
            f"当前 {len(text)} 个字符、宽度 {width}"
        )
    if not _NAME_PATTERN.match(text):
        raise StoreNameError("店铺名只能用中文、英文、数字、空格，以及 - _ . & ' ( )")
    if text[0] in "-_" or text[-1] in "-_":
        raise StoreNameError("店铺名不能以 - 或 _ 开头或结尾")
    if text.isdigit():
        raise StoreNameError("店铺名不能是纯数字，容易和店铺 ID 混淆")
    return text


def normalize_store_name(name: object) -> str:
    """唯一性比较用的键：半角、压空格、英文统一小写。"""
    return _compact_spaces(_to_halfwidth(str(name or ""))).lower()


def validate_staff_no(value: object) -> str:
    """校验员工标识（首次建店时填写，用于辨识和后续提交）。"""
    text = _to_halfwidth(str(value or "")).strip()
    if not text:
        raise StaffNoError("员工标识不能为空")
    if not MIN_STAFF_NO_LENGTH <= len(text) <= MAX_STAFF_NO_LENGTH:
        raise StaffNoError(
            f"员工标识需要 {MIN_STAFF_NO_LENGTH}-{MAX_STAFF_NO_LENGTH} 个字符"
        )
    if not _STAFF_PATTERN.match(text):
        raise StaffNoError("员工标识只能用中文、英文、数字，以及 - 和 _")
    return text


def normalize_staff_no(value: object) -> str:
    """员工标识唯一性比较用的键。"""
    return _to_halfwidth(str(value or "")).strip().lower()


@dataclass(frozen=True)
class StoreNameDecision:
    """店名占用判定结果。

    ``conflict`` 为空表示可以直接创建；
    ``same_account`` 表示自己账号下已有同名店；
    ``other_account`` 表示别的账号已占用（仍然允许录入，但要打标记）。
    """

    normalized: str
    conflict: str = ""
    store_code: str = ""
    account_id: str = ""

    @property
    def is_conflict(self) -> bool:
        return bool(self.conflict)


def decide_store_name(
    name: object,
    *,
    account_id: str,
    registry: Iterable[Mapping[str, object]] = (),
) -> StoreNameDecision:
    """判断店名是否已被占用。

    ``registry`` 每项包含 ``name`` / ``account_id`` / ``store_code`` 三个字段，
    既可以是本地的店铺列表，也可以是远端的全局注册表，判定逻辑保持一致。
    """
    normalized = normalize_store_name(name)
    owner = str(account_id or "")
    for row in registry:
        if normalize_store_name(row.get("name")) != normalized:
            continue
        row_account = str(row.get("account_id") or "")
        return StoreNameDecision(
            normalized=normalized,
            conflict="same_account" if row_account == owner else "other_account",
            store_code=str(row.get("store_code") or ""),
            account_id=row_account,
        )
    return StoreNameDecision(normalized=normalized)


def format_store_code(biz_date: date, seq: int) -> str:
    """把「日期 + 顺序」拼成店铺码，例如 2026-10-26 的第 3 家店是 20261026-03。"""
    order = int(seq)
    if not 1 <= order <= MAX_STORE_SEQ_PER_DAY:
        raise ValueError(
            f"同一天的创建顺序必须在 1-{MAX_STORE_SEQ_PER_DAY} 之间，收到 {seq}"
        )
    return f"{biz_date:%Y%m%d}-{order:02d}"


def parse_store_code(code: object) -> tuple[date, int]:
    """解析店铺码，返回（创建日期，当天顺序）。"""
    match = _STORE_CODE_PATTERN.match(str(code or "").strip())
    if not match:
        raise ValueError(f"店铺 ID 格式不正确：{code}（应为 YYYYMMDD-NN）")
    year, month, day, seq = (int(part) for part in match.groups())
    return date(year, month, day), seq


class StoreCodeAllocator:
    """按「日期 + 当天创建顺序」发号。

    号码只增不减，店铺删除后不回收；同一天超过 99 家直接报错，
    由服务器在同一个写事务里调用，保证并发下也不会重号。
    """

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS store_code_sequences ("
            "biz_date TEXT PRIMARY KEY, "
            "next_seq INTEGER NOT NULL DEFAULT 1)"
        )

    def allocate(self, biz_date: date | None = None) -> str:
        day = biz_date or date.today()
        key = day.isoformat()
        self._conn.execute(
            "INSERT INTO store_code_sequences (biz_date, next_seq) VALUES (?, 1) "
            "ON CONFLICT(biz_date) DO NOTHING",
            (key,),
        )
        row = self._conn.execute(
            "SELECT next_seq FROM store_code_sequences WHERE biz_date = ?",
            (key,),
        ).fetchone()
        seq = int(row[0]) if row else 1
        if seq > MAX_STORE_SEQ_PER_DAY:
            raise RuntimeError(
                f"{key} 当天的店铺编号已用尽（上限 {MAX_STORE_SEQ_PER_DAY}）"
            )
        self._conn.execute(
            "UPDATE store_code_sequences SET next_seq = ? WHERE biz_date = ?",
            (seq + 1, key),
        )
        return format_store_code(day, seq)
