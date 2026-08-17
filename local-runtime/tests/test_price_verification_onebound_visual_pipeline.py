from __future__ import annotations

from dataclasses import dataclass
import threading
import time
from typing import Any

from wh_local.data_collection.criteria import DailySelectionCriteria
from wh_local.data_collection.provider import HttpResponse, OneBound1688Provider, ProviderCallResult
from wh_local.price_verification.contracts import PriceVerificationActor
from wh_local.price_verification.plugin.shared_gateway import SharedPluginGateway
from wh_local.price_verification.repository import BatchSelectionRecord, PriceVerificationRepository
from wh_local.price_verification.sourcing.contracts import SourceSearchTask
from wh_local.price_verification.sourcing.onebound_adapter import OneBoundSourceAdapter
from wh_local.price_verification.sourcing.service import SourcingService
from wh_local.price_verification.sourcing import onebound_adapter


@dataclass
class _Result:
    response: dict[str, Any]
    audits: tuple[object, ...] = ()
    error: object | None = None


class _Provider:
    def __init__(self) -> None:
        self.image_target_count = 0
        self.detail_calls = 0

    def search_by_image(self, criteria: object) -> _Result:
        self.image_target_count = int(getattr(criteria, "target_count"))
        items = [
            {
                "num_iid": str(111111 + index),
                "title": "宠物降温冰垫",
                "pic_url": f"https://images.example/search-thumb-{index}.jpg",
                "item_url": f"https://detail.1688.com/offer/{111111 + index}.html",
            }
            for index in range(5)
        ]
        return _Result(
            response={
                "items": {
                    "item": items
                }
            }
        )

    def search_keyword(self, criteria: object) -> _Result:
        return _Result(response={"items": {"item": []}})

    def get_item_detail(self, offer_id: str) -> _Result:
        self.detail_calls += 1
        assert offer_id == "111111"
        return _Result(
            response={
                "item": {
                    "num_iid": "111111",
                    "title": "宠物降温冰垫详情",
                    "pic_url": "https://images.example/detail-gallery.jpg",
                    "price": "8.8",
                }
            }
        )


