"""智能前置层：模糊输入 → 结构化业务字段（brief_runtime）的契约与运行测试。

全程使用假 provider 密钥与 mock HTTP，不触发任何真实付费调用。
"""

from __future__ import annotations

import json

import pytest

from wh_local.modules.pod_customization.billing_contract import (
    BRIEF_ATTEMPTS,
    PodBillingAuthorizationRequired,
    PodCallPlan,
    PodExecutionGrant,
)
from wh_local.modules.pod_customization.brief_runtime import (
    PROMPT_VERSION,
    PodBriefRequest,
    PodBriefRuntime,
    _SAFETY_EXCLUDED_BASELINE,
    is_concrete_style_element,
    validate_brief_fields,
)
from wh_local.modules.product_processing.doubao_ark import DoubaoArkError


# 用户实际遇到的反例：形容词 / 风格词 / 配色属性 / 表现手法 / 场景类别，都不是「元素」。
ABSTRACT_STYLE_KEYWORDS = (
    "波普色块拼接", "高饱和度配色", "美式乡村风", "复古元素", "大胆线条",
    "撞色设计", "俏皮图案", "复古做旧质感", "美式怀旧符号", "波普艺术线条",
    "高对比色彩", "休闲度假元素", "健身场景元素", "海滩元素", "出游元素",
)

# 正例：具体、可绘制的事物。
CONCRETE_STYLE_KEYWORDS = (
    "红白波普条纹", "奶昔杯", "汉堡", "薯条", "甜甜圈", "冰淇淋", "爆米花", "热狗",
    "汽水罐", "弹珠机", "点唱机", "轮滑鞋", "老式轿车", "汽车影院幕布", "停车标志牌",
    "霓虹灯牌", "复古收音机", "卡通美式人物", "餐厅桌布花纹", "吧台高脚椅", "留声机",
    "投币电话", "汽车牌照", "汽水吸管", "奶昔搅拌器", "爆米花桶", "热狗面包",
    "冰淇淋甜筒", "甜甜圈糖霜", "复古海报字体", "汽车影院指示牌", "露天影院座椅",
    "复古餐具", "卡通动物形象", "复古轿车", "风车", "老式冰箱", "餐盘",
    "老式加油机", "点唱机唱片", "复古餐具托盘", "苏打水杯",
)


def _grant() -> PodExecutionGrant:
    return PodExecutionGrant("freeze-brief", 1, "2099-01-01T00:00:00Z", {"ark": "ark-key"})


def _fields(keyword_count: int = 45) -> dict[str, object]:
    return {
        "product_name": "复古印花托特包",
        "product_category": "女士手提包",
        "target_market": "美国",
        "target_audience": "25-40 岁通勤女性",
        "core_selling_points": ["耐磨帆布", "大容量"],
        "design_theme": "美式西南复古牛仔荒野风",
        "style_keywords": [f"具体物件{i}" for i in range(keyword_count)],
        "color_preferences": [f"配色{i}" for i in range(12)],
        "excluded_elements": [f"本主题禁用项{i}" for i in range(12)],
    }


def _call_ids(brief_id: str) -> tuple[str, ...]:
    return tuple(f"{brief_id}:brief:{attempt}" for attempt in range(1, BRIEF_ATTEMPTS + 1))


class _Response:
    def __init__(self, payload: dict[str, object], *, status_code: int = 200) -> None:
        body = json.dumps({"choices": [{"message": {"content": json.dumps(payload, ensure_ascii=False)}}]})
        self.content = body.encode("utf-8")
        self.status_code = status_code

    def close(self) -> None:
        return None


class _Session:
    def __init__(self, responses: list[_Response]) -> None:
        self.responses = list(responses)
        self.requests: list[dict[str, object]] = []

    def post(self, url: str, *, headers, json, timeout, allow_redirects):  # noqa: A002 - 与 requests 签名一致
        self.requests.append({"url": url, "headers": headers, "json": json})
        return self.responses.pop(0)


