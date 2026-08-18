from __future__ import annotations

import json
import threading

import pytest

import wh_local.modules.product_processing.service as service_module
from wh_local.modules.product_processing.ai_client import AiProviderError
from wh_local.modules.product_processing.doubao_vision import (
    DoubaoVisionError,
    SubjectAnalysis,
)
from wh_local.modules.product_processing.service import (
    GridImageOutput,
    ListingTextConfigurationError,
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


class _CombinedClient:
    def __init__(self, response: str | Exception) -> None:
        self.response = response
        self.prompts: list[str] = []
        self.messages: list[list[dict]] = []

    def chat(self, messages: list[dict]) -> str:
        self.messages.append(messages)
        self.prompts.append(messages[0]["content"])
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


def _combined_service(monkeypatch, response: str | Exception) -> tuple[ProductProcessingService, _CombinedClient]:
    service = object.__new__(ProductProcessingService)
    service.repository = _PromptRepository()
    client = _CombinedClient(response)
    monkeypatch.setattr(service_module, "_ai_enabled", lambda: True)
    monkeypatch.setattr(service, "_ai_client", lambda: client)
    monkeypatch.setattr(service, "_load_ai_stage_cache", lambda _stage, _key: None)
    monkeypatch.setattr(service, "_save_ai_stage_cache", lambda *args, **kwargs: None)
    return service, client


def test_combined_call_embeds_operator_description_prompt_and_enforces_five_points(monkeypatch) -> None:
    service, client = _combined_service(
        monkeypatch,
        json.dumps(
            {
                "optimized_title": "Insulated Stainless Steel Travel Mug, 500 ml Portable Drink Cup",
                "description": VALID_DESCRIPTION,
                "variant_translations": [],
            }
        ),
    )

    result = service._generate_combined_text(
        "500 ml Stainless Steel Travel Mug",
        "Kitchen & Dining",
        _raw(),
        "en",
        "US",
        [],
    )

    assert result is not None
    assert result["description"].count("\n") == 4
    assert "CUSTOM OPERATOR DESCRIPTION RULE" in client.prompts[0]


def test_combined_partial_description_is_rejected_when_five_points_are_required(monkeypatch) -> None:
    candidate = "- CUTE KEYCHAINS: Small decorative charms for bags."
    service, _client = _combined_service(
        monkeypatch,
        json.dumps(
            {
                "optimized_title": "Cartoon Character Keychain Set, Colorful Resin Bag Charms with Metal Rings",
                "description": candidate,
                "variant_translations": [],
            }
        ),
    )

    result = service._generate_combined_text(
        "Cartoon Character Keychain Set", "Accessories", _raw(), "en", "US", []
    )

    assert result is not None
    assert result["description"] == ""
    assert result["description_candidate"] == candidate
    assert "exactly five" in result["description_contract_error"]


def test_changing_operator_description_prompt_changes_combined_cache_key(monkeypatch) -> None:
    response = json.dumps(
        {
            "optimized_title": "Insulated Stainless Steel Travel Mug, 500 ml Portable Drink Cup",
            "description": VALID_DESCRIPTION,
            "variant_translations": [],
        }
    )
    service, client = _combined_service(monkeypatch, response)
    cache_keys: list[str] = []

    class _MutableRepository:
        marker = "FIRST DESCRIPTION CONTRACT"

        def prompts(self) -> dict[str, str]:
            return {"desc": f"{self.marker}\nProduct: {{title}}\nOutput exactly five English bullet points."}

    repository = _MutableRepository()
    service.repository = repository
    monkeypatch.setattr(
        service,
        "_load_ai_stage_cache",
        lambda _stage, key: cache_keys.append(key) or None,
    )

    service._generate_combined_text(
        "500 ml Stainless Steel Travel Mug", "Kitchen & Dining", _raw(), "en", "US", []
    )
    repository.marker = "SECOND DESCRIPTION CONTRACT"
    service._generate_combined_text(
        "500 ml Stainless Steel Travel Mug", "Kitchen & Dining", _raw(), "en", "US", []
    )

    assert len(client.prompts) == 2
    assert cache_keys[0] != cache_keys[1]


def test_combined_non_retryable_400_raises_configuration_error(monkeypatch) -> None:
    service, client = _combined_service(
        monkeypatch,
        AiProviderError("AI provider HTTP 400: no routed merchant", status_code=400),
    )

    with pytest.raises(ListingTextConfigurationError, match="no routed merchant"):
        service._generate_combined_text(
            "500 ml Stainless Steel Travel Mug",
            "Kitchen & Dining",
            _raw(),
            "en",
            "US",
            [],
        )
    assert len(client.prompts) == 1


def test_combined_multimodal_call_returns_identity_text_variants_and_dimensions(monkeypatch) -> None:
    raw = {
        **_raw(),
        "source_variant_records": [{"attributes": {"Color": "蓝色"}}],
    }
    service, client = _combined_service(
        monkeypatch,
        json.dumps(
            {
                "sellable_subject": "insulated stainless steel travel mug",
                "preliminary_title": "Blue Stainless Steel Travel Mug",
                "optimized_title": "Insulated Stainless Steel Travel Mug, 500 ml Portable Drink Cup",
                "description": VALID_DESCRIPTION,
                "variant_translations": [{"raw_value": "蓝色", "export_value": "Blue"}],
                "product_dimensions": {
                    "length_cm": 10,
                    "width_cm": 8,
                    "height_cm": 20,
                    "weight_g": 350,
                },
            }
        ),
    )
    monkeypatch.setattr(
        service,
        "_image_to_data_url",
        lambda _url: "data:image/jpeg;base64,dGVzdA==",
    )

    result = service._generate_combined_text(
        "500 ml Stainless Steel Travel Mug",
        "Kitchen & Dining",
        raw,
        "en",
        "US",
        [],
        vision_identity={
            "sellable_subject": "insulated stainless steel travel mug",
            "subject_explanation": "The foreground mug is the complete sellable product.",
            "visible_attributes": ["blue body", "metallic finish"],
            "excluded_elements": ["table"],
            "confidence": "high",
            "uncertainty_reason": "",
        },
        image_url="https://example.com/source.jpg",
        known_dimensions={"weight_g": 300},
        include_dimensions=True,
    )

    assert len(client.messages) == 1
    assert client.messages[0][0]["content"][1]["type"] == "image_url"
    assert "AUTHORITATIVE SUBJECT ANALYSIS FROM THE ORIGINAL 1688 IMAGE:" in (
        client.messages[0][0]["content"][0]["text"]
    )
    assert result["vision_subject"] == "insulated stainless steel travel mug"
    assert result["variant_translations"] == {"蓝色": "Blue"}
    assert result["product_dimensions"]["weight_g"] == 300
    assert result["product_dimensions"]["height_cm"] == 20


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

    service, _client = _combined_service(
        monkeypatch,
        json.dumps(
            {
                "optimized_title": "Insulated Stainless Steel Travel Mug, 500 ml Portable Drink Cup",
                "description": VALID_DESCRIPTION,
                "variant_translations": [],
            }
        ),
    )
    cache_inputs: list[dict] = []
    monkeypatch.setattr(
        service,
        "_ai_stage_cache_key",
        lambda _stage, **kwargs: cache_inputs.append(kwargs["input_data"]) or "cache-key",
    )
    service._generate_combined_text(
        "Travel Mug", "Kitchen & Dining", raw, "en", "US", []
    )

    assert cache_inputs[0]["raw"] == evidence
    assert "stock" not in cache_inputs[0]["raw"]
    assert "source_image_urls" not in cache_inputs[0]["raw"]


def _process_service(monkeypatch) -> ProductProcessingService:
    service = object.__new__(ProductProcessingService)
    service.repository = object()
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
    return service


def test_doubao_subject_is_resolved_before_gpt_and_cannot_be_overridden(monkeypatch) -> None:
    service = _process_service(monkeypatch)
    order: list[str] = []
    captured: dict[str, object] = {}

    def recognize(*_args, **_kwargs) -> SubjectAnalysis:
        order.append("doubao")
        return SubjectAnalysis(
            sellable_subject="rectangular bamboo cooling mat",
            subject_explanation="The foreground woven mat is the complete sellable product.",
            visible_attributes=("rectangular", "woven bamboo surface"),
            excluded_elements=("bed", "pillows"),
            confidence="high",
            uncertainty_reason="",
        )

    def combined(*_args, **kwargs):
        order.append("gpt-text")
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
    monkeypatch.setattr(service, "_generate_combined_text", combined)
    monkeypatch.setattr(service, "_translate_variant_values", lambda *args, **kwargs: {})
    monkeypatch.setattr(service, "_generate_grid_images", grid)

    result = service._process_one({"id": 1}, _draft(), _settings(), False, task_id=12)

    assert result["status"] == "completed"
    assert order == ["doubao", "gpt-text", "gpt-image"]
    assert result["result"]["vision_identity"]["sellable_subject"] == (
        "rectangular bamboo cooling mat"
    )
    assert captured["combined_identity"] == result["result"]["vision_identity"]
    assert captured["grid_identity"] == result["result"]["vision_identity"]


def test_low_confidence_doubao_subject_blocks_all_gpt_calls(monkeypatch) -> None:
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
    monkeypatch.setattr(
        service,
        "_generate_combined_text",
        lambda *_args, **_kwargs: pytest.fail("GPT text must not run"),
    )
    monkeypatch.setattr(
        service,
        "_generate_grid_images",
        lambda *_args, **_kwargs: pytest.fail("GPT image must not run"),
    )

    result = service._process_one({"id": 1}, _draft(), _settings(), False, task_id=12)

    assert result["status"] == "attention_required"
    assert result["result"]["error_type"] == "doubao_subject_low_confidence"
    assert result["result"]["retryable"] is True


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
        "_generate_combined_text",
        lambda *_args, **_kwargs: pytest.fail("GPT text must not run"),
    )
    monkeypatch.setattr(
        service,
        "_generate_grid_images",
        lambda *_args, **_kwargs: pytest.fail("GPT image must not run"),
    )

    result = service._process_one({"id": 1}, _draft(), _settings(), False, task_id=12)

    assert result["status"] == expected_status
    assert result["result"]["error_type"] == "doubao_vision_unavailable"
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
        "_generate_combined_text",
        lambda *args, **kwargs: {
            "title": "Insulated Stainless Steel Travel Mug, 500 ml Portable Drink Cup",
            "description": VALID_DESCRIPTION,
            "variant_translations": {},
            "product_dimensions": {},
        },
    )
    monkeypatch.setattr(service, "_translate_variant_values", lambda *args, **kwargs: {})
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
    second = service._process_one(
        {"id": 1, "item_id": 101}, _draft(), _settings(), False, task_id=12
    )

    assert first["status"] == second["status"] == "completed"
    assert recognition_calls == 1
    assert second["result"]["provider_attempts"]["doubao_vision"] == 0
    assert second["result"]["provider_status_classes"]["doubao_vision"] == "receipt_hit"


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


