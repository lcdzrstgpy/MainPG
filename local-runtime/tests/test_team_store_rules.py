from __future__ import annotations

import sqlite3
from datetime import date

import pytest

from wh_local.modules.team_store.dedupe import (
    FLAG_SAME_ACCOUNT_DUP_ORDER,
    FLAG_SAME_STORE_MULTI_ACCOUNT,
    FLAG_LABELS,
    order_data_key,
    should_overwrite,
)
from wh_local.modules.team_store.rules import (
    MAX_STORE_SEQ_PER_DAY,
    StoreCodeAllocator,
    StoreNameError,
    StaffNoError,
    decide_store_name,
    format_store_code,
    normalize_store_name,
    normalize_staff_no,
    parse_store_code,
    validate_staff_no,
    validate_store_name,
)


def test_validate_store_name_accepts_normal_names() -> None:
    assert validate_store_name("Bluqevix") == "Bluqevix"
    assert validate_store_name("Bluqevix-US") == "Bluqevix-US"
    assert validate_store_name("北美一号店") == "北美一号店"
    assert validate_store_name("店铺-01") == "店铺-01"
    assert validate_store_name("ab_cd") == "ab_cd"
    assert validate_store_name("  美妆店  ") == "美妆店"
    # Temu 后台真实店名：英文 + 空格，长度远超 7 个汉字也要能登记
    assert validate_store_name("Little little Pony Shop") == "Little little Pony Shop"
    assert validate_store_name("Tom & Jerry's Store") == "Tom & Jerry's Store"
    # 连续空格压成一个，避免同名店铺出现两个键
    assert validate_store_name("Little  Pony   Shop") == "Little Pony Shop"


def test_validate_store_name_coerces_fullwidth() -> None:
    # 全角字母必须转成半角，否则会和 "ABC" 变成两条记录
    assert validate_store_name("ＡＢＣ") == "ABC"


@pytest.mark.parametrize(
    "name",
    [
        "",
        "   ",
        "一二三四五六七八九十一二三四五六七八九十一",  # 21 个汉字，宽度 42
        "a" * 41,  # 41 个半角字符
        "店铺#1",  # 含特殊符号
        "店铺😀",  # emoji
        "-店铺",
        "店铺_",
        "123456",  # 纯数字
    ],
)
def test_validate_store_name_rejects(name: str) -> None:
    with pytest.raises(StoreNameError):
        validate_store_name(name)


def test_validate_store_name_boundary_width() -> None:
    # 20 个汉字 = 40 个半角宽，正好到上限
    assert validate_store_name("一二三四五六七八九十一二三四五六七八九十") == (
        "一二三四五六七八九十一二三四五六七八九十"
    )
    assert validate_store_name("a" * 40) == "a" * 40
    # 中英混排按宽度累计：20 汉字 + 1 字母 = 41，超限
    with pytest.raises(StoreNameError):
        validate_store_name("一二三四五六七八九十一二三四五六七八九十a")


def test_normalize_store_name_is_case_and_width_insensitive() -> None:
    assert normalize_store_name(" ＡBC ") == "abc"
    assert normalize_store_name("BluQevix") == normalize_store_name("bluqevix")
    assert normalize_store_name("北美一号店") == "北美一号店"


def test_decide_store_name_no_conflict() -> None:
    decision = decide_store_name("新店", account_id="acc-1", registry=[])
    assert decision.conflict == ""
    assert decision.is_conflict is False
    assert decision.normalized == "新店"


def test_decide_store_name_same_account_conflict() -> None:
    registry = [{"name": "Bluqevix", "account_id": "acc-1", "store_code": "20261026-01"}]
    decision = decide_store_name(" bluqevix ", account_id="acc-1", registry=registry)
    assert decision.conflict == "same_account"
    assert decision.store_code == "20261026-01"


def test_decide_store_name_other_account_conflict() -> None:
    registry = [{"name": "Bluqevix", "account_id": "acc-1", "store_code": "20261026-01"}]
    decision = decide_store_name("Bluqevix", account_id="acc-2", registry=registry)
    assert decision.conflict == "other_account"
    assert decision.account_id == "acc-1"


def test_store_code_format_and_parse_roundtrip() -> None:
    code = format_store_code(date(2026, 10, 26), 1)
    assert code == "20261026-01"
    assert parse_store_code(code) == (date(2026, 10, 26), 1)
    assert format_store_code(date(2026, 10, 26), 99) == "20261026-99"


