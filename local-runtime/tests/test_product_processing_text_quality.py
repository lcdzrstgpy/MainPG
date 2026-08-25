from __future__ import annotations

import socket
import threading

import pytest

import wh_local.modules.product_processing.service as service_module
from wh_local.data_collection.public_image_fetch import FetchedPublicImage, PublicImageFetchError
from wh_local.modules.product_processing.domain import policy as policy_module
from wh_local.modules.product_processing.doubao_vision import (
    DoubaoVisionError,
    SubjectAnalysis,
)
from wh_local.modules.product_processing.doubao_text import (
    DoubaoTextError,
    DoubaoTextResult,
)
from wh_local.modules.product_processing.service import (
    GridImageOutput,
    ProductProcessingService,
)


VALID_DESCRIPTION = "\n".join(
    [
        "- VERIFIED STEEL BUILD: Confirmed stainless steel construction supports dependable everyday drink service while preserving the source-supported product identity and clean travel mug shape.",
        "- PRACTICAL DRINK USE: The mug is suited to routine beverage use at a desk, during a commute, or in other ordinary daily settings.",
        "- CONFIRMED CAPACITY SIZE: The verified 500 ml capacity provides a clear volume reference without adding unsupported dimensions or package claims.",
        "- SIMPLE DAILY HANDLING: The straightforward mug format is easy to place, carry, and incorporate into a regular beverage routine with normal care.",
        "- USEFUL PORTABLE FORMAT: Its travel-oriented form combines the confirmed capacity and construction in one practical item for supported on-the-go use.",
    ]
)


def test_doubao_source_image_download_retries_transient_failure(monkeypatch) -> None:
    service = object.__new__(ProductProcessingService)
    service._source_data_url_cache = {}
    service._source_data_url_lock = threading.Lock()
    calls = 0

    def fetcher(_url: str, **_kwargs) -> FetchedPublicImage:
        nonlocal calls
        calls += 1
        if calls < 3:
            raise PublicImageFetchError("temporary CDN failure")
        return FetchedPublicImage(b"\xff\xd8\xffimage", "image/jpeg", _url)

    service._public_image_fetcher = fetcher
    monkeypatch.setattr(service_module.time, "sleep", lambda _seconds: None)

    value = service._image_to_data_url("https://example.com/source.jpg")

    assert value.startswith("data:image/jpeg;base64,")
    assert calls == 3
    assert service._image_to_data_url("https://example.com/source.jpg") == value
    assert calls == 3


def test_doubao_source_image_download_uses_hardened_fetcher_behind_proxy_fake_ip(
    monkeypatch,
) -> None:
    service = object.__new__(ProductProcessingService)
    service._source_data_url_cache = {}
    service._source_data_url_lock = threading.Lock()
    calls: list[str] = []

    monkeypatch.setattr(
        policy_module.socket,
        "getaddrinfo",
        lambda *_args, **_kwargs: [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("198.18.0.30", 443))
        ],
    )

    def fetcher(url: str, **_kwargs) -> FetchedPublicImage:
        calls.append(url)
        return FetchedPublicImage(b"\xff\xd8\xffimage", "image/jpeg", url)

    service._public_image_fetcher = fetcher

    value = service._image_to_data_url("https://cbu01.alicdn.com/source.jpg")

    assert value.startswith("data:image/jpeg;base64,")
    assert calls == ["https://cbu01.alicdn.com/source.jpg"]


def test_local_source_download_uses_hardened_fetcher_behind_proxy_fake_ip(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        policy_module.socket,
        "getaddrinfo",
        lambda *_args, **_kwargs: [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("198.18.0.30", 443))
        ],
    )
    calls: list[str] = []

    def fetcher(url: str, **_kwargs) -> FetchedPublicImage:
        calls.append(url)
        return FetchedPublicImage(b"\xff\xd8\xffimage", "image/jpeg", url)

    monkeypatch.setattr(service_module, "fetch_public_image", fetcher)

    content = ProductProcessingService._local_source_bytes(
        "https://cbu01.alicdn.com/source.jpg"
    )

    assert content == b"\xff\xd8\xffimage"
    assert calls == ["https://cbu01.alicdn.com/source.jpg"]


def _raw() -> dict:
    return {
        "source_url": "https://example.com/product",
        "source_image_urls": ["https://example.com/source.jpg"],
        "category": "Kitchen & Dining",
        "category_path": "Home & Kitchen > Kitchen & Dining",
        "source_attributes": [
            {"attribute_name_en": "Capacity", "value_name_en": "500 ml"},
            {"attribute_name_en": "Material", "value_name_en": "Stainless Steel"},
        ],
    }


class _PromptRepository:
    @staticmethod
    def prompts() -> dict[str, str]:
        return {
            "desc": (
                "CUSTOM OPERATOR DESCRIPTION RULE: emphasize the verified capacity before convenience.\n"
                "Product: {title}\nCategory: {category}\nOutput exactly five English bullet points."
            )
        }

    @staticmethod
    def active_prompt_template() -> None:
        return None


class _ReceiptRepository(_PromptRepository):
    def __init__(self) -> None:
        self.receipts: dict[str, dict] = {}

    def load_stage_receipt(self, _task_id, _item_id, stage, **_kwargs):
        return self.receipts.get(stage)

    def upsert_stage_receipt(
        self, _task_id, _item_id, stage, *, input_hash, output_data, **_kwargs
    ):
        receipt = {"input_hash": input_hash, "output": output_data}
        self.receipts[stage] = receipt
        return receipt

    def delete_invalid_stage_receipt(
        self, _task_id, _item_id, stage, *, expected_input_hash, **_kwargs
    ):
        receipt = self.receipts.get(stage)
        if receipt and receipt["input_hash"] != expected_input_hash:
            self.receipts.pop(stage, None)
            return True
        return False

    def delete_downstream_stage_receipts(
        self, _task_id, _item_id, stages, **_kwargs
    ):
        for stage in stages:
            self.receipts.pop(stage, None)
        return 0


