"""新手引导配置的持久化。

引导本身完全在前端渲染（driver.js + CSS 选择器），服务端只负责把
「哪个板块下有哪些子任务、每个子任务有哪些步骤」这份数据存进通用 KV 表
``workbench_settings``。这样运营改引导只是改数据，不需要重新打包前端。

一级板块（产品处理 / POD定制 / 核价及货源）与工作台导航结构绑定，写死在
前端常量里；这里只存板块下的子任务与步骤。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ...db import connect, transaction

CONFIG_KEY = "guide_config"

MAX_BOARDS = 12
MAX_SUBTASKS_PER_BOARD = 40
MAX_STEPS_PER_SUBTASK = 40
MAX_SELECTORS_PER_STEP = 5
# 选择器 + 文案都是短文本，整份配置正常在几 KB；这里给一个宽松但有限的上限，
# 避免异常请求把整张配置表撑爆。
MAX_PAYLOAD_BYTES = 512 * 1024

SIDES = ("top", "bottom", "left", "right")
ALIGNS = ("start", "center", "end")
# 步骤的预设值处理方式：require 要用户填对才能下一步，auto 由引导自动填入。
PRESET_MODES = ("require", "auto")
# 步骤的放行方式：click 要用户点了高亮区域、file 要用户选到文件，两者都在满足后自动前进；
# manual（用户自己点「下一步」）是缺省值，不落库。
ADVANCE_MODES = ("manual", "click", "file")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _text(value: Any, limit: int) -> str:
    if not isinstance(value, str):
        return ""
    return value.strip()[:limit]


def _choice(value: Any, allowed: tuple[str, ...], default: str) -> str:
    text = _text(value, 16).lower()
    return text if text in allowed else default


def _selectors(raw: Any, task_id: str, position: int) -> list[str]:
    # 一个步骤可以带多个候选选择器：面板常驻挂载时同名节点可能有多份，
    # 前端按顺序取第一个「真实可见」的，命中失败还能往后回退。
    items = raw if isinstance(raw, list) else [raw]
    selectors: list[str] = []
    for item in items:
        text = _text(item, 240)
        if text and text not in selectors:
            selectors.append(text)
    if not selectors:
        raise ValueError(f"子任务「{task_id}」的第 {position} 步还没有指定高亮位置")
    return selectors[:MAX_SELECTORS_PER_STEP]


def _step(raw: Any, task_id: str, index: int) -> dict[str, Any]:
    position = index + 1
    if not isinstance(raw, dict):
        raise ValueError(f"子任务「{task_id}」的第 {position} 步结构不正确")
    step: dict[str, Any] = {
        "selectors": _selectors(raw.get("selectors"), task_id, position),
        "page": _text(raw.get("page"), 64),
        "title": _text(raw.get("title"), 60),
        "description": _text(raw.get("description"), 300),
        "side": _choice(raw.get("side"), SIDES, "bottom"),
        "align": _choice(raw.get("align"), ALIGNS, "center"),
    }
    # 「需要用户先填写/操作」的步骤：前端播放时放开蒙版拦截。
    # 缺省为只读讲解，因此只在为真时落库，老配置不用补字段。
    if raw.get("interactive") is True:
        step["interactive"] = True
    # 预设值：只在设了处理方式时落库；设了方式却没内容的话前端播放时会失去拦截目标，
    # 这种配置直接报错，而不是存成一条静默失效的「空门槛」。
    mode = _choice(raw.get("presetMode"), PRESET_MODES, "")
    if mode:
        value = _text(raw.get("presetValue"), 120)
        if not value:
            raise ValueError(f"子任务「{task_id}」的第 {position} 步设了预设值但没填内容")
        step["presetMode"] = mode
        step["presetValue"] = value
    # 下拉框这类「不点开就展不开」的控件：进入该步时由引导自动点一下。
    if raw.get("autoOpen") is True:
        step["autoOpen"] = True
    # 放行方式：click / file 会在用户完成对应动作后自动前进。缺省 manual，因此只在非
    # manual 时落库；写错的值按缺省处理，不落一条前端不认识的配置。
    advance_on = _choice(raw.get("advanceOn"), ADVANCE_MODES, "")
    if advance_on and advance_on != "manual":
        step["advanceOn"] = advance_on
    return step


def _sub_tasks(raw: Any, board_id: str) -> list[dict[str, Any]]:
    if not isinstance(raw, list):
        raise ValueError(f"板块「{board_id}」的子任务必须是数组")
    if len(raw) > MAX_SUBTASKS_PER_BOARD:
        raise ValueError(f"板块「{board_id}」的子任务数量超出上限")

    tasks: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in raw:
        if not isinstance(item, dict):
            raise ValueError(f"板块「{board_id}」里有结构不正确的子任务")
        task_id = _text(item.get("id"), 64)
        if not task_id:
            raise ValueError(f"板块「{board_id}」里有子任务缺少 id")
        if task_id in seen:
            raise ValueError(f"板块「{board_id}」的子任务 id 重复：{task_id}")
        seen.add(task_id)
        label = _text(item.get("label"), 40)
        if not label:
            raise ValueError(f"子任务「{task_id}」缺少名称")

        raw_steps = item.get("steps")
        if raw_steps is None:
            raw_steps = []
        if not isinstance(raw_steps, list):
            raise ValueError(f"子任务「{task_id}」的步骤必须是数组")
        if len(raw_steps) > MAX_STEPS_PER_SUBTASK:
            raise ValueError(f"子任务「{task_id}」的步骤数量超出上限")

        tasks.append(
            {
                "id": task_id,
                "label": label,
                "steps": [_step(step, task_id, index) for index, step in enumerate(raw_steps)],
            }
        )
    return tasks


def normalize_config(config: Any) -> dict[str, Any]:
    """校验并规整前端提交的引导配置，非法结构直接报错而不是静默丢弃。"""
    if not isinstance(config, dict):
        raise ValueError("引导配置必须是一个对象")
    boards_in = config.get("boards")
    if not isinstance(boards_in, dict):
        raise ValueError("引导配置缺少 boards")
    if len(boards_in) > MAX_BOARDS:
        raise ValueError("板块数量超出上限")

    boards: dict[str, Any] = {}
    for board_id, board in boards_in.items():
        board_key = _text(board_id, 64)
        if not board_key:
            raise ValueError("板块 id 不能为空")
        if not isinstance(board, dict):
            raise ValueError(f"板块「{board_key}」结构不正确")
        boards[board_key] = {"subTasks": _sub_tasks(board.get("subTasks"), board_key)}
    return {"version": 1, "boards": boards}


@dataclass
class GuideConfigService:
    database_path: Path

    def load(self) -> dict[str, Any]:
        """读取已保存的引导配置；从未保存过时 config 为 None，由前端回退内置默认值。"""
        conn = connect(self.database_path)
        try:
            row = conn.execute(
                "SELECT value_json, updated_by, updated_at FROM workbench_settings WHERE key = ?",
                (CONFIG_KEY,),
            ).fetchone()
        finally:
            conn.close()

        if row is None:
            return {"ok": True, "config": None, "updated_by": "", "updated_at": ""}

        try:
            config = json.loads(row["value_json"])
        except (TypeError, ValueError):
            # 存量数据损坏时按「未保存过」处理，前端回退默认值，不至于整个引导打不开。
            config = None
        return {
            "ok": True,
            "config": config,
            "updated_by": row["updated_by"] or "",
            "updated_at": row["updated_at"] or "",
        }

    def save(self, config: Any, actor_id: str) -> dict[str, Any]:
        normalized = normalize_config(config)
        payload = json.dumps(normalized, ensure_ascii=False, separators=(",", ":"))
        if len(payload.encode("utf-8")) > MAX_PAYLOAD_BYTES:
            raise ValueError("引导配置过大，请拆分后再保存")

        now = utc_now()
        with transaction(self.database_path) as conn:
            conn.execute(
                """
                INSERT INTO workbench_settings(key, value_json, updated_by, updated_at)
                VALUES(?, ?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET
                    value_json = excluded.value_json,
                    updated_by = excluded.updated_by,
                    updated_at = excluded.updated_at
                """,
                (CONFIG_KEY, payload, actor_id, now),
            )
        return {"ok": True, "config": normalized, "updated_by": actor_id, "updated_at": now}