@pytest.mark.parametrize("code", ["", "20261026", "2026-10-26-01", "20261026-1", "abc"])
def test_parse_store_code_rejects_bad_input(code: str) -> None:
    with pytest.raises(ValueError):
        parse_store_code(code)


def test_store_code_sequence_out_of_range() -> None:
    with pytest.raises(ValueError):
        format_store_code(date(2026, 10, 26), 0)
    with pytest.raises(ValueError):
        format_store_code(date(2026, 10, 26), 100)


def test_allocator_increments_within_a_day() -> None:
    conn = sqlite3.connect(":memory:")
    allocator = StoreCodeAllocator(conn)
    day = date(2026, 10, 26)
    assert allocator.allocate(day) == "20261026-01"
    assert allocator.allocate(day) == "20261026-02"
    assert allocator.allocate(day) == "20261026-03"
    conn.close()


def test_allocator_keeps_days_independent() -> None:
    conn = sqlite3.connect(":memory:")
    allocator = StoreCodeAllocator(conn)
    assert allocator.allocate(date(2026, 10, 26)) == "20261026-01"
    assert allocator.allocate(date(2026, 10, 27)) == "20261027-01"
    assert allocator.allocate(date(2026, 10, 26)) == "20261026-02"
    conn.close()


def test_allocator_does_not_recycle_numbers() -> None:
    conn = sqlite3.connect(":memory:")
    allocator = StoreCodeAllocator(conn)
    day = date(2026, 10, 26)
    first = allocator.allocate(day)
    allocator.allocate(day)
    # 模拟第一家店被删除：号码不回退，新店继续往后发
    conn.execute("DELETE FROM store_code_sequences WHERE biz_date = ?", (day.isoformat(),))
    conn.execute(
        "INSERT INTO store_code_sequences (biz_date, next_seq) VALUES (?, 3)",
        (day.isoformat(),),
    )
    assert first == "20261026-01"
    assert allocator.allocate(day) == "20261026-03"
    conn.close()


def test_allocator_rejects_overflow_day() -> None:
    conn = sqlite3.connect(":memory:")
    allocator = StoreCodeAllocator(conn)
    day = date(2026, 10, 26)
    for _ in range(MAX_STORE_SEQ_PER_DAY):
        allocator.allocate(day)
    with pytest.raises(RuntimeError):
        allocator.allocate(day)
    conn.close()


def test_validate_staff_no() -> None:
    assert validate_staff_no("张三") == "张三"
    assert validate_staff_no("staff-01") == "staff-01"
    assert normalize_staff_no(" Staff-01 ") == "staff-01"
    for bad in ["", "a", "带 空格", "带#号"]:
        with pytest.raises(StaffNoError):
            validate_staff_no(bad)


def test_order_data_key_is_stable_and_normalized() -> None:
    first = order_data_key("PO-1", "SO-1", "SKU-1")
    assert first == order_data_key(" po-1 ", "so-1", "sku-1")
    assert first != order_data_key("PO-1", "SO-1", "SKU-2")
    assert len(first) == 40


def test_should_overwrite_prefers_newer_source_time() -> None:
    assert should_overwrite("2026-10-25T10:00:00+08:00", "2026-10-25T11:00:00+08:00")
    assert not should_overwrite("2026-10-25T11:00:00+08:00", "2026-10-25T10:00:00+08:00")
    assert not should_overwrite("2026-10-25T11:00:00+08:00", "2026-10-25T11:00:00+08:00")


def test_should_overwrite_handles_timezone_and_missing_values() -> None:
    # 03:00Z 等于 11:00+08:00，必须视为同一时刻而不是字符串更小
    assert not should_overwrite("2026-10-25T11:00:00+08:00", "2026-10-25T03:00:00Z")
    assert should_overwrite("2026-10-25T00:00:00+08:00", "2026-10-25T03:00:00Z")
    # 缺时间、乱码都不覆盖，避免空值冲掉好数据
    assert not should_overwrite("2026-10-25T11:00:00+08:00", "")
    assert not should_overwrite("2026-10-25T11:00:00+08:00", "not-a-time")
    assert should_overwrite("", "2026-10-25T11:00:00+08:00")


def test_flag_labels_cover_both_kinds() -> None:
    assert FLAG_LABELS[FLAG_SAME_STORE_MULTI_ACCOUNT]
    assert FLAG_LABELS[FLAG_SAME_ACCOUNT_DUP_ORDER]
