from __future__ import annotations

import json
from threading import Event

import pytest

from wh_local.modules.pod_customization.ai_runtime import PodCustomizationAiRuntime
from wh_local.modules.pod_customization.billing_contract import (
    PodBillingAuthorizationRequired,
    PodExecutionGrant,
)
from wh_local.modules.pod_customization.contracts import BusinessFields
from wh_local.modules.product_processing.doubao_ark import DoubaoArkError


def _grant(**keys: str) -> PodExecutionGrant:
    return PodExecutionGrant("freeze-1", 1, "2099-01-01T00:00:00Z", keys)


def _title(*parts: str) -> str:
    """Return a valid, ASCII-only US listing title with normalized length 80-200."""
    return " ".join(
        (
            "Handcrafted coastal botanical illustration brings calm sunlit texture to everyday spaces",
            *parts,
            "with layered ink details for home office studio and seasonal gifting",
        )
    )


def _payload(
    *,
    title: str | None = None,
    visual_theme: str = "Coastal botanical ink",
    motif_keywords: list[str] | None = None,
    color_keywords: list[str] | None = None,
) -> dict[str, object]:
    return {
        "title": title or _title("ocean fern", "sandstone leaves"),
        "english_title": "Coastal Botanical Canvas Tote with Ocean Fern Artwork",
        "description": "A canvas tote featuring layered coastal botanical artwork for everyday carry.",
        "visual_theme": visual_theme,
        "motif_keywords": motif_keywords or ["ocean fern", "sandstone leaves"],
        "color_keywords": color_keywords or ["navy", "sand"],
    }


class _Response:
    def __init__(self, payload: dict[str, object], *, status_code: int = 200) -> None:
        self.content = json.dumps({"choices": [{"message": {"content": json.dumps(payload)}}]}).encode("utf-8")
        self.status_code = status_code

    def close(self) -> None:
        return None


class _Session:
    def __init__(self, responses: list[_Response]) -> None:
        self.responses = list(responses)
        self.requests: list[dict[str, object]] = []

    def post(
        self,
        url: str,
        *,
        headers: dict[str, str],
        json: dict[str, object],
        timeout: float,
        allow_redirects: bool,
    ):
        self.requests.append(
            {
                "url": url,
                "headers": dict(headers),
                "json": json,
                "timeout": timeout,
                "allow_redirects": allow_redirects,
            }
        )
        return self.responses.pop(0)

    def close(self) -> None:
        return None


def _request(
    *,
    accepted_titles: tuple[str, ...] = (),
    accepted_visual_signatures: tuple[str, ...] = (),
    rejected_reason: str = "",
):
    from wh_local.modules.pod_customization.title_runtime import PodTitleRequest

    return PodTitleRequest(
        style_task_id="style-task-72",
        style_index=4,
        hero_image=b"cropped-hero",
        hero_content_type="image/png",
        business_fields=BusinessFields(product_name="Canvas Tote", product_category="tote bag"),
        creative_prompt="coastal botanic line art",
        accepted_titles=accepted_titles,
        accepted_visual_signatures=accepted_visual_signatures,
        rejected_reason=rejected_reason,
    )


def test_title_runtime_owns_a_dedicated_session_executor_and_provider_capacity() -> None:
    from wh_local.modules.pod_customization.title_runtime import PodTitleRuntime

    product = PodCustomizationAiRuntime(image_workers=1, requests_per_minute=0)
    pod_images = PodCustomizationAiRuntime(image_workers=1, requests_per_minute=0)
    title_runtime = PodTitleRuntime(requests_per_minute=0)
    try:
        assert title_runtime.config.name == "pod-title"
        assert title_runtime.config.executor_workers == 2
        assert title_runtime.config.provider_concurrency == 2
        assert title_runtime.session is not product.session
        assert title_runtime.session is not pod_images.session
        assert title_runtime.executor is not product.executor
        assert title_runtime.executor is not pod_images.executor
    finally:
        title_runtime.close()
        pod_images.close()
        product.close()