class _DoubaoListingClient:
    def __init__(self, result: DoubaoTextResult) -> None:
        self.result = result
        self.prompts: list[str] = []
        self.last_attempt_count = 1

    def generate_listing_text(self, prompt: str, *, validator=None) -> DoubaoTextResult:
        self.prompts.append(prompt)
        if validator is not None:
            validator(self.result)
        return self.result


def test_doubao_text_stage_uses_subject_json_and_source_facts_without_image(monkeypatch) -> None:
    service = object.__new__(ProductProcessingService)
    service.repository = _PromptRepository()
    client = _DoubaoListingClient(
        DoubaoTextResult(
            optimized_title="Insulated Stainless Steel Travel Mug, 500 ml Portable Drink Cup",
            description=VALID_DESCRIPTION,
            variant_translations=(),
            product_dimensions={},
        )
    )
    monkeypatch.setattr(service, "_doubao_text_client", lambda: client)
    assert not hasattr(ProductProcessingService, "_ai_client")

    result = service._generate_doubao_text(
        "500 ml Stainless Steel Travel Mug",
        "Kitchen & Dining",
        _raw(),
        "en",
        "US",
        [],
        vision_identity={
            "sellable_subject": "insulated stainless steel travel mug",
            "subject_explanation": "The foreground mug is the complete sellable product.",
            "visible_attributes": ["blue cylindrical body", "fitted lid"],
            "excluded_elements": ["table"],
            "confidence": "high",
            "uncertainty_reason": "",
        },
        needs_title=True,
        needs_description=True,
        needs_dimensions=False,
    )

    assert result["title"].startswith("Insulated Stainless Steel Travel Mug")
    prompt = client.prompts[0]
    assert "AUTHORITATIVE SUBJECT ANALYSIS FROM THE ORIGINAL 1688 IMAGE:" in prompt
    assert '"sellable_subject": "insulated stainless steel travel mug"' in prompt
    assert '"Capacity": "500 ml"' in prompt
    assert "data:image" not in prompt
    assert "image_url" not in prompt


def test_chinese_template_addition_is_translated_before_injection(monkeypatch) -> None:
    """含中文的模板附加词先翻译成目标语言再拼入生成提示词，避免 AI 复写中文被拒。"""
    service = object.__new__(ProductProcessingService)

    class _TemplateRepo(_PromptRepository):
        @staticmethod
        def active_prompt_template():
            return {
                "prompts": {
                    "title": "必须加入节日促销关键词，如七夕节送礼必备",
                    "desc": "必须加入节日促销关键词，如七夕节送礼必备",
                    "variant_values": "",
                }
            }

    service.repository = _TemplateRepo()
    monkeypatch.setattr(
        service,
        "_translate_prompt_addition",
        lambda text, lang: "Include holiday gift keywords such as Valentine's Day gift",
    )
    client = _DoubaoListingClient(
        DoubaoTextResult(
            optimized_title="Insulated Stainless Steel Travel Mug, 500 ml Portable Drink Cup",
            description=VALID_DESCRIPTION,
            variant_translations=(),
            product_dimensions={},
        )
    )
    monkeypatch.setattr(service, "_doubao_text_client", lambda: client)

    result = service._generate_doubao_text(
        "500 ml Stainless Steel Travel Mug",
        "Kitchen & Dining",
        _raw(),
        "en",
        "US",
        [],
        vision_identity={
            "sellable_subject": "insulated stainless steel travel mug",
            "subject_explanation": "The foreground mug is the complete sellable product.",
            "visible_attributes": ["blue cylindrical body", "fitted lid"],
            "excluded_elements": ["table"],
            "confidence": "high",
            "uncertainty_reason": "",
        },
        needs_title=True,
        needs_description=True,
        needs_dimensions=False,
    )

    assert result["title"].startswith("Insulated Stainless Steel Travel Mug")
    prompt = client.prompts[0]
    assert "Include holiday gift keywords such as Valentine's Day gift" in prompt
    assert "必须加入节日促销关键词" not in prompt


def test_doubao_text_dimension_scope_requests_complete_numeric_estimates(monkeypatch) -> None:
    service = object.__new__(ProductProcessingService)
    service.repository = _PromptRepository()
    client = _DoubaoListingClient(
        DoubaoTextResult(
            optimized_title="",
            description="",
            variant_translations=(),
            product_dimensions={
                "length_cm": 4.2,
                "width_cm": 2.9,
                "height_cm": 2.0,
                "weight_g": 20,
            },
        )
    )
    monkeypatch.setattr(service, "_doubao_text_client", lambda: client)

    result = service._generate_doubao_text(
        "42 mm Ivory Mahjong Tile Set",
        "Tabletop Games",
        _raw(),
        "en",
        "US",
        [],
        vision_identity={
            "sellable_subject": "home hand-play mahjong set",
            "subject_explanation": "The foreground tiles are the sellable product.",
            "visible_attributes": ["ivory-colored tiles"],
            "excluded_elements": ["background"],
            "confidence": "high",
            "uncertainty_reason": "",
        },
        needs_title=False,
        needs_description=False,
        needs_dimensions=True,
    )

    assert result["product_dimensions"]["length_cm"] == 4.2
    prompt = client.prompts[0]
    assert "return all four positive numeric fields" in prompt
    assert "Conservatively estimate only missing dimension values" in prompt
    assert "Do not use dimension estimates in the title or description" in prompt


