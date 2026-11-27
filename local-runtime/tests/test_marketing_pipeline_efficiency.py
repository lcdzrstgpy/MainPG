"""P1 效率优化回归：品类规则短路/缓存、Pro 熔断判定与任务级切换。"""

from __future__ import annotations

from types import SimpleNamespace

from wh_local.modules.product_processing import service as service_module
from wh_local.modules.product_processing.service import ProductProcessingService


def _service() -> ProductProcessingService:
    return object.__new__(ProductProcessingService)


def test_category_rule_high_confidence_skips_vision(monkeypatch) -> None:
    service = _service()
    calls = {"vision": 0}

    def fake_vision(**_kwargs):
        calls["vision"] += 1
        return "home_kitchen", "high"

    monkeypatch.setattr(service, "_marketing_vision_category", fake_vision)
    notes: list[str] = []
    chosen = service._marketing_category_id(
        title="专业省力钳子",
        category="工具与五金",
        reference_urls=["https://img.example/a.jpg"],
        ai_notes=notes,
    )
    assert chosen == "hand_tools"
    assert calls["vision"] == 0
    assert any(note.endswith(":rule-high") for note in notes)


def test_category_cache_reuses_vision_result_for_same_category(monkeypatch) -> None:
    service = _service()
    calls = {"vision": 0}

    def fake_vision(**_kwargs):
        calls["vision"] += 1
        return "home_kitchen", "high"

    monkeypatch.setattr(service, "_marketing_vision_category", fake_vision)
    first_notes: list[str] = []
    second_notes: list[str] = []
    first = service._marketing_category_id(
        title="多功能整理筐",
        category="家居与厨房",
        reference_urls=["https://img.example/a.jpg"],
        ai_notes=first_notes,
    )
    second = service._marketing_category_id(
        title="可折叠整理筐",
        category="家居与厨房",
        reference_urls=["https://img.example/b.jpg"],
        ai_notes=second_notes,
    )
    assert first == second == "home_kitchen"
    assert calls["vision"] == 1
    assert any(note.endswith(":cache") for note in second_notes)


def test_pro_unavailable_marker_detection() -> None:
    assert service_module._pro_unavailable_error(RuntimeError("model not granted for this key"))
    assert service_module._pro_unavailable_error(RuntimeError("unsupported model: seedream-pro"))
    # 内容审核类不能熔断：换一张候选通常就过了
    assert not service_module._pro_unavailable_error(RuntimeError("content policy violation"))


def test_standard_tier_uses_flash_directly() -> None:
    from wh_local.modules.product_processing.provider_config import (
        MARKETING_IMAGE_MODEL_FALLBACK,
    )

    service = _service()
    seen_models: list[str] = []

    class FakeProcessor:
        def generate(self, **kwargs):
            seen_models.append(str(kwargs.get("model_override") or ""))
            return SimpleNamespace(content=b"image", model=kwargs.get("model_override"))

    notes: list[str] = []
    result = service._marketing_render(
        FakeProcessor(), "prompt", ["ref"], notes, {"enabled": True}, tier="standard"
    )

    assert result.content == b"image"
    # 标准档不尝试 Pro，直接 Flash，也不触发熔断逻辑。
    assert seen_models == [MARKETING_IMAGE_MODEL_FALLBACK]
    assert any(note.endswith(MARKETING_IMAGE_MODEL_FALLBACK) for note in notes)


def test_pro_circuit_opens_after_first_unavailable_error() -> None:
    from wh_local.modules.product_processing.provider_config import (
        MARKETING_IMAGE_MODEL_FALLBACK,
        MARKETING_IMAGE_MODEL_PRO,
    )

    service = _service()
    seen_models: list[str] = []
    class FakeProcessor:
        def generate(self, **kwargs):
            seen_models.append(str(kwargs.get("model_override") or "default"))
            if kwargs.get("model_override") == MARKETING_IMAGE_MODEL_PRO:
                raise RuntimeError("model not granted for this batch")
            return SimpleNamespace(content=b"default-image")

    processor = FakeProcessor()
    state = {"enabled": True}
    notes: list[str] = []

    first = service._marketing_render(
        processor, "prompt", ["ref"], notes, state
    )
    assert first.content == b"default-image"
    assert state["enabled"] is False
    assert any(note == "marketing:pro-circuit-open" for note in notes)

    second = service._marketing_render(processor, "prompt", ["ref"], notes, state)
    assert second.content == b"default-image"
    # 第一次：Pro 失败 → 显式回落 Flash；第二次：熔断后直接 Flash。
    assert seen_models == [
        MARKETING_IMAGE_MODEL_PRO,
        MARKETING_IMAGE_MODEL_FALLBACK,
        MARKETING_IMAGE_MODEL_FALLBACK,
    ]