def test_brief_plan_freezes_title_only_scope_so_the_action_is_free() -> None:
    """免费的关键：只冻结 pod.title，服务端对 POD 画像的纯 title scope 零计费。"""
    plan = PodCallPlan.for_brief("brf1")

    assert plan.idempotency_key == "pod:brief:brf1"
    assert len(plan.calls) == BRIEF_ATTEMPTS
    assert {call.feature for call in plan.calls} == {"pod.title"}

    payload = plan.product_batch_freeze_payload()
    assert payload["scope"] == ["title"]
    assert payload["link_count"] == 1
    assert payload["billing_profile"] == "pod_random_v1"


def test_validate_brief_fields_requires_at_least_forty_theme_keywords() -> None:
    with pytest.raises(ValueError, match="至少需要 40"):
        validate_brief_fields({**_fields(), "style_keywords": ["仙人掌", "太阳纹"]})

    assert len(validate_brief_fields(_fields(40)).style_keywords) == 40


def test_is_concrete_style_element_separates_objects_from_abstract_words() -> None:
    assert all(is_concrete_style_element(word) for word in CONCRETE_STYLE_KEYWORDS)
    assert not any(is_concrete_style_element(word) for word in ABSTRACT_STYLE_KEYWORDS)


def test_validate_brief_fields_drops_abstract_style_elements() -> None:
    """形容词/风格词/配色词/手法词/场景类别要被剔除，只留可绘制的具体事物。"""
    result = validate_brief_fields(
        {**_fields(), "style_keywords": [*CONCRETE_STYLE_KEYWORDS, *ABSTRACT_STYLE_KEYWORDS]}
    )

    assert result.style_keywords == list(CONCRETE_STYLE_KEYWORDS)
    assert not any(word in result.style_keywords for word in ABSTRACT_STYLE_KEYWORDS)


def test_validate_brief_fields_reports_rejected_abstract_elements_in_feedback() -> None:
    """剔除后不足 40 个时，把被剔除的具体条目回传给模型，触发契约修复重试。"""
    with pytest.raises(ValueError, match="不是具体事物已被剔除"):
        validate_brief_fields(
            {
                **_fields(),
                "style_keywords": [*CONCRETE_STYLE_KEYWORDS[:20], *ABSTRACT_STYLE_KEYWORDS],
            }
        )


def test_validate_brief_fields_requires_minimum_colors_and_exclusions() -> None:
    with pytest.raises(ValueError, match="color_preferences 至少需要 10"):
        validate_brief_fields({**_fields(), "color_preferences": ["土橘色", "牛仔蓝"]})

    with pytest.raises(ValueError, match="excluded_elements 至少需要 10"):
        validate_brief_fields({**_fields(), "excluded_elements": ["现代玻璃幕墙"]})


def test_validate_brief_fields_merges_safety_baseline_without_duplicates() -> None:
    """侵权/危险类屏蔽不依赖模型自觉：模型没写也要补齐，写了也不重复。"""
    model_items = [*_SAFETY_EXCLUDED_BASELINE[:5], *(f"本主题禁用项{i}" for i in range(10))]
    fields = validate_brief_fields({**_fields(), "excluded_elements": model_items})

    for item in _SAFETY_EXCLUDED_BASELINE:
        assert item in fields.excluded_elements
    # 主题相关的禁用项保留，安全基线与模型输出合并后不重复。
    assert "本主题禁用项0" in fields.excluded_elements
    assert len(fields.excluded_elements) == len(set(fields.excluded_elements))
    assert len(fields.excluded_elements) == 10 + len(_SAFETY_EXCLUDED_BASELINE)