def test_prompt_evidence_is_ordered_deduplicated_and_used_by_combined_cache(monkeypatch) -> None:
    raw = {
        **_raw(),
        "source_attributes": [
            {"name": "Material", "value": " Stainless   Steel "},
            {"attribute_name_en": "material", "value_name_en": "stainless steel"},
            {"name": "Capacity", "value": "500 ml"},
        ],
        "source_variant_records": [
            {"attributes": {"Color": " Blue ", "Size": "Large"}},
            {"attributes": {"color": "blue", "Pack": "Large"}},
        ],
        "stock": 999,
        "price": 123,
        "source_image_urls": ["https://example.com/mutable.jpg"],
    }
    evidence = ProductProcessingService._canonical_prompt_evidence(raw)
    assert evidence == {
        "source_attributes": [
            {"name": "Material", "value": "Stainless Steel"},
            {"name": "Capacity", "value": "500 ml"},
        ],
        "variant_attributes": [
            {"name": "Color", "value": "Blue"},
            {"name": "Size", "value": "Large"},
            {"name": "Pack", "value": "Large"},
        ],
    }
    assert ProductProcessingService._unique_variant_values(raw) == ["Blue", "Large"]
    size_text = ProductProcessingService._size_source_text(raw, "Travel Mug")
    assert size_text.count("Stainless Steel") == 1
    assert size_text.count("Large") == 2  # distinct ordered attributes remain evidence

def _process_service(monkeypatch) -> ProductProcessingService:
    service = object.__new__(ProductProcessingService)
    class _Repository:
        @staticmethod
        def prompts() -> dict[str, str]:
            return {}

        @staticmethod
        def active_prompt_template() -> None:
            return None

    service.repository = _Repository()
    monkeypatch.setattr(service_module, "product_policy_issue", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        service,
        "_recognize_doubao_subject",
        lambda *args, **kwargs: SubjectAnalysis(
            sellable_subject="travel mug",
            subject_explanation="The travel mug is the complete foreground sellable product.",
            visible_attributes=("cylindrical body",),
            excluded_elements=("background",),
            confidence="high",
            uncertainty_reason="",
        ),
    )
    monkeypatch.setattr(
        service,
        "_generate_doubao_text",
        lambda *args, **kwargs: {
            "title": "Insulated Stainless Steel Travel Mug, 500 ml Portable Drink Cup",
            "description": VALID_DESCRIPTION,
            "variant_translations": {},
            "vision_subject": "travel mug",
            "vision_preliminary_title": "",
            "product_dimensions": {},
        },
        raising=False,
    )
    return service


def test_doubao_subject_precedes_independent_text_and_image_branches(monkeypatch) -> None:
    service = _process_service(monkeypatch)
    order: list[str] = []
    captured: dict[str, object] = {}

    def recognize(*_args, **_kwargs) -> SubjectAnalysis:
        order.append("doubao")
        captured["vision_source_title"] = _args[1]
        return SubjectAnalysis(
            sellable_subject="rectangular bamboo cooling mat",
            subject_explanation="The foreground woven mat is the complete sellable product.",
            visible_attributes=("rectangular", "woven bamboo surface"),
            excluded_elements=("bed", "pillows"),
            confidence="high",
            uncertainty_reason="",
        )

    def combined(*_args, **kwargs):
        order.append("doubao-text")
        captured["combined_identity"] = kwargs["vision_identity"]
        return {
            "title": "Rectangular Bamboo Cooling Mat, Woven Summer Sleeping Pad",
            "description": VALID_DESCRIPTION,
            "variant_translations": {},
            "vision_subject": "wrong GPT subject",
            "vision_preliminary_title": "Bamboo Cooling Mat",
            "product_dimensions": {},
        }

    def grid(*_args, **kwargs):
        order.append("gpt-image")
        captured["grid_identity"] = kwargs["vision_identity"]
        return GridImageOutput(
            carousel_urls=tuple(f"https://example.com/grid-{index}.jpg" for index in range(4)),
            attempt_count=1,
            provider_status_class="success",
        )

    monkeypatch.setattr(service, "_recognize_doubao_subject", recognize)
    monkeypatch.setattr(service, "_generate_doubao_text", combined)
    monkeypatch.setattr(service, "_generate_grid_images", grid)

    source_title = "1688 ORIGINAL TRAVEL MUG TITLE"
    draft = {
        **_draft(),
        "title": "PREVIOUSLY OPTIMIZED TITLE",
        "raw_payload": {**_raw(), "source_title": source_title},
    }
    result = service._process_one({"id": 1}, draft, _settings(), False, task_id=12)

    assert result["status"] == "completed"
    assert order[0] == "doubao"
    assert set(order[1:]) == {"doubao-text", "gpt-image"}
    assert result["result"]["vision_identity"]["sellable_subject"] == (
        "rectangular bamboo cooling mat"
    )
    assert captured["combined_identity"] == result["result"]["vision_identity"]
    assert captured["grid_identity"] == result["result"]["vision_identity"]
    assert captured["vision_source_title"] == source_title


def test_doubao_text_failure_keeps_gpt_image_result_and_requires_attention(monkeypatch) -> None:
    service = _process_service(monkeypatch)
    captured: dict[str, str] = {}
    monkeypatch.setattr(
        service,
        "_generate_doubao_text",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            DoubaoTextError(
                "text contract exhausted",
                error_kind="invalid_response",
                retryable=True,
                attempt_count=3,
            )
        ),
        raising=False,
    )

    def grid(*args, **kwargs):
        captured["title"] = args[3]
        return GridImageOutput(
            carousel_urls=tuple(
                f"https://example.com/grid-{index}.jpg" for index in range(4)
            ),
            attempt_count=1,
            provider_status_class="success",
        )

    monkeypatch.setattr(service, "_generate_grid_images", grid)
    assert not hasattr(ProductProcessingService, "_generate_title")
    assert not hasattr(ProductProcessingService, "_generate_description")
    assert not hasattr(ProductProcessingService, "_translate_variant_values")
    assert not hasattr(ProductProcessingService, "_generate_size")

    result = service._process_one({"id": 1}, _draft(), _settings(), False, task_id=12)

    assert result["status"] == "attention_required"
    assert len(result["result"]["carousel_image_paths"]) == 4
    assert result["result"]["provider_attempts"]["doubao_text"] == 3
    assert result["result"]["provider_status_classes"]["doubao_text"] == "invalid_response"
    assert result["result"]["text_generation"]["status"] == "failed"
    assert captured["title"] == _draft()["title"]