def test_adapter_recalls_sixty_and_keeps_verified_thumbnail_with_offer(monkeypatch: Any) -> None:
    provider = _Provider()

    def verify(reference_url: str, candidates: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        assert reference_url == "https://images.example/temu.jpg"
        assert len(candidates) == 5
        output = [dict(candidates[0])]
        output[0].update(
            image_similarity_score=0.96,
            image_similarity_method="test",
            image_similarity_verified=True,
        )
        return output, {"reference_available": True, "verified_count": 1, "input_count": 1}

    monkeypatch.setattr(
        "wh_local.price_verification.sourcing.onebound_adapter.verify_visual_candidates",
        verify,
    )
    repository = object.__new__(PriceVerificationRepository)
    adapter = OneBoundSourceAdapter(repository, lambda: provider)
    task = SourceSearchTask(
        task_key="skc-1",
        skc_id="skc-1",
        main_image_url="https://images.example/temu.jpg",
        source_quote_keys=("quote-1",),
        product_title="Pet cooling mat",
        max_candidates=5,
    )

    result = adapter.search_by_image(
        PriceVerificationActor(workspace_id="workspace", actor_id="employee"),
        (task,),
        keyword_search=False,
    )

    candidate = result["items"][0]["candidates"][0]
    assert provider.image_target_count == 60
    assert candidate["num_iid"] == "111111"
    assert candidate["main_image_url"] == "https://images.example/search-thumb-0.jpg"
    assert candidate["image_similarity_score"] == 0.96
    assert len(result["items"][0]["candidates"]) == 5


def test_adapter_defaults_to_image_search_without_title_translation_or_keyword_lookup(monkeypatch: Any) -> None:
    provider = _Provider()
    translated_titles: list[str] = []

    def translate(title: str) -> str:
        translated_titles.append(title)
        return "宠物降温垫"

    def verify(reference_url: str, candidates: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        output = [dict(candidate) for candidate in candidates]
        for candidate in output:
            candidate.update(
                image_similarity_score=0.9,
                image_similarity_method="test",
                image_similarity_verified=True,
                image_similarity_fallback=False,
            )
        return output, {"reference_available": True}

    keyword_calls = 0
    original_keyword_search = provider.search_keyword

    def search_keyword(criteria: object) -> _Result:
        nonlocal keyword_calls
        keyword_calls += 1
        return original_keyword_search(criteria)

    provider.search_keyword = search_keyword  # type: ignore[method-assign]
    monkeypatch.setattr(onebound_adapter, "translate_title_to_chinese", translate)
    monkeypatch.setattr(onebound_adapter, "verify_visual_candidates", verify)
    adapter = OneBoundSourceAdapter(object.__new__(PriceVerificationRepository), lambda: provider)

    result = adapter.search_by_image(
        PriceVerificationActor(workspace_id="workspace", actor_id="employee"),
        (
            SourceSearchTask(
                task_key="skc-default-image-only",
                skc_id="skc-default-image-only",
                main_image_url="https://images.example/temu.jpg",
                source_quote_keys=("quote-default-image-only",),
                product_title="Pet cooling mat",
                max_candidates=5,
            ),
        ),
    )

    assert translated_titles == []
    assert keyword_calls == 0
    assert len(result["items"][0]["candidates"]) == 5


def test_adapter_caps_three_skc_image_search_chains_at_two_workers(monkeypatch: Any) -> None:
    lock = threading.Lock()
    active = 0
    maximum_active = 0
    started_skc_ids: list[str] = []

    class Provider:
        def search_by_image(self, criteria: object) -> _Result:
            nonlocal active, maximum_active
            skc_id = str(getattr(criteria, "reference_image_url")).rsplit("/", 1)[-1]
            with lock:
                active += 1
                maximum_active = max(maximum_active, active)
                started_skc_ids.append(skc_id)
            time.sleep(0.04)
            with lock:
                active -= 1
            return _Result(response={"items": {"item": [{
                "num_iid": f"offer-{skc_id}",
                "title": "宠物降温冰垫",
                "pic_url": f"https://images.example/{skc_id}.jpg",
                "item_url": f"https://detail.1688.com/offer/{skc_id}.html",
                "price": "8.8",
            }]}})

        def get_item_detail(self, offer_id: str) -> _Result:
            raise AssertionError(f"unexpected detail lookup: {offer_id}")

    def verify(reference_url: str, candidates: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        return [], {"reference_available": True}

    monkeypatch.setattr(onebound_adapter, "_recommended_skc_parallelism", lambda: 3)
    monkeypatch.setattr(onebound_adapter, "verify_visual_candidates", verify)
    adapter = OneBoundSourceAdapter(object.__new__(PriceVerificationRepository), Provider)
    tasks = tuple(
        SourceSearchTask(
            task_key=f"skc-{index}",
            skc_id=f"skc-{index}",
            main_image_url=f"https://images.example/{index}",
            source_quote_keys=(f"quote-{index}",),
            product_title="Pet mat",
            max_candidates=1,
        )
        for index in range(3)
    )

    result = adapter.search_by_image(
        PriceVerificationActor(workspace_id="workspace", actor_id="employee"), tasks, keyword_search=False
    )

    assert maximum_active == 2
    assert set(started_skc_ids) == {"0", "1", "2"}
    assert [item["skc_id"] for item in result["items"]] == ["skc-0", "skc-1", "skc-2"]


def test_adapter_records_visual_and_total_elapsed_time(monkeypatch: Any) -> None:
    provider = _Provider()

    def verify(reference_url: str, candidates: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        return [], {"reference_available": True}

    monkeypatch.setattr(onebound_adapter, "verify_visual_candidates", verify)
    adapter = OneBoundSourceAdapter(object.__new__(PriceVerificationRepository), lambda: provider)

    result = adapter.search_by_image(
        PriceVerificationActor(workspace_id="workspace", actor_id="employee"),
        (
            SourceSearchTask(
                task_key="skc-timing",
                skc_id="skc-timing",
                main_image_url="https://images.example/temu.jpg",
                source_quote_keys=("quote-timing",),
                product_title="Pet mat",
                max_candidates=1,
            ),
        ),
        keyword_search=False,
    )

    item = result["items"][0]
    assert isinstance(item["total_elapsed_ms"], int)
    assert item["total_elapsed_ms"] >= 0
    assert isinstance(item["visual_verification"]["elapsed_ms"], int)
    assert item["visual_verification"]["elapsed_ms"] >= 0


def test_title_conflicts_do_not_empty_visual_recall(monkeypatch: Any) -> None:
    provider = _Provider()

    def verify(reference_url: str, candidates: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        assert reference_url == "https://images.example/temu-vest.jpg"
        assert len(candidates) == 5
        # "Cooling" may be absent from Chinese 1688 titles, but every image
        # hit must still reach visual verification.
        assert all("title_evidence_status" in candidate for candidate in candidates)
        output = []
        for index, candidate in enumerate(candidates):
            item = dict(candidate)
            item.update(
                image_similarity_score=0.9 - index / 100,
                image_similarity_method="test",
                image_similarity_verified=True,
                image_similarity_fallback=False,
            )
            output.append(item)
        return output, {
            "reference_available": True,
            "verified_count": 5,
            "input_count": len(candidates),
            "fallback_count": 0,
        }

    monkeypatch.setattr(
        "wh_local.price_verification.sourcing.onebound_adapter.verify_visual_candidates",
        verify,
    )
    repository = object.__new__(PriceVerificationRepository)
    adapter = OneBoundSourceAdapter(repository, lambda: provider)
    task = SourceSearchTask(
        task_key="skc-vest",
        skc_id="55872375182",
        main_image_url="https://images.example/temu-vest.jpg",
        source_quote_keys=("55872375182",),
        product_title="Cute Cartoon Dog Vest, Cooling Breathable Pet Clothing",
        max_candidates=5,
    )

    result = adapter.search_by_image(
        PriceVerificationActor(workspace_id="workspace", actor_id="employee"),
        (task,),
        keyword_search=False,
    )

    item = result["items"][0]
    assert item["visual_verification"]["input_count"] == 5
    assert sum(item["visual_verification"]["title_evidence"].values()) == 5
    assert len(item["candidates"]) == 5


def test_complete_search_payload_skips_redundant_detail_request(monkeypatch: Any) -> None:
    provider = _Provider()
    original_search = provider.search_by_image

    def complete_search(criteria: object) -> _Result:
        result = original_search(criteria)
        for item in result.response["items"]["item"]:
            item["price"] = "6.80"
        return result

    provider.search_by_image = complete_search  # type: ignore[method-assign]

    def verify(reference_url: str, candidates: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        output = []
        for candidate in candidates:
            item = dict(candidate)
            item.update(
                image_similarity_score=0.75,
                image_similarity_method="test",
                image_similarity_verified=True,
                image_similarity_fallback=False,
            )
            output.append(item)
        return output, {"reference_available": True, "verified_count": 5, "input_count": 5}

    monkeypatch.setattr(
        "wh_local.price_verification.sourcing.onebound_adapter.verify_visual_candidates",
        verify,
    )
    repository = object.__new__(PriceVerificationRepository)
    adapter = OneBoundSourceAdapter(repository, lambda: provider)
    task = SourceSearchTask(
        task_key="skc-fast",
        skc_id="skc-fast",
        main_image_url="https://images.example/temu.jpg",
        source_quote_keys=("quote-fast",),
        product_title="Pet mat",
        max_candidates=5,
    )

    result = adapter.search_by_image(
        PriceVerificationActor(workspace_id="workspace", actor_id="employee"),
        (task,),
        keyword_search=False,
    )

    assert len(result["items"][0]["candidates"]) == 5
    assert provider.detail_calls == 0


def test_adapter_passes_provider_reference_bytes_to_visual_verification(monkeypatch: Any) -> None:
    provider = _Provider()
    reference_content = b"reference-image-bytes"
    legacy_calls = 0

    def legacy_search(criteria: object) -> _Result:
        nonlocal legacy_calls
        legacy_calls += 1
        return _Result(response={})

    original_search = provider.search_by_image
    provider.search_by_image = legacy_search  # type: ignore[method-assign]
    provider.search_by_image_with_reference = lambda criteria: (  # type: ignore[attr-defined]
        original_search(criteria),
        reference_content,
    )

    def verify(
        reference_url: str,
        candidates: list[dict[str, Any]],
        *,
        reference_content: bytes,
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        assert reference_url == "https://images.example/temu.jpg"
        assert reference_content == b"reference-image-bytes"
        output = [dict(candidate) for candidate in candidates]
        for item in output:
            item.update(
                image_similarity_score=0.9,
                image_similarity_method="test",
                image_similarity_verified=True,
                image_similarity_fallback=False,
            )
        return output, {"reference_available": True, "reference_reused": True}

    monkeypatch.setattr(
        "wh_local.price_verification.sourcing.onebound_adapter.verify_visual_candidates",
        verify,
    )
    repository = object.__new__(PriceVerificationRepository)
    adapter = OneBoundSourceAdapter(repository, lambda: provider)
    task = SourceSearchTask(
        task_key="skc-reuse",
        skc_id="skc-reuse",
        main_image_url="https://images.example/temu.jpg",
        source_quote_keys=("quote-reuse",),
        product_title="Pet mat",
        max_candidates=5,
    )

    result = adapter.search_by_image(
        PriceVerificationActor(workspace_id="workspace", actor_id="employee"),
        (task,),
        keyword_search=False,
    )

    assert len(result["items"][0]["candidates"]) == 5
    assert legacy_calls == 0
    assert result["items"][0]["visual_verification"]["reference_reused"] is True


def test_production_provider_downloads_reference_once_and_returns_same_bytes() -> None:
    provider = object.__new__(OneBound1688Provider)
    provider._enabled = True
    reference_content = b"safe-fetched-reference"
    download_calls = 0
    uploaded_contents: list[bytes] = []
    searched_result = ProviderCallResult(response={"items": {"item": []}}, audits=())

    def download(reference_url: str) -> tuple[bytes, object, None]:
        nonlocal download_calls
        download_calls += 1
        assert reference_url == "https://images.example/temu.jpg"
        return reference_content, object(), None

    def upload(content: bytes, audit: object) -> ProviderCallResult:
        uploaded_contents.append(content)
        return ProviderCallResult(response={"items": {"imgid": "image-id"}}, audits=())

    provider._download_reference_image = download  # type: ignore[method-assign]
    provider._upload_reference_content = upload  # type: ignore[method-assign]
    provider._search_by_uploaded_image = (  # type: ignore[method-assign]
        lambda criteria, uploaded: searched_result
    )
    criteria = type(
        "Criteria",
        (),
        {
            "collection_mode": "image",
            "reference_image_url": "https://images.example/temu.jpg",
        },
    )()

    result, returned_content = provider.search_by_image_with_reference(criteria)

    assert result is searched_result
    assert download_calls == 1
    assert uploaded_contents == [reference_content]
    assert returned_content is reference_content


def test_provider_audit_records_elapsed_ms_for_a_successful_onebound_call() -> None:
    class Transport:
        def request(self, *args: object, **kwargs: object) -> HttpResponse:
            return HttpResponse(status=200, body=b'{"code": 200, "items": {"item": []}}')

    provider = OneBound1688Provider(
        {
            "api_key": "test-key",
            "api_secret": "test-secret",
            "base_url": "https://api.example.test/1688",
        },
        transport=Transport(),  # type: ignore[arg-type]
    )

    result = provider.search_keyword(
        DailySelectionCriteria(collection_mode="keyword", keywords=("宠物垫",), target_count=1)
    )

    assert isinstance(result.audit.response_summary["elapsed_ms"], int)
    assert result.audit.response_summary["elapsed_ms"] >= 0


def test_provider_audit_records_zero_elapsed_ms_for_a_local_validation_failure() -> None:
    provider = OneBound1688Provider(
        {
            "api_key": "test-key",
            "api_secret": "test-secret",
            "base_url": "https://api.example.test/1688",
        }
    )

    result = provider.get_item_detail("")

    assert result.error is not None
    assert result.audit.response_summary["elapsed_ms"] == 0


def test_batch_sourcing_explicitly_disables_keyword_search(monkeypatch: Any) -> None:
    repository = object.__new__(PriceVerificationRepository)
    selection = BatchSelectionRecord(
        id=1,
        workspace_id="workspace",
        batch_id="batch-1",
        skc_id="skc-1",
        quote_keys=("quote-1",),
        product_title="Pet cooling mat",
        main_image_url="https://images.example/temu.jpg",
        official_link_url="https://temu.example/product/1",
        site="US",
        adjusted_min="10.00",
        max_candidates=5,
        status="retained",
    )
    repository.list_batch_selections = lambda **kwargs: (selection,)  # type: ignore[method-assign]
    repository.get_batch_sourcing_session = lambda **kwargs: None  # type: ignore[method-assign]
    repository.save_batch_sourcing_session = lambda **kwargs: kwargs  # type: ignore[method-assign]
    service = SourcingService(
        repository=repository,
        plugin_gateway=object.__new__(SharedPluginGateway),
    )
    service.prepare_batch_sourcing = lambda *args, **kwargs: {  # type: ignore[method-assign]
        "selected_skc_ids": ("skc-1",),
        "unresolved_skc_ids": ("skc-1",),
        "matched_products": (),
        "selected_candidates": (),
    }
    seen: list[bool] = []

    class Adapter:
        def __init__(self, *args: object) -> None:
            pass

        def search_by_image(
            self, actor: PriceVerificationActor, tasks: tuple[SourceSearchTask, ...], *, keyword_search: bool
        ) -> dict[str, Any]:
            seen.append(keyword_search)
            return {"status": "succeeded", "items": [], "counts": {}}

    monkeypatch.setattr(onebound_adapter, "OneBoundSourceAdapter", Adapter)

    service.search_batch_selections_by_image(
        PriceVerificationActor(workspace_id="workspace", actor_id="employee"),
        batch_id="batch-1",
        provider_factory=lambda: _Provider(),
    )

    assert seen == [False]


def test_adapter_runs_two_skcs_concurrently_and_preserves_input_order(monkeypatch: Any) -> None:
    repository = object.__new__(PriceVerificationRepository)
    adapter = OneBoundSourceAdapter(repository, lambda: _Provider())
    barrier = threading.Barrier(2, timeout=2)
    lock = threading.Lock()
    active = 0
    maximum_active = 0

    def run(task: SourceSearchTask, *, keyword_search: bool) -> dict[str, Any]:
        nonlocal active, maximum_active
        with lock:
            active += 1
            maximum_active = max(maximum_active, active)
        barrier.wait()
        time.sleep(0.01 if task.skc_id == "skc-1" else 0.02)
        with lock:
            active -= 1
        return {
            "task_key": task.task_key,
            "skc_id": task.skc_id,
            "source_quote_keys": list(task.source_quote_keys),
            "status": "succeeded",
            "error": "",
            "candidates": [],
            "evidence": [],
        }

    monkeypatch.setattr(onebound_adapter, "_recommended_skc_parallelism", lambda: 2)
    monkeypatch.setattr(adapter, "_search_task_with_new_provider", run)
    tasks = tuple(
        SourceSearchTask(
            task_key=f"skc-{index}",
            skc_id=f"skc-{index}",
            main_image_url=f"https://images.example/{index}.jpg",
            source_quote_keys=(f"quote-{index}",),
            product_title="Pet mat",
            max_candidates=5,
        )
        for index in (1, 2)
    )

    result = adapter.search_by_image(
        PriceVerificationActor(workspace_id="workspace", actor_id="employee"),
        tasks,
        keyword_search=False,
    )

    assert maximum_active == 2
    assert [item["skc_id"] for item in result["items"]] == ["skc-1", "skc-2"]


def test_low_spec_machine_uses_one_skc_worker(monkeypatch: Any) -> None:
    monkeypatch.setattr(onebound_adapter.os, "cpu_count", lambda: 4)
    monkeypatch.setattr(
        onebound_adapter,
        "_total_physical_memory_bytes",
        lambda: 16 * 1024**3,
    )

    assert onebound_adapter._recommended_skc_parallelism() == 1

    monkeypatch.setattr(onebound_adapter.os, "cpu_count", lambda: 8)
    monkeypatch.setattr(
        onebound_adapter,
        "_total_physical_memory_bytes",
        lambda: 7 * 1024**3,
    )

    assert onebound_adapter._recommended_skc_parallelism() == 1


def test_rate_limit_or_timeout_evidence_requests_serial_fallback() -> None:
    items = [
        {
            "evidence": [
                {"response_summary": {"outcome": "rate_limited"}},
            ]
        }
    ]

    assert onebound_adapter._requires_serial_fallback(items) is True
    assert onebound_adapter._requires_serial_fallback(
        [{"evidence": [{"response_summary": {"outcome": "success"}}]}]
    ) is False