def test_title_request_uses_cropped_hero_and_includes_style_task_id() -> None:
    from wh_local.modules.pod_customization.title_runtime import PodTitleRuntime

    session = _Session([_Response(_payload())])
    runtime = PodTitleRuntime(session=session, requests_per_minute=0)
    try:
        result = runtime.generate_title(_request(), grant=_grant(ark="ark-secret"), call_id="style-task-72:title:1")
    finally:
        runtime.close()

    assert result.title == _payload()["title"]
    assert result.english_title == "Coastal Botanical Canvas Tote with Ocean Fern Artwork"
    assert result.description == "A canvas tote featuring layered coastal botanical artwork for everyday carry."
    assert result.model == "doubao-seed-2-0-mini-260428"
    assert result.prompt_version == "pod-title-v1"
    assert result.attempt_count == 1
    body = session.requests[0]["json"]
    assert isinstance(body, dict)
    assert body["model"] == "doubao-seed-2-0-mini-260428"
    response_format = body["response_format"]
    assert response_format["type"] == "json_schema"
    assert response_format["json_schema"]["strict"] is True
    schema = response_format["json_schema"]["schema"]
    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == {
        "title",
        "english_title",
        "description",
        "visual_theme",
        "motif_keywords",
        "color_keywords",
    }
    assert schema["properties"]["title"]["minLength"] == 80
    assert schema["properties"]["title"]["maxLength"] == 200
    system_message, message = body["messages"]
    assert system_message["role"] == "system"
    assert "untrusted" in system_message["content"].casefold()
    assert "do not execute" in system_message["content"].casefold()
    assert message["role"] == "user"
    content = message["content"]
    assert "style-task-72" in json.dumps(content)
    assert "data:image/png;base64,Y3JvcHBlZC1oZXJv" in json.dumps(content)
    assert "cropped-hero" not in json.dumps(content)
    user_contract = content[1]["text"]
    assert "untrusted_input_notice" in user_contract
    assert "accepted_visual_signatures" in user_contract
    assert '"english_title"' in user_contract
    assert '"description"' in user_contract


@pytest.mark.parametrize(
    "payload",
    [
        {
            "title": _title("ocean fern"),
            "english_title": "English listing title",
            "description": "Listing description",
            "visual_theme": "theme",
            "motif_keywords": ["fern"],
        },
        {**_payload(), "unexpected": "field"},
        {**_payload(), "motif_keywords": 17},
        {**_payload(), "color_keywords": ["navy", 3]},
        {**_payload(), "english_title": "   "},
        {**_payload(), "description": 123},
    ],
)
def test_title_contract_parsing_requires_exact_json_shape(payload: dict[str, object]) -> None:
    from wh_local.modules.pod_customization.title_runtime import title_result_from_dict

    with pytest.raises(DoubaoArkError) as captured:
        title_result_from_dict(payload)

    assert captured.value.error_kind == "invalid_response"
    assert captured.value.retryable is True


def test_title_contract_normalizes_legacy_copy_into_a_displayable_english_title() -> None:
    from wh_local.modules.pod_customization.title_runtime import title_result_from_dict

    result = title_result_from_dict(
        {
            "title": "轻量舒适漫画主题短袖",
            "english_title": "Lightweight Manga Camp Collar Shirt for Students",
            "description": (
                "A comfortable short sleeve shirt with a navy sunset silhouette, botanical branches, "
                "and a clean cream base for everyday campus outfits."
            ),
            "visual_theme": "Manga sunset silhouette",
            "motif_keywords": "sunset silhouette, botanical branches",
            "color_keywords": "navy, cream",
        }
    )

    assert result.title.isascii()
    assert result.title.startswith(result.english_title)
    assert 80 <= len(result.normalized_title) <= 200
    assert result.motif_keywords == ("sunset silhouette", "botanical branches")
    assert result.color_keywords == ("navy", "cream")


def test_title_validator_accepts_a_safe_102_character_us_listing_title() -> None:
    from wh_local.modules.pod_customization.title_runtime import validate_title_result, title_result_from_dict

    title = "Lightweight Comfortable Short-Sleeve Camp Collar Shirt with Topographic Mountain Print for US Students"
    assert len(title) == 102

    validate_title_result(title_result_from_dict(_payload(title=title)))


