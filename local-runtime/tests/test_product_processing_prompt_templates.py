# -*- coding: utf-8 -*-
"""预设提示词模板（追加指令模式 + 多命名模板）功能测试。"""

from pathlib import Path

import pytest

from wh_local.modules.product_processing.infrastructure.assets import ProductProcessingAssets
from wh_local.modules.product_processing.infrastructure.database import create_database
from wh_local.modules.product_processing.infrastructure.repository import ProductProcessingRepository
from wh_local.modules.product_processing.service import ProductProcessingService


@pytest.fixture()
def service(tmp_path: Path) -> ProductProcessingService:
    db = create_database("sqlite:///:memory:")
    svc = ProductProcessingService(
        ProductProcessingRepository(db),
        ProductProcessingAssets(tmp_path / "assets"),
    )
    yield svc
    db.dispose()


def test_prompt_template_crud_and_activation(service: ProductProcessingService) -> None:
    saved = service.save_prompt_template(
        {"name": "夏季清爽风", "prompts": {"title": "强调清爽透气", "grid_image": "宫内使用浅色场景"}}
    )
    assert saved["template"]["name"] == "夏季清爽风"
    assert saved["template"]["is_active"] is True

    templates = service.prompt_templates()["templates"]
    assert len(templates) == 1
    first_id = templates[0]["id"]

    # 保存第二个模板时自动激活新模板，第一个取消激活
    service.save_prompt_template({"name": "高端质感款", "prompts": {"desc": "强调材质质感"}})
    second_list = service.prompt_templates()["templates"]
    assert len(second_list) == 2
    assert sum(1 for t in second_list if t["is_active"]) == 1
    assert next(t for t in second_list if t["is_active"])["name"] == "高端质感款"

    # 重新激活第一个模板
    service.activate_prompt_template(first_id)
    active = service.repository.active_prompt_template()
    assert active is not None
    assert active["id"] == first_id
    assert service.repository.active_prompt_template()["is_active"] is True

    # 更新模板内容（追加指令），激活状态保持
    service.save_prompt_template(
        {
            "template_id": first_id,
            "name": "夏季清爽风",
            "prompts": {"title": "强调清爽透气（v2）", "grid_image": ""},
        }
    )
    updated = service.repository.active_prompt_template()
    assert updated["prompts"]["title"] == "强调清爽透气（v2）"
    assert updated["prompts"]["grid_image"] == ""

    # 删除后列表为空，且不再有激活模板
    service.delete_prompt_template(first_id)
    remaining = service.prompt_templates()["templates"]
    assert len(remaining) == 1
    assert all(t["id"] != first_id for t in remaining)

    # 不支持的 key 拒绝保存
    with pytest.raises(ValueError):
        service.save_prompt_template({"name": "bad", "prompts": {"combined_text": "x"}})


def test_image_additions_append_with_fixed_contract(service: ProductProcessingService) -> None:
    service.save_prompt_template({"name": "t", "prompts": {"grid_image": "宫内放蓝色道具"}})

    base = "SYSTEM FIXED: output one exact 2x2 four-panel grid with clean dividers."
    out = service._apply_user_image_additions(base, "grid_image")
    assert out.startswith(base)
    assert "USER-REQUESTED PANEL PLANNING ADDITIONS" in out
    assert "MUST NOT override the fixed runtime contracts above" in out
    assert "宫内放蓝色道具" in out

    # 未填写的板块不附加
    out2 = service._apply_user_image_additions(base, "premium_image")
    assert out2 == base


def test_active_template_prompts_only_exposed_keys(service: ProductProcessingService) -> None:
    service.save_prompt_template({"name": "t", "prompts": {"title": "标题要求", "grid_image_b": "模特场景"}})
    prompts = service._active_template_prompts()
    assert prompts["title"] == "标题要求"
    assert prompts["grid_image_b"] == "模特场景"
    assert prompts["desc"] == ""
    assert prompts["variant_values"] == ""
    # 内部 key（combined_text / image_repair 等）不属于用户板块，不会被附加
    assert "combined_text" not in prompts
    assert "image_repair_grid" not in prompts


def test_chinese_prompt_addition_is_translated_cached_and_falls_back(monkeypatch) -> None:
    """中文附加词先调豆包翻译成目标语言；翻译失败/结果仍含中文时回退空串。"""
    from wh_local.modules.product_processing.doubao_ark import DoubaoArkClient

    svc = object.__new__(ProductProcessingService)
    calls: list[str] = []

    # 测试环境无批次冻结 token，__init__ 会抛配置错误；mock 掉只留 complete。
    monkeypatch.setattr(DoubaoArkClient, "__init__", lambda self: None)

    def fake_complete(self, messages):
        calls.append(str(messages))
        return "Include holiday gift keywords such as Valentine's Day gift."

    monkeypatch.setattr(DoubaoArkClient, "complete", fake_complete)

    out1 = svc._translate_prompt_addition("必须加入节日促销关键词，如七夕节送礼必备", "en")
    assert out1 == "Include holiday gift keywords such as Valentine's Day gift."
    assert len(calls) == 1
    # 同一原文+目标语言命中缓存，不再调用上游
    out2 = svc._translate_prompt_addition("必须加入节日促销关键词，如七夕节送礼必备", "en")
    assert out2 == out1
    assert len(calls) == 1

    # 翻译结果仍含中文 → 不采纳（回退，避免把中文带进生成提示词）
    monkeypatch.setattr(
        DoubaoArkClient, "complete", lambda self, messages: "Include 中文关键词 gift"
    )
    assert svc._translate_prompt_addition("另一段中文要求", "en") == ""

    # 上游异常 → 回退
    def boom(self, messages):
        raise RuntimeError("upstream down")

    monkeypatch.setattr(DoubaoArkClient, "complete", boom)
    assert svc._translate_prompt_addition("再一段中文要求", "en") == ""

    # 西语目标语言用 Spanish 指令
    def spanish_complete(self, messages):
        calls.append(str(messages))
        return "Incluya palabras clave de regalo de festividad."

    monkeypatch.setattr(DoubaoArkClient, "complete", spanish_complete)
    out_es = svc._translate_prompt_addition("加入节日送礼关键词", "es")
    assert out_es == "Incluya palabras clave de regalo de festividad."
    assert "Spanish" in calls[-1]