def test_process_stops_after_combined_400_before_any_image_call(monkeypatch) -> None:
    service = _process_service(monkeypatch)
    monkeypatch.setattr(
        service,
        "_generate_combined_text",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            ListingTextConfigurationError("HTTP 400", status_code=400)
        ),
    )
    monkeypatch.setattr(
        service,
        "_generate_grid_images",
        lambda *args, **kwargs: pytest.fail("image generation must not run after text configuration failure"),
    )

    result = service._process_one({"id": 1}, _draft(), _settings(), False, task_id=12)

    assert result["status"] == "attention_required"
    assert result["result"]["error_type"] == "text_provider_configuration"
    assert "stage_timings_ms" in result["result"]


def test_process_only_rejects_when_no_usable_description_was_generated(monkeypatch) -> None:
    service = _process_service(monkeypatch)
    monkeypatch.setattr(
        service,
        "_generate_combined_text",
        lambda *args, **kwargs: {
            "title": "Insulated Stainless Steel Travel Mug, 500 ml Portable Drink Cup",
            "description": "",
            "variant_translations": {},
        },
    )
    monkeypatch.setattr(service, "_generate_description", lambda *args, **kwargs: "")
    monkeypatch.setattr(
        service,
        "_generate_grid_images",
        lambda *args, **kwargs: pytest.fail("image generation must not run with invalid description"),
    )

    result = service._process_one({"id": 1}, _draft(), _settings(), False, task_id=12)

    assert result["status"] == "attention_required"
    assert result["result"]["error_type"] == "description_content_unavailable"
    assert "Source information preserved" not in json.dumps(result, ensure_ascii=False)