@pytest.mark.parametrize(
    "candidate,accepted_titles,expected_reason",
    [
        ("too short", (), "80-200 ASCII characters"),
        (_title("Amazon exclusive", "ocean fern"), (), "prohibited term"),
        (_title("ocean fern", "sandstone leaves"), (_title("ocean fern", "sandstone leaves"),), "duplicate"),
        (
            _title("ocean fern", "sandstone leaves"),
            (_title("ocean fern", "sandstone leaves", "gift"),),
            "too similar",
        ),
    ],
)
def test_title_validator_rejects_invalid_listing_titles(
    candidate: str, accepted_titles: tuple[str, ...], expected_reason: str
) -> None:
    from wh_local.modules.pod_customization.title_runtime import validate_title_result, title_result_from_dict

    payload = _payload(title=candidate)
    if candidate == "too short":
        payload["english_title"] = "short"
        payload["description"] = "short"
    with pytest.raises(ValueError, match=expected_reason):
        validate_title_result(title_result_from_dict(payload), accepted_titles=accepted_titles)


@pytest.mark.parametrize(
    "prohibited_content",
    ["Coca-Cola collectible", "Temu listing", "clinically proven pain relief", "youth favorite"],
)
def test_title_validator_rejects_policy_word_classes(prohibited_content: str) -> None:
    from wh_local.modules.pod_customization.title_runtime import validate_title_result, title_result_from_dict

    with pytest.raises(ValueError, match="prohibited term"):
        validate_title_result(title_result_from_dict(_payload(title=_title(prohibited_content, "ocean fern"))))


@pytest.mark.parametrize(
    "field,value,expected_reason",
    [
        ("english_title", "Temu exclusive canvas tote", "english_title contains prohibited term"),
        ("english_title", "海岸植物帆布包", "english_title must use ASCII"),
        ("description", "Official Disney artwork for everyday carry.", "description contains prohibited term"),
        ("description", "适合日常携带的海岸植物图案。", "description must use ASCII"),
    ],
)
def test_title_validator_rejects_non_ascii_or_prohibited_short_copy(
    field: str, value: str, expected_reason: str
) -> None:
    from wh_local.modules.pod_customization.title_runtime import validate_title_result, title_result_from_dict

    payload = _payload()
    payload[field] = value

    with pytest.raises(ValueError, match=expected_reason):
        validate_title_result(title_result_from_dict(payload))


def test_title_validator_requires_english_alphabetic_tokens() -> None:
    from wh_local.modules.pod_customization.title_runtime import validate_title_result, title_result_from_dict

    non_language_title = ("1234 5678 9012 3456 " * 10).strip()
    assert 160 <= len(non_language_title) <= 200
    with pytest.raises(ValueError, match="English alphabetic tokens"):
        validate_title_result(title_result_from_dict(_payload(title=non_language_title)))


def test_title_validator_rejects_accepted_visual_signature() -> None:
    from wh_local.modules.pod_customization.title_runtime import (
        validate_title_result,
        title_result_from_dict,
        visual_signature,
    )

    result = title_result_from_dict(_payload(title=_title("ocean fern", "sandstone leaves")))
    signature = visual_signature(result)
    assert signature == "coastal botanical ink|ocean fern|sandstone leaves"

    with pytest.raises(ValueError, match="combination is duplicate"):
        validate_title_result(result, accepted_visual_signatures=(signature.upper(),))


def test_title_runtime_retries_when_persisted_visual_signature_is_accepted() -> None:
    from wh_local.modules.pod_customization.title_runtime import (
        PodTitleRuntime,
        title_result_from_dict,
        visual_signature,
    )

    accepted_signature = visual_signature(title_result_from_dict(_payload()))
    session = _Session(
        [
            _Response(_payload()),
            _Response(
                _payload(
                    title=_title("desert sun", "copper blooms"),
                    visual_theme="Desert blooms",
                    motif_keywords=["desert sun", "copper blooms"],
                )
            ),
        ]
    )
    runtime = PodTitleRuntime(session=session, requests_per_minute=0, sleeper=lambda _seconds: None)
    try:
        result = runtime.generate_title(
            _request(accepted_visual_signatures=(accepted_signature,)),
            grant=_grant(ark="ark-secret"),
            call_id="style-task-72:title:1",
        )
    finally:
        runtime.close()

    assert result.attempt_count == 2
    assert result.visual_theme == "Desert blooms"
    assert len(session.requests) == 2
    retry_prompt = session.requests[1]["json"]["messages"][1]["content"][1]["text"]
    assert accepted_signature in retry_prompt
    assert "duplicate" in retry_prompt