def test_image_branch_never_uses_doubao_optimized_title(monkeypatch) -> None:
    service = _process_service(monkeypatch)
    captured: dict[str, str] = {}
    monkeypatch.setattr(
        service,
        "_generate_doubao_text",
        lambda *args, **kwargs: {
            "title": "DOUBAO OPTIMIZED TITLE MUST NOT REACH IMAGE",
            "description": VALID_DESCRIPTION,
            "variant_translations": {},
            "vision_subject": "travel mug",
            "vision_preliminary_title": "",
            "product_dimensions": {},
        },
        raising=False,
    )

    def grid(*args, **kwargs):
        captured["title"] = args[3]
        return GridImageOutput(
            carousel_urls=tuple(
                f"https://example.com/grid-{index}.jpg" for index in range(4)
            ),
            attempt_count=1,
            provider_status_class="success",
        )

    monkeypatch.setattr(service, "_generate_grid_images", grid)
    result = service._process_one({"id": 1}, _draft(), _settings(), False, task_id=12)

    assert result["status"] == "completed"
    assert result["result"]["optimized_title"].startswith("DOUBAO OPTIMIZED")
    assert captured["title"] == _draft()["title"]


def test_low_confidence_subject_without_subject_blocks_all_gpt_calls(monkeypatch) -> None:
    """低置信且模型完全无法给出可售主体时仍拦截（极端兜底）。"""
    service = _process_service(monkeypatch)
    monkeypatch.setattr(
        service,
        "_recognize_doubao_subject",
        lambda *_args, **_kwargs: SubjectAnalysis(
            sellable_subject="",
            subject_explanation="No single sellable product could be identified.",
            visible_attributes=(),
            excluded_elements=("room",),
            confidence="low",
            uncertainty_reason="Nothing clearly sellable visible.",
        ),
    )
    monkeypatch.setattr(
        service,
        "_generate_doubao_text",
        lambda *_args, **_kwargs: pytest.fail("GPT text must not run"),
    )
    monkeypatch.setattr(
        service,
        "_generate_grid_images",
        lambda *_args, **_kwargs: pytest.fail("GPT image must not run"),
    )

    result = service._process_one({"id": 1}, _draft(), _settings(), False, task_id=12)

    assert result["status"] == "attention_required"
    assert result["result"]["error_type"] == "vision_subject_low_confidence"
    assert result["result"]["retryable"] is True


def test_low_confidence_subject_with_subject_passes_automatically(monkeypatch) -> None:
    """低置信但模型已识别出可售主体（多色号/多件套等正常主图）直接放行，降低误杀。"""
    service = _process_service(monkeypatch)
    monkeypatch.setattr(
        service,
        "_recognize_doubao_subject",
        lambda *_args, **_kwargs: SubjectAnalysis(
            sellable_subject="possible textile item",
            subject_explanation="Several foreground objects may be sellable.",
            visible_attributes=(),
            excluded_elements=("room",),
            confidence="low",
            uncertainty_reason="Multiple products overlap.",
        ),
    )
    captured: dict[str, object] = {}

    def combined(*_args, **kwargs):
        captured["identity"] = kwargs["vision_identity"]
        return {
            "title": "Textile Item with Multi-Use Storage",
            "description": VALID_DESCRIPTION,
            "variant_translations": {},
            "vision_subject": "possible textile item",
            "vision_preliminary_title": "",
            "product_dimensions": {},
        }

    def grid(*_args, **kwargs):
        captured["grid_identity"] = kwargs["vision_identity"]
        return GridImageOutput(
            carousel_urls=tuple(
                f"https://example.com/grid-{index}.jpg" for index in range(4)
            ),
            attempt_count=1,
            provider_status_class="success",
        )

    monkeypatch.setattr(service, "_generate_doubao_text", combined)
    monkeypatch.setattr(service, "_generate_grid_images", grid)

    result = service._process_one({"id": 1}, _draft(), _settings(), False, task_id=12)

    assert result["status"] == "completed"
    assert result["result"]["vision_identity"]["status"] == "accepted"
    assert result["result"]["vision_identity"]["confidence"] == "low"
    assert "subject_identity:low-confidence-pass" in result["result"]["ai_notes"]
    assert captured["identity"] == result["result"]["vision_identity"]
    assert captured["grid_identity"] == result["result"]["vision_identity"]


def test_low_confidence_subject_passes_when_user_confirms_sellable(monkeypatch) -> None:
    service = _process_service(monkeypatch)
    monkeypatch.setattr(
        service,
        "_recognize_doubao_subject",
        lambda *_args, **_kwargs: SubjectAnalysis(
            sellable_subject="possible textile item",
            subject_explanation="Several foreground objects may be sellable.",
            visible_attributes=(),
            excluded_elements=("room",),
            confidence="low",
            uncertainty_reason="Multiple products overlap.",
        ),
    )
    captured: dict[str, object] = {}

    def combined(*_args, **kwargs):
        captured["identity"] = kwargs["vision_identity"]
        return {
            "title": "Textile Item with Multi-Use Storage",
            "description": VALID_DESCRIPTION,
            "variant_translations": {},
            "vision_subject": "possible textile item",
            "vision_preliminary_title": "",
            "product_dimensions": {},
        }

    def grid(*_args, **kwargs):
        captured["grid_identity"] = kwargs["vision_identity"]
        return GridImageOutput(
            carousel_urls=tuple(
                f"https://example.com/grid-{index}.jpg" for index in range(4)
            ),
            attempt_count=1,
            provider_status_class="success",
        )

    monkeypatch.setattr(service, "_generate_doubao_text", combined)
    monkeypatch.setattr(service, "_generate_grid_images", grid)
    settings = {**_settings(), "identity_override_draft_ids": [7]}

    result = service._process_one({"id": 1}, _draft(), settings, False, task_id=12)

    assert result["status"] == "completed"
    assert result["result"]["vision_identity"]["status"] == "user_override"
    assert result["result"]["vision_identity"]["confidence"] == "low"
    assert captured["identity"] == result["result"]["vision_identity"]
    assert captured["grid_identity"] == result["result"]["vision_identity"]