def test_process_sends_failed_combined_candidate_to_the_single_description_repair(monkeypatch) -> None:
    service = _process_service(monkeypatch)
    repair_calls: list[dict] = []
    monkeypatch.setattr(
        service,
        "_generate_combined_text",
        lambda *args, **kwargs: {
            "title": "Cartoon Character Keychain Set, Colorful Resin Bag Charms with Metal Rings",
            "description": "",
            "description_candidate": "产品描述生成失败",
            "description_contract_error": "description must be English only",
            "variant_translations": {},
        },
    )

    def repaired_description(*args, **kwargs) -> str:
        repair_calls.append(kwargs)
        return VALID_DESCRIPTION

    monkeypatch.setattr(service, "_generate_description", repaired_description)
    monkeypatch.setattr(service, "_translate_variant_values", lambda *args, **kwargs: {})
    monkeypatch.setattr(
        service,
        "_generate_grid_images",
        lambda *args, **kwargs: GridImageOutput(
            carousel_urls=tuple(f"https://example.com/grid-{index}.jpg" for index in range(4)),
            summary_url="https://example.com/grid-summary.jpg",
            attempt_count=1,
            provider_status_class="success",
        ),
    )

    result = service._process_one({"id": 1}, _draft(), _settings(), False, task_id=12)

    assert result["status"] == "completed"
    assert repair_calls[0]["prior_description"] == "产品描述生成失败"
    assert repair_calls[0]["contract_error"] == "description must be English only"
    assert "description_contract:repaired" in result["result"]["ai_notes"]