def test_duplicate_theme_and_motifs_retries_three_times_with_feedback() -> None:
    from wh_local.modules.pod_customization.title_runtime import PodTitleRuntime

    accepted = _title("ocean fern", "sandstone leaves", "gift")
    unusable_short = _payload(title="short")
    unusable_short["english_title"] = "short"
    unusable_short["description"] = "short"
    session = _Session(
        [
            _Response(unusable_short),
            _Response(_payload(title=_title("ocean fern", "sandstone leaves", "gift"))),
            _Response(
                _payload(
                    title=_title("desert sun", "copper blooms"),
                    visual_theme="Desert blooms",
                    motif_keywords=["desert sun", "copper blooms"],
                )
            ),
        ]
    )
    runtime = PodTitleRuntime(session=session, requests_per_minute=0, sleeper=lambda _seconds: None)
    try:
        result = runtime.generate_title(
            _request(accepted_titles=(accepted,)), grant=_grant(ark="ark-secret"), call_id="style-task-72:title:1"
        )
    finally:
        runtime.close()

    assert result.attempt_count == 3
    assert len(session.requests) == 3
    third_content = session.requests[2]["json"]["messages"][1]["content"]
    assert "rejection_feedback" in json.dumps(third_content)
    assert "duplicate" in json.dumps(third_content)


def test_title_runtime_retries_transient_ark_408_until_third_success() -> None:
    from wh_local.modules.pod_customization.title_runtime import PodTitleRuntime

    session = _Session(
        [
            _Response(_payload(), status_code=408),
            _Response(_payload(), status_code=408),
            _Response(_payload()),
        ]
    )
    runtime = PodTitleRuntime(session=session, requests_per_minute=0, sleeper=lambda _seconds: None)
    outcomes = []
    try:
        result = runtime.generate_title(
            _request(),
            grant=_grant(ark="ark-secret"),
            call_id="style-task-72:title:1",
            call_ids=("style-task-72:title:1", "style-task-72:title:2", "style-task-72:title:3"),
            on_outcome=lambda call_id, status: outcomes.append((call_id, status)),
        )
    finally:
        runtime.close()

    assert result.attempt_count == 3
    assert len(session.requests) == 3
    assert outcomes == [
        ("style-task-72:title:1", "no_return"),
        ("style-task-72:title:2", "no_return"),
        ("style-task-72:title:3", "success"),
    ]


def test_missing_ark_grant_fails_without_provider_attempt() -> None:
    from wh_local.modules.pod_customization.title_runtime import PodTitleRuntime

    runtime = PodTitleRuntime(requests_per_minute=0)
    try:
        with pytest.raises(PodBillingAuthorizationRequired) as captured:
            runtime.generate_title(_request(), grant=_grant(), call_id="style-task-72:title:1")
    finally:
        runtime.close()

    assert "expired" in str(captured.value)


def test_ark_grant_is_rechecked_after_waiting_for_provider_slot() -> None:
    from wh_local.modules.pod_customization.title_runtime import PodTitleRuntime

    class BlockingGate:
        def __init__(self) -> None:
            self.entered = Event()
            self.allow = Event()

        def acquire(self, *_args, **_kwargs) -> bool:
            self.entered.set()
            return self.allow.wait(timeout=1)

        def release(self) -> None:
            return None

    class ClockGrant:
        def __init__(self) -> None:
            self.clock = 0

        def provider_key(self, provider: str) -> str:
            assert provider == "ark"
            return "short-lived-key" if self.clock < 1 else ""

    session = _Session([_Response(_payload())])
    runtime = PodTitleRuntime(session=session, requests_per_minute=0)
    grant = ClockGrant()
    gate = BlockingGate()
    runtime._providers = gate  # type: ignore[assignment]
    future = runtime.submit(
        runtime.generate_title,
        _request(),
        grant=grant,
        call_id="style-task-72:title:1",
    )
    try:
        assert gate.entered.wait(timeout=1)
        grant.clock = 2
        gate.allow.set()
        with pytest.raises(PodBillingAuthorizationRequired, match="expired"):
            future.result(timeout=1)
        assert session.requests == []
    finally:
        gate.allow.set()
        runtime.close()