@pytest.mark.parametrize(
    ("error", "expected_status", "expected_failure_class"),
    [
        (
            DoubaoVisionError(
                "temporary",
                error_kind="transient",
                retryable=True,
            ),
            "failed",
            "technical_retryable",
        ),
        (
            DoubaoVisionError(
                "missing key",
                error_kind="configuration",
                retryable=False,
            ),
            "attention_required",
            "configuration_blocked",
        ),
        (
            DoubaoVisionError(
                "invalid subject JSON",
                error_kind="invalid_response",
                retryable=False,
            ),
            "attention_required",
            "identity_review_required",
        ),
    ],
)
def test_doubao_failure_blocks_gpt_with_stable_task_status(
    monkeypatch, error, expected_status: str, expected_failure_class: str
) -> None:
    service = _process_service(monkeypatch)
    monkeypatch.setattr(
        service,
        "_recognize_doubao_subject",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(error),
    )
    monkeypatch.setattr(
        service,
        "_generate_doubao_text",
        lambda *_args, **_kwargs: pytest.fail("GPT text must not run"),
    )
    monkeypatch.setattr(
        service,
        "_generate_grid_images",
        lambda *_args, **_kwargs: pytest.fail("GPT image must not run"),
    )

    result = service._process_one({"id": 1}, _draft(), _settings(), False, task_id=12)

    assert result["status"] == expected_status
    assert result["result"]["error_type"] == "vision_service_unavailable"
    assert result["result"]["failure_class"] == expected_failure_class


def test_doubao_retry_exhaustion_reports_two_provider_attempts(monkeypatch) -> None:
    service = _process_service(monkeypatch)
    error = DoubaoVisionError(
        "temporary",
        error_kind="transient",
        retryable=True,
        attempt_count=2,
    )
    monkeypatch.setattr(
        service,
        "_recognize_doubao_subject",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(error),
    )

    result = service._process_one({"id": 1}, _draft(), _settings(), False, task_id=12)

    assert result["status"] == "failed"
    assert result["result"]["provider_attempts"]["doubao_vision"] == 2


def test_doubao_identity_receipt_avoids_repeat_recognition_on_task_retry(monkeypatch) -> None:
    service = _process_service(monkeypatch)

    class ReceiptRepository:
        def __init__(self) -> None:
            self.receipts: dict[str, dict] = {}

        @staticmethod
        def prompts() -> dict[str, str]:
            return {}

        @staticmethod
        def active_prompt_template() -> None:
            return None

        def load_stage_receipt(self, _task_id, _item_id, stage, **_kwargs):
            return self.receipts.get(stage)

        def upsert_stage_receipt(
            self, _task_id, _item_id, stage, *, input_hash, output_data, **_kwargs
        ):
            receipt = {"input_hash": input_hash, "output": output_data}
            self.receipts[stage] = receipt
            return receipt

        def delete_invalid_stage_receipt(
            self, _task_id, _item_id, stage, *, expected_input_hash, **_kwargs
        ):
            receipt = self.receipts.get(stage)
            if receipt and receipt["input_hash"] != expected_input_hash:
                self.receipts.pop(stage, None)
                return True
            return False

        def delete_downstream_stage_receipts(
            self, _task_id, _item_id, stages, **_kwargs
        ):
            for stage in stages:
                self.receipts.pop(stage, None)
            return 0

    service.repository = ReceiptRepository()
    recognition_calls = 0

    def recognize(*_args, **_kwargs) -> SubjectAnalysis:
        nonlocal recognition_calls
        recognition_calls += 1
        return SubjectAnalysis(
            sellable_subject="travel mug",
            subject_explanation="The foreground travel mug is the complete product.",
            visible_attributes=("cylindrical body",),
            excluded_elements=("table",),
            confidence="high",
            uncertainty_reason="",
        )

    monkeypatch.setattr(service, "_recognize_doubao_subject", recognize)
    monkeypatch.setattr(
        service,
        "_generate_doubao_text",
        lambda *args, **kwargs: {
            "title": "Insulated Stainless Steel Travel Mug, 500 ml Portable Drink Cup",
            "description": VALID_DESCRIPTION,
            "variant_translations": {},
            "product_dimensions": {},
        },
    )
    monkeypatch.setattr(
        service,
        "_generate_grid_images",
        lambda *args, **kwargs: GridImageOutput(
            carousel_urls=tuple(f"https://example.com/grid-{index}.jpg" for index in range(4)),
            attempt_count=1,
            provider_status_class="success",
        ),
    )

    first = service._process_one(
        {"id": 1, "item_id": 101}, _draft(), _settings(), False, task_id=12
    )
    assert "images" in service.repository.receipts
    second = service._process_one(
        {"id": 1, "item_id": 101}, _draft(), _settings(), False, task_id=12
    )

    assert first["status"] == second["status"] == "completed"
    assert recognition_calls == 1
    assert second["result"]["provider_attempts"]["doubao_vision"] == 0
    assert second["result"]["provider_status_classes"]["doubao_vision"] == "receipt_hit"