def test_description_repair_prompt_uses_failed_candidate_as_data_only(monkeypatch) -> None:
    service, client = _combined_service(monkeypatch, VALID_DESCRIPTION)

    result = service._generate_description(
        "Cartoon Character Keychain Set, Colorful Resin Bag Charms with Metal Rings",
        "Accessories",
        _raw(),
        "en",
        "US",
        [],
        prior_description="- Cute Keychains: Small decorative charms for bags.",
        contract_error="description must contain exactly five bullet points",
    )

    assert result.count("\n") == 4
    assert "PREVIOUS CANDIDATE" in client.prompts[0]
    assert "16-24 English words" in client.prompts[0]
    assert "untrusted formatting input only" in client.prompts[0]


def test_process_success_exposes_five_points_grid_attempts_and_stage_timings(monkeypatch) -> None:
    service = _process_service(monkeypatch)
    monkeypatch.setattr(service_module, "_ai_enabled", lambda: True)
    monkeypatch.setattr(
        service,
        "_generate_combined_text",
        lambda *args, **kwargs: {
            "title": "Insulated Stainless Steel Travel Mug, 500 ml Portable Drink Cup",
            "description": VALID_DESCRIPTION,
            "variant_translations": {},
        },
    )
    monkeypatch.setattr(service, "_translate_variant_values", lambda *args, **kwargs: {})
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
        "combined_text": 1,
        "four_grid": 1,
    }
    assert result["result"]["provider_status_classes"]["four_grid"] == "success"
    assert result["result"]["stage_timings_ms"]["grid_generation_ms"] == 12
    assert result["result"]["stage_timings_ms"]["total_processing_ms"] >= 0