def test_validate_brief_fields_dedupes_and_requires_required_scalars() -> None:
    duplicated = {**_fields(), "style_keywords": ["仙人掌"] * 45}
    with pytest.raises(ValueError, match="至少需要 40"):
        validate_brief_fields(duplicated)

    with pytest.raises(ValueError, match="不能为空"):
        validate_brief_fields({**_fields(), "product_category": "   "})


def test_validate_brief_fields_rejects_style_planning_from_the_provider() -> None:
    """样式规划由用户在页面上二选一；provider 若擅自输出该字段，契约即判不合格。"""
    with pytest.raises(ValueError, match="failed validation"):
        validate_brief_fields({**_fields(), "style_planning": "全覆盖"})


def test_generate_brief_fields_sends_theme_keyword_recipe_and_untrusted_notice() -> None:
    session = _Session([_Response(_fields())])
    runtime = PodBriefRuntime(session=session, sleeper=lambda _seconds: None)
    outcomes: list[tuple[str, str]] = []

    result = runtime.generate_brief_fields(
        PodBriefRequest(brief_id="brf1", brief="美式西南复古牛仔荒野风托特包"),
        grant=_grant(),
        call_id="brf1:brief:1",
        call_ids=_call_ids("brf1"),
        on_outcome=lambda call_id, status: outcomes.append((call_id, status)),
    )

    assert result.prompt_version == PROMPT_VERSION
    assert len(result.fields.style_keywords) == 45
    # 样式规划不代填：固定返回空串，交给用户在页面上二选一。
    assert result.fields.style_planning == ""
    assert outcomes == [("brf1:brief:1", "success")]

    request = session.requests[0]
    schema = request["json"]["response_format"]["json_schema"]["schema"]
    assert schema["properties"]["style_keywords"]["minItems"] == 40
    # 模糊输入必须被当作不可信数据包裹。
    assert "untrusted" in str(request["json"]["messages"][0]["content"]).lower()
    user_prompt = str(request["json"]["messages"][1]["content"])
    assert "style_keywords" in user_prompt
    assert "40" in user_prompt


def test_generate_brief_fields_repairs_an_invalid_contract_then_succeeds() -> None:
    session = _Session([_Response(_fields(2)), _Response(_fields(45))])
    runtime = PodBriefRuntime(session=session, sleeper=lambda _seconds: None)
    outcomes: list[tuple[str, str]] = []

    result = runtime.generate_brief_fields(
        PodBriefRequest(brief_id="brf2", brief="主题"),
        grant=_grant(),
        call_id="brf2:brief:1",
        call_ids=_call_ids("brf2"),
        on_outcome=lambda call_id, status: outcomes.append((call_id, status)),
    )

    assert result.attempt_count == 2
    assert len(session.requests) == 2
    # 契约不合规的响应仍然来自 provider，因此按 success 记账（不计退款）。
    assert outcomes == [("brf2:brief:1", "success"), ("brf2:brief:2", "success")]


def test_generate_brief_fields_fails_after_exhausting_frozen_calls() -> None:
    session = _Session([_Response(_fields(2)) for _ in range(BRIEF_ATTEMPTS)])
    runtime = PodBriefRuntime(session=session, sleeper=lambda _seconds: None)

    with pytest.raises(DoubaoArkError):
        runtime.generate_brief_fields(
            PodBriefRequest(brief_id="brf3", brief="主题"),
            grant=_grant(),
            call_id="brf3:brief:1",
            call_ids=_call_ids("brf3"),
        )

    assert len(session.requests) == BRIEF_ATTEMPTS


def test_generate_brief_fields_requires_an_ark_grant() -> None:
    runtime = PodBriefRuntime(session=_Session([]), sleeper=lambda _seconds: None)

    with pytest.raises(PodBillingAuthorizationRequired):
        runtime.generate_brief_fields(
            PodBriefRequest(brief_id="brf4", brief="主题"),
            grant=PodExecutionGrant("freeze", 1, "2099-01-01T00:00:00Z", {}),
            call_id="brf4:brief:1",
        )