def test_source_title_change_invalidates_doubao_identity_receipt(monkeypatch) -> None:
    service = _process_service(monkeypatch)
    service.repository = _ReceiptRepository()
    recognition_titles: list[str] = []

    def recognize(_image_url: str, source_title: str) -> SubjectAnalysis:
        recognition_titles.append(source_title)
        return SubjectAnalysis(
            sellable_subject="travel mug",
            subject_explanation="The foreground travel mug is the complete product.",
            visible_attributes=("cylindrical body",),
            excluded_elements=("table",),
            confidence="high",
            uncertainty_reason="",
        )

    monkeypatch.setattr(service, "_recognize_doubao_subject", recognize)
    monkeypatch.setattr(
        service,
        "_generate_grid_images",
        lambda *args, **kwargs: GridImageOutput(
            carousel_urls=tuple(
                f"https://example.com/grid-{index}.jpg" for index in range(4)
            ),
            attempt_count=1,
            provider_status_class="success",
        ),
    )

    first_draft = {
        **_draft(),
        "raw_payload": {**_raw(), "source_title": "1688 ORIGINAL MUG TITLE"},
    }
    second_draft = {
        **_draft(),
        "raw_payload": {**_raw(), "source_title": "1688 CORRECTED MUG TITLE"},
    }
    first = service._process_one(
        {"id": 1, "item_id": 101}, first_draft, _settings(), False, task_id=12
    )
    second = service._process_one(
        {"id": 1, "item_id": 101}, second_draft, _settings(), False, task_id=12
    )

    assert first["status"] == second["status"] == "completed"
    assert recognition_titles == [
        first_draft["raw_payload"]["source_title"],
        second_draft["raw_payload"]["source_title"],
    ]
    assert second["result"]["provider_attempts"]["doubao_vision"] == 1


def test_source_title_is_part_of_in_memory_doubao_subject_cache(monkeypatch) -> None:
    service = object.__new__(ProductProcessingService)
    service._doubao_subject_cache = {}
    service._doubao_subject_cache_lock = threading.Lock()
    calls: list[str] = []

    class Client:
        last_attempt_count = 1

        def recognize_subject(
            self, _image_data_url: str, source_title: str
        ) -> SubjectAnalysis:
            calls.append(source_title)
            return SubjectAnalysis(
                sellable_subject="mahjong tile set",
                subject_explanation="The foreground tiles are the sellable product.",
                visible_attributes=("ivory-colored tiles",),
                excluded_elements=("table cloth",),
                confidence="high",
                uncertainty_reason="",
            )

    monkeypatch.setattr(
        service,
        "_image_to_data_url",
        lambda _url: "data:image/jpeg;base64,dGVzdA==",
    )
    monkeypatch.setattr(service, "_doubao_vision_client", Client)

    first = service._recognize_doubao_subject(
        "https://example.com/mahjong.jpg", "象牙色家用麻将牌"
    )
    repeated = service._recognize_doubao_subject(
        "https://example.com/mahjong.jpg", "象牙色家用麻将牌"
    )
    changed = service._recognize_doubao_subject(
        "https://example.com/mahjong.jpg", "象牙色家用麻将套装"
    )

    assert first == repeated == changed
    assert calls == ["象牙色家用麻将牌", "象牙色家用麻将套装"]
    assert service._attempt_state().doubao_vision == 1


def test_retry_reuses_successful_images_and_only_retries_failed_doubao_text(monkeypatch) -> None:
    service = _process_service(monkeypatch)

    class ReceiptRepository:
        def __init__(self) -> None:
            self.receipts: dict[str, dict] = {}

        @staticmethod
        def prompts() -> dict[str, str]:
            return {}

        @staticmethod
        def active_prompt_template() -> None:
            return None

        def load_stage_receipt(self, _task_id, _item_id, stage, **_kwargs):
            return self.receipts.get(stage)

        def upsert_stage_receipt(
            self, _task_id, _item_id, stage, *, input_hash, output_data, **_kwargs
        ):
            receipt = {"input_hash": input_hash, "output": output_data}
            self.receipts[stage] = receipt
            return receipt

        def delete_invalid_stage_receipt(
            self, _task_id, _item_id, stage, *, expected_input_hash, **_kwargs
        ):
            receipt = self.receipts.get(stage)
            if receipt and receipt["input_hash"] != expected_input_hash:
                self.receipts.pop(stage, None)
                return True
            return False

        def delete_downstream_stage_receipts(
            self, _task_id, _item_id, stages, **_kwargs
        ):
            for stage in stages:
                self.receipts.pop(stage, None)
            return 0

    service.repository = ReceiptRepository()
    text_calls = 0
    grid_calls = 0

    def text(*args, **kwargs):
        nonlocal text_calls
        text_calls += 1
        if text_calls == 1:
            raise DoubaoTextError(
                "temporary text failure",
                error_kind="transient",
                retryable=True,
                attempt_count=3,
            )
        return {
            "title": "Insulated Stainless Steel Travel Mug, 500 ml Portable Drink Cup",
            "description": VALID_DESCRIPTION,
            "variant_translations": {},
            "vision_subject": "travel mug",
            "vision_preliminary_title": "",
            "product_dimensions": {},
        }

    def grid(*args, **kwargs):
        nonlocal grid_calls
        grid_calls += 1
        return GridImageOutput(
            carousel_urls=tuple(
                f"https://example.com/grid-{index}.jpg" for index in range(4)
            ),
            summary_url="https://example.com/grid-summary.jpg",
            attempt_count=1,
            provider_status_class="success",
        )

    monkeypatch.setattr(service, "_generate_doubao_text", text)
    monkeypatch.setattr(service, "_generate_grid_images", grid)

    first = service._process_one(
        {"id": 1, "item_id": 101}, _draft(), _settings(), False, task_id=12
    )
    assert "images" in service.repository.receipts
    first_image_hash = service.repository.receipts["images"]["input_hash"]
    second = service._process_one(
        {"id": 1, "item_id": 101}, _draft(), _settings(), False, task_id=12
    )

    assert first["status"] == "attention_required"
    assert second["status"] == "completed"
    assert text_calls == 2
    assert service.repository.receipts["images"]["input_hash"] == first_image_hash
    assert grid_calls == 1
    assert second["result"]["provider_attempts"]["four_grid"] == 0
    assert second["result"]["provider_status_classes"]["four_grid"] == "receipt_hit"