def test_process_repairs_only_invalid_title_field(monkeypatch) -> None:
    service = _process_service(monkeypatch)
    monkeypatch.setattr(service_module, "_ai_enabled", lambda: True)
    calls = {"combined": 0, "title": 0}

    def combined(*args, **kwargs):
        calls["combined"] += 1
        return {
            "title": "",
            "description": VALID_DESCRIPTION,
            "variant_translations": {},
            "vision_subject": "travel mug",
            "vision_preliminary_title": "Travel Mug",
            "product_dimensions": {},
        }

    def title_repair(*args, **kwargs):
        calls["title"] += 1
        return "Insulated Stainless Steel Travel Mug, 500 ml Portable Drink Cup"

    monkeypatch.setattr(service, "_generate_combined_text", combined)
    monkeypatch.setattr(service, "_generate_title", title_repair)
    monkeypatch.setattr(
        service,
        "_generate_description",
        lambda *args, **kwargs: pytest.fail("valid description must not be regenerated"),
    )
    monkeypatch.setattr(service, "_translate_variant_values", lambda *args, **kwargs: {})
    monkeypatch.setattr(
        service,
        "_generate_grid_images",
        lambda *args, **kwargs: GridImageOutput(
            carousel_urls=tuple(f"https://example.com/grid-{index}.jpg" for index in range(4)),
            summary_url="https://example.com/grid-summary.jpg",
            attempt_count=1,
            provider_status_class="success",
        ),
    )

    result = service._process_one({"id": 1}, _draft(), _settings(), False, task_id=12)

    assert result["status"] == "completed"
    assert calls == {"combined": 1, "title": 1}


def test_title_repair_uses_doubao_subject_when_gpt_returns_no_preliminary_title(
    monkeypatch,
) -> None:
    service = _process_service(monkeypatch)
    captured: dict[str, str] = {}
    monkeypatch.setattr(
        service,
        "_generate_combined_text",
        lambda *args, **kwargs: {
            "title": "",
            "description": VALID_DESCRIPTION,
            "variant_translations": {},
            "vision_subject": "wrong GPT subject",
            "vision_preliminary_title": "",
            "product_dimensions": {},
        },
    )

    def title_repair(*_args, **kwargs) -> str:
        captured["image_derived_title"] = kwargs["image_derived_title"]
        return "Insulated Stainless Steel Travel Mug, 500 ml Portable Drink Cup"

    monkeypatch.setattr(service, "_generate_title", title_repair)
    monkeypatch.setattr(service, "_translate_variant_values", lambda *args, **kwargs: {})
    monkeypatch.setattr(
        service,
        "_generate_grid_images",
        lambda *args, **kwargs: GridImageOutput(
            carousel_urls=tuple(f"https://example.com/grid-{index}.jpg" for index in range(4)),
            attempt_count=1,
            provider_status_class="success",
        ),
    )

    result = service._process_one({"id": 1}, _draft(), _settings(), False, task_id=12)

    assert result["status"] == "completed"
    assert captured["image_derived_title"] == "travel mug"


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
        "_generate_combined_text",
        lambda *args, **kwargs: {
            "title": "Insulated Stainless Steel Travel Mug, 500 ml Portable Drink Cup",
            "description": VALID_DESCRIPTION,
            "variant_translations": {},
            "product_dimensions": {},
        },
    )
    monkeypatch.setattr(service, "_translate_variant_values", lambda *args, **kwargs: {})

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