def _draft() -> dict:
    return {
        "id": 7,
        "status": "processing",
        "title": "500 ml Stainless Steel Travel Mug",
        "image_url": "https://example.com/source.jpg",
        "raw_payload": _raw(),
    }


def _settings() -> dict:
    return {
        "processing_scope": ["title", "details", "four_grid"],
        "title_optimize": True,
        "ai_media_opt_in": True,
        "image_template": "B",
        "target_language": "en",
        "target_site": "US",
    }


def test_process_success_exposes_five_points_grid_attempts_and_stage_timings(monkeypatch) -> None:
    service = _process_service(monkeypatch)
    monkeypatch.setattr(service_module, "_ai_enabled", lambda: True)
    monkeypatch.setattr(
        service,
        "_generate_doubao_text",
        lambda *args, **kwargs: {
            "title": "Insulated Stainless Steel Travel Mug, 500 ml Portable Drink Cup",
            "description": VALID_DESCRIPTION,
            "variant_translations": {},
        },
    )
    monkeypatch.setattr(
        service,
        "_generate_grid_images",
        lambda *args, **kwargs: GridImageOutput(
            carousel_urls=tuple(f"https://example.com/grid-{index}.jpg" for index in range(4)),
            summary_url="https://example.com/grid-summary.jpg",
            attempt_count=1,
            provider_status_class="success",
            stage_timings_ms={
                "grid_generation_ms": 12,
                "grid_validation_ms": 3,
                "publish_ms": 2,
            },
        ),
    )

    result = service._process_one({"id": 1}, _draft(), _settings(), False, task_id=12)

    assert result["status"] == "completed"
    assert result["result"]["description"].count("\n") == 4
    assert len(result["result"]["carousel_image_paths"]) == 4
    assert result["result"]["provider_attempts"] == {
        "doubao_vision": 1,
        "doubao_text": 1,
        "four_grid": 1,
    }
    assert result["result"]["provider_status_classes"]["four_grid"] == "success"
    assert result["result"]["stage_timings_ms"]["grid_generation_ms"] == 12
    assert result["result"]["stage_timings_ms"]["total_processing_ms"] >= 0


def test_image_only_scope_does_not_validate_or_generate_listing_text(monkeypatch) -> None:
    service = _process_service(monkeypatch)
    draft = _draft()
    draft["title"] = "不锈钢保温杯"
    monkeypatch.setattr(
        service,
        "_generate_doubao_text",
        lambda *args, **kwargs: pytest.fail("image-only scope must not call text"),
    )
    monkeypatch.setattr(
        service,
        "_generate_grid_images",
        lambda *args, **kwargs: GridImageOutput(
            carousel_urls=tuple(
                f"https://example.com/grid-{index}.jpg" for index in range(4)
            ),
            attempt_count=1,
            provider_status_class="success",
        ),
    )
    settings = {**_settings(), "processing_scope": ["four_grid"]}

    result = service._process_one({"id": 1}, draft, settings, False, task_id=12)

    assert result["status"] == "completed"
    assert result["result"]["optimized_title"] == "不锈钢保温杯"
    assert result["result"]["text_generation"]["status"] == "not_requested"


def test_deterministic_dimensions_remain_instance_callable() -> None:
    service = object.__new__(ProductProcessingService)

    result = service._extract_deterministic_size(
        {"source_attributes": {"包装尺寸": "15*10*4cm", "重量": "180g"}}
    )

    assert result is not None
    assert result["length_cm"] == 15
    assert result["width_cm"] == 10
    assert result["height_cm"] == 4
    assert result["weight_g"] == 180


def test_detail_generation_keeps_original_main_before_1688_detail_images(
    monkeypatch,
) -> None:
    service = _process_service(monkeypatch)
    captured: dict[str, list[str]] = {}
    draft = _draft()
    draft["raw_payload"] = {
        **draft["raw_payload"],
        "source_detail_image_urls": ["https://example.com/detail-source.jpg"],
    }
    monkeypatch.setattr(
        service,
        "_generate_doubao_text",
        lambda *args, **kwargs: {
            "title": "Insulated Stainless Steel Travel Mug, 500 ml Portable Drink Cup",
            "description": VALID_DESCRIPTION,
            "variant_translations": {},
            "product_dimensions": {},
        },
    )
    def detail(*args, **_kwargs):
        captured["references"] = args[5]
        return ["https://example.com/generated-detail.jpg"]

    monkeypatch.setattr(service, "_generate_detail_images", detail)
    settings = {
        **_settings(),
        "processing_scope": ["title", "details", "detail_images"],
    }

    result = service._process_one({"id": 1}, draft, settings, False, task_id=12)

    assert result["status"] == "completed"
    assert captured["references"] == [
        "https://example.com/source.jpg",
        "https://example.com/detail-source.jpg",
    ]


def test_empty_detail_image_result_is_retryable_failure(monkeypatch) -> None:
    service = _process_service(monkeypatch)
    monkeypatch.setattr(service, "_generate_detail_images", lambda *args, **kwargs: [])
    settings = {
        **_settings(),
        "processing_scope": ["title", "details", "detail_images"],
    }

    result = service._process_one({"id": 1}, _draft(), settings, False, task_id=12)

    assert result["status"] == "failed"
    assert result["result"]["error_type"] == "detail_images_incomplete"
    assert result["result"]["retryable"] is True


def test_images_receipt_hash_changes_with_real_image_prompt_key() -> None:
    service = object.__new__(ProductProcessingService)

    class _MutablePromptRepository:
        values = {"grid_image": "grid-v1"}

        @classmethod
        def prompts(cls):
            return dict(cls.values)

    service.repository = _MutablePromptRepository()
    first = service._processing_stage_input_hash("images", {"source": "same"})
    _MutablePromptRepository.values["grid_image"] = "grid-v2"
    second = service._processing_stage_input_hash("images", {"source": "same"})

    assert first != second


def test_branch_receipt_inputs_track_real_dependencies_without_cross_scope(monkeypatch) -> None:
    service = _process_service(monkeypatch)
    service.repository = _ReceiptRepository()
    captured: dict[str, dict] = {}
    original_hash = service._processing_stage_input_hash

    def capture(stage, input_data):
        captured[stage] = input_data
        return original_hash(stage, input_data)

    monkeypatch.setattr(service, "_processing_stage_input_hash", capture)
    monkeypatch.setattr(
        service,
        "_generate_grid_images",
        lambda *args, **kwargs: GridImageOutput(
            carousel_urls=tuple(
                f"https://example.com/grid-{index}.jpg" for index in range(4)
            ),
            attempt_count=1,
            provider_status_class="success",
        ),
    )
    draft = _draft()
    draft["raw_payload"] = {
        **draft["raw_payload"],
        "description": "Source description changes the real image prompt context.",
    }

    result = service._process_one(
        {"id": 1, "item_id": 101}, draft, _settings(), False, task_id=12
    )

    assert result["status"] == "completed"
    assert captured["doubao_text"]["scope"] == ["details", "title"]
    assert "four_grid" not in captured["doubao_text"]["scope"]
    assert (
        captured["images"]["source_facts"]["description"]
        == "Source description changes the real image prompt context."
    )


def test_process_grid_quality_failure_is_retryable_and_never_marks_completed(monkeypatch) -> None:
    """四宫格失败必须明确失败，不能用零轮播结果伪装完成。"""
    service = _process_service(monkeypatch)
    monkeypatch.setattr(service_module, "_ai_enabled", lambda: True)
    monkeypatch.setattr(
        service,
        "_generate_doubao_text",
        lambda *args, **kwargs: {
            "title": "Insulated Stainless Steel Travel Mug, 500 ml Portable Drink Cup",
            "description": VALID_DESCRIPTION,
            "variant_translations": {},
        },
    )
    monkeypatch.setattr(
        service,
        "_generate_grid_images",
        lambda *args, **kwargs: GridImageOutput(
            rejected_image_paths=("/tmp/provider-original.png",)
        ),  # 空轮播 + 原图留存 = 质量门/拆图失败
    )

    result = service._process_one({"id": 1}, _draft(), _settings(), False, task_id=12)

    assert result["status"] == "failed"
    assert result["result"]["error_type"] == "image_grid_incomplete"
    assert result["result"]["failure_class"] == "technical_retryable"
    assert result["result"]["retryable"] is True
    assert result["result"]["optimized_title"].startswith(
        "Insulated Stainless Steel Travel Mug"
    )
    assert result["result"]["description"] == VALID_DESCRIPTION
    assert result["result"]["text_generation"]["status"] == "success"
    assert result["result"]["rejected_image_paths"] == ["/tmp/provider-original.png"]
    assert "force_import_acknowledged" not in "|".join(result["result"]["ai_notes"])


def test_image_failure_preserves_successful_doubao_text_receipt(monkeypatch) -> None:
    service = _process_service(monkeypatch)
    service.repository = _ReceiptRepository()
    monkeypatch.setattr(
        service,
        "_generate_grid_images",
        lambda *args, **kwargs: GridImageOutput(
            carousel_urls=(),
            attempt_count=1,
            provider_status_class="gateway_unavailable",
        ),
    )

    result = service._process_one(
        {"id": 1, "item_id": 101}, _draft(), _settings(), False, task_id=12
    )

    assert result["status"] == "failed"
    assert result["reason"] == "商品图片生成失败"
    assert result["result"]["error_type"] == "image_grid_incomplete"
    assert "本地质量门" in result["result"]["debug_hint"]
    assert "doubao_text" in service.repository.receipts
    assert service.repository.receipts["doubao_text"]["output"]["title"].startswith(
        "Insulated Stainless Steel Travel Mug"
    )
    assert "images" not in service.repository.receipts


def test_process_force_import_enables_explicit_grid_quality_override(monkeypatch) -> None:
    service = _process_service(monkeypatch)
    captured: dict[str, object] = {}
    monkeypatch.setattr(service_module, "_ai_enabled", lambda: True)
    monkeypatch.setattr(
        service,
        "_generate_doubao_text",
        lambda *args, **kwargs: {
            "title": "Insulated Stainless Steel Travel Mug, 500 ml Portable Drink Cup",
            "description": VALID_DESCRIPTION,
            "variant_translations": {},
        },
    )
    def generate_grid(*_args, **kwargs):
        captured["allow_quality_override"] = kwargs.get("allow_quality_override")
        return GridImageOutput(
            carousel_urls=tuple(f"https://example.com/forced-{index}.jpg" for index in range(4)),
            attempt_count=1,
            provider_status_class="quality_override",
        )

    monkeypatch.setattr(service, "_generate_grid_images", generate_grid)

    settings = {**_settings(), "force_import_draft_ids": [7]}
    result = service._process_one({"id": 1}, _draft(), settings, False, task_id=12)

    assert captured["allow_quality_override"] is True
    assert result["status"] == "completed"
    assert len(result["result"]["carousel_image_paths"]) == 4
    assert result["result"]["provider_status_classes"]["four_grid"] == "quality_override"