def test_dimension_repair_runs_in_parallel_with_grid_generation(monkeypatch) -> None:
    service = _process_service(monkeypatch)
    monkeypatch.setattr(service_module, "_ai_enabled", lambda: True)
    grid_started = threading.Event()
    size_finished = threading.Event()
    monkeypatch.setattr(
        service,
        "_generate_combined_text",
        lambda *args, **kwargs: {
            "title": "Insulated Stainless Steel Travel Mug, 500 ml Portable Drink Cup",
            "description": VALID_DESCRIPTION,
            "variant_translations": {},
            "vision_subject": "travel mug",
            "vision_preliminary_title": "Travel Mug",
            "product_dimensions": {},
        },
    )
    monkeypatch.setattr(service, "_translate_variant_values", lambda *args, **kwargs: {})

    def size_repair(*args, **kwargs):
        assert grid_started.wait(1), "grid generation did not start before dimension repair"
        size_finished.set()
        return {"length_cm": 10, "width_cm": 8, "height_cm": 20, "weight_g": 300}

    def grid(*args, **kwargs):
        grid_started.set()
        assert size_finished.wait(1), "dimension repair did not overlap grid generation"
        return GridImageOutput(
            carousel_urls=tuple(f"https://example.com/grid-{index}.jpg" for index in range(4)),
            summary_url="https://example.com/grid-summary.jpg",
            attempt_count=1,
            provider_status_class="success",
        )

    monkeypatch.setattr(service, "_generate_size", size_repair)
    monkeypatch.setattr(service, "_generate_grid_images", grid)
    settings = {**_settings(), "processing_scope": ["title", "details", "product_dimensions", "four_grid"]}

    result = service._process_one({"id": 1}, _draft(), settings, False, task_id=12)

    assert result["status"] == "completed"
    assert result["result"]["product_dimensions"]["weight_g"] == 300


def test_process_grid_quality_failure_is_retryable_and_never_marks_completed(monkeypatch) -> None:
    """四宫格失败必须明确失败，不能用零轮播结果伪装完成。"""
    service = _process_service(monkeypatch)
    monkeypatch.setattr(service_module, "_ai_enabled", lambda: True)
    monkeypatch.setattr(
        service,
        "_generate_combined_text",
        lambda *args, **kwargs: {
            "title": "Insulated Stainless Steel Travel Mug, 500 ml Portable Drink Cup",
            "description": VALID_DESCRIPTION,
            "variant_translations": {},
        },
    )
    monkeypatch.setattr(service, "_translate_variant_values", lambda *args, **kwargs: {})
    monkeypatch.setattr(
        service,
        "_generate_grid_images",
        lambda *args, **kwargs: GridImageOutput(),  # 空输出 = 质量门/拆图失败
    )

    result = service._process_one({"id": 1}, _draft(), _settings(), False, task_id=12)

    assert result["status"] == "failed"
    assert result["result"]["error_type"] == "image_grid_incomplete"
    assert result["result"]["failure_class"] == "technical_retryable"
    assert result["result"]["retryable"] is True
    assert "force_import_acknowledged" not in "|".join(result["result"]["ai_notes"])


def test_process_force_import_cannot_bypass_grid_completeness(monkeypatch) -> None:
    """历史 force-import 参数也不能把零轮播结果标成成功。"""
    service = _process_service(monkeypatch)
    monkeypatch.setattr(service_module, "_ai_enabled", lambda: True)
    monkeypatch.setattr(
        service,
        "_generate_combined_text",
        lambda *args, **kwargs: {
            "title": "Insulated Stainless Steel Travel Mug, 500 ml Portable Drink Cup",
            "description": VALID_DESCRIPTION,
            "variant_translations": {},
        },
    )
    monkeypatch.setattr(service, "_translate_variant_values", lambda *args, **kwargs: {})
    monkeypatch.setattr(
        service,
        "_generate_grid_images",
        lambda *args, **kwargs: GridImageOutput(),  # 空输出 = 质量门失败
    )

    settings = {**_settings(), "force_import_draft_ids": [7]}
    result = service._process_one({"id": 1}, _draft(), settings, False, task_id=12)

    assert result["status"] == "failed"
    assert result["result"]["error_type"] == "image_grid_incomplete"
    assert result["result"]["retryable"] is True
    assert "force_import_acknowledged" not in "|".join(result["result"]["ai_notes"])
