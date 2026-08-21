"""Host-independent orchestration for the daily-selection HTTP boundary."""

from __future__ import annotations

import ipaddress
import socket
import threading
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import unquote, urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .budget import TaskApiBudget
from .collector import DailySelectionCollector, DailySelectionProvider
from .criteria import DailySelectionCriteria
from .empty_collection import (
    EmptyCollectionRetryRunner,
    empty_collection_retry_state,
)
from .filtering import filter_and_score_candidates
from .repository import (
    DailySelectionFeedback,
    DailySelectionRepository,
    DailySelectionRun,
    DailySelectionRunSummary,
)
from .handoff import DailySelectionHandoff
from .link_collection import canonical_1688_offer_url, detail_seed
from .sku_repull import SkuRepullRunner, empty_repull_state, incomplete_candidates


class DailySelectionActor(BaseModel):
    """The minimum authenticated host context needed by this module."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    actor_id: str = Field(min_length=1)
    workspace_id: str = Field(min_length=1)

    @field_validator("actor_id", "workspace_id", mode="before")
    @classmethod
    def _required_text(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value


@dataclass(frozen=True)
class CachedDailySelectionImage:
    """Bytes returned by an injected safe image cache/fetch adapter."""

    content: bytes
    media_type: str
    final_url: str
    resolved_address: str | None = None


class DailySelectionImageAccessDenied(PermissionError):
    """Raised for unrecorded or unsafe image targets."""


class DailySelectionImageNotFound(LookupError):
    """Raised when a requested URL is not part of an owned run snapshot."""


class DailySelectionImageCache(Protocol):
    """Host adapter that validates every network target before connecting.

    Implementations must invoke ``validate_target`` for the initial resolved
    address and again for every redirect target before opening that connection.
    """

    def get_or_fetch(
        self,
        *,
        workspace_id: str,
        url: str,
        validate_target: Callable[[str, str | None], None],
    ) -> CachedDailySelectionImage: ...


class ProviderConfigResolver(Protocol):
    def __call__(self, actor: DailySelectionActor) -> Mapping[str, Any]: ...


class ProviderFactory(Protocol):
    def __call__(self, config: Mapping[str, Any]) -> DailySelectionProvider: ...


class DailySelectionHandoffConsumer(Protocol):
    """Host-owned bridge from an acknowledged candidate to product drafts."""

    def __call__(self, handoffs: tuple[DailySelectionHandoff, ...]) -> Mapping[str, Any]: ...


class DailySelectionProviderUnavailable(RuntimeError):
    """Configuration failed before an upstream collection call could begin."""


class RunIdFactory(Protocol):
    def __call__(self) -> str: ...


class CollectionProgressCallback(Protocol):
    def __call__(
        self,
        stage: str,
        progress: int,
        completed: int,
        total: int,
        message: str,
    ) -> None: ...


class DailySelectionService:
    """The sole orchestration entry point used by FastAPI routes."""

    def __init__(
        self,
        *,
        repository: DailySelectionRepository,
        budget: Any,
        provider_config_resolver: ProviderConfigResolver,
        provider_factory: ProviderFactory,
        image_cache: DailySelectionImageCache | None = None,
        run_id_factory: RunIdFactory | None = None,
    ) -> None:
        self._repository = repository
        self._budget = budget
        self._provider_config_resolver = provider_config_resolver
        self._provider_factory = provider_factory
        self._image_cache = image_cache
        self._run_id_factory = run_id_factory or (lambda: str(uuid.uuid4()))
        self._sku_repull_runner = SkuRepullRunner(
            repository=repository,
            provider_config_resolver=provider_config_resolver,
            provider_factory=provider_factory,
        )
        self._empty_collection_retry_runner = EmptyCollectionRetryRunner(
            repository=repository,
            budget=budget,
            provider_config_resolver=provider_config_resolver,
            provider_factory=provider_factory,
        )

    @classmethod
    def from_database_path(
        cls,
        *,
        database_path: str | Path,
        provider_config_resolver: ProviderConfigResolver,
        provider_factory: ProviderFactory,
        image_cache: DailySelectionImageCache | None = None,
        run_id_factory: RunIdFactory | None = None,
    ) -> "DailySelectionService":
        """Construct the two existing SQLite owners without changing schemas."""
        return cls(
            repository=DailySelectionRepository(database_path),
            budget=TaskApiBudget(),
            provider_config_resolver=provider_config_resolver,
            provider_factory=provider_factory,
            image_cache=image_cache,
            run_id_factory=run_id_factory,
        )

    def preview(
        self,
        *,
        actor: DailySelectionActor,
        request: Mapping[str, Any],
        progress_callback: CollectionProgressCallback | None = None,
        cancel_event: threading.Event | None = None,
    ) -> DailySelectionRun:
        _report_progress(progress_callback, "preparing", 1, 0, 1, "正在准备采集")
        criteria = DailySelectionCriteria.model_validate(dict(request))
        provider_config = self._provider_config_resolver(actor)
        if not isinstance(provider_config, Mapping):
            raise TypeError("provider config resolver must return a mapping")
        provider = self._build_provider(provider_config)
        _report_progress(progress_callback, "preparing", 4, 1, 1, "采集服务已就绪")
        collected = DailySelectionCollector(
            workspace_id=actor.workspace_id,
            provider=provider,
            budget=self._budget,
            provider_credentials=provider_config,
            progress_callback=lambda stage, completed, total: _report_collection_progress(
                progress_callback, stage, completed, total
            ),
            cancel_event=cancel_event,
        ).collect(criteria)
        _report_progress(progress_callback, "filtering", 92, 0, 1, "正在筛选候选商品")
        filtered = filter_and_score_candidates(
            tuple(item.candidate for item in collected.candidates), criteria
        )
        public_candidates = (
            *filtered.candidates[: criteria.target_count],
            *filtered.filtered,
        )
        _report_progress(progress_callback, "saving", 97, 0, 1, "正在保存采集结果")
        run = self._repository.save_run(
            workspace_id=actor.workspace_id,
            run_id=self._run_id_factory(),
            status=collected.status,
            candidates=public_candidates,
            criteria=criteria,
            metadata=_collection_metadata(collected),
        )
        _report_progress(progress_callback, "saving", 99, 1, 1, "采集结果已保存")
        return run

    def preview_from_1688_link(
        self, *, actor: DailySelectionActor, request: Mapping[str, Any]
    ) -> DailySelectionRun:
        """Use a 1688 product detail as the seed for image-first similar search."""
        request_data = dict(request)
        source_url = request_data.pop("source_url", request_data.pop("product_url", None))
        canonical_url, offer_id = canonical_1688_offer_url(source_url)
        # Link collection derives its actual search seed from item_get.  Ignore
        # any stale front-end mode/image values while retaining filter settings.
        request_data.pop("collection_mode", None)
        request_data.pop("reference_image_url", None)
        request_data.setdefault("keywords", ("1688 similar products",))
        criteria = DailySelectionCriteria.model_validate(request_data)
        provider_config = self._provider_config_resolver(actor)
        if not isinstance(provider_config, Mapping):
            raise TypeError("provider config resolver must return a mapping")
        provider = self._build_provider(provider_config)
        detail = provider.get_item_detail(offer_id)
        if not detail.ok:
            message = detail.error.message if detail.error is not None else "1688 item detail lookup failed"
            raise ValueError(message)
        title, image_url = detail_seed(detail.response)
        seed_criteria = criteria.model_copy(
            update={
                "collection_mode": "image" if image_url else "keyword",
                "reference_image_url": image_url,
                "keywords": (title,),
            }
        )
        collected = DailySelectionCollector(
            workspace_id=actor.workspace_id,
            provider=provider,
            budget=self._budget,
            provider_credentials=provider_config,
        ).collect(seed_criteria)
        filtered = filter_and_score_candidates(
            tuple(item.candidate for item in collected.candidates), seed_criteria
        )
        public_candidates = (
            *filtered.candidates[: seed_criteria.target_count],
            *filtered.filtered,
        )
        metadata = dict(_collection_metadata(collected))
        metadata["source_link"] = {
            "platform": "1688",
            "source_url": canonical_url,
            "offer_id": offer_id,
            "seed_title": title,
            "seed_image_used": image_url is not None,
            "detail_evidence": [item.model_dump(mode="json") for item in detail.audits],
        }
        return self._repository.save_run(
            workspace_id=actor.workspace_id,
            run_id=self._run_id_factory(),
            status=collected.status,
            candidates=public_candidates,
            criteria=seed_criteria,
            metadata=metadata,
        )

    def list_runs(
        self, *, actor: DailySelectionActor, limit: int = 20, offset: int = 0
    ) -> tuple[DailySelectionRunSummary, ...]:
        return self._repository.list_runs(
            workspace_id=actor.workspace_id, limit=limit, offset=offset
        )

    def get_run(
        self, *, actor: DailySelectionActor, run_id: str
    ) -> DailySelectionRun:
        return self._repository.get_run(
            workspace_id=actor.workspace_id, run_id=run_id
        )

    def start_sku_repull(
        self, *, actor: DailySelectionActor, run_id: str
    ) -> Mapping[str, Any]:
        """Start (or resume observing) a background SKU re-pull round."""
        run = self.get_run(actor=actor, run_id=run_id)
        current = self._sku_repull_runner.state(actor=actor, run=run)
        if current.get("status") == "running":
            # 已有轮次在跑，只观察不重复启动（内存 job 优先，避免与
            # 持久化元数据存在写入窗口时误判为已完成而重复开轮）。
            return current
        previous_round = current.get("round", 0) if current.get("status") != "idle" else 0
        targets = incomplete_candidates(run)
        return self._sku_repull_runner.start(
            actor=actor,
            run=run,
            targets=targets,
            previous_round=previous_round,
        )

    def auto_start_sku_repull(
        self, *, actor: DailySelectionActor, run_id: str
    ) -> Mapping[str, Any]:
        """Automatically start the first re-pull round after collection.

        只在从未执行过补齐（idle）时自动启动第一轮；后续轮次由用户手动触发，
        避免对长期失败的候选重复消耗 API 调用。无未读全候选时直接返回空状态。
        运行器内存状态优先，避免与持久化元数据存在写入窗口时重复启动同轮。
        """
        run = self.get_run(actor=actor, run_id=run_id)
        current = self._sku_repull_runner.state(actor=actor, run=run)
        if current.get("status") != "idle":
            return current
        previous_metadata = run.metadata.get("sku_repull")
        if isinstance(previous_metadata, Mapping) and previous_metadata.get("status") not in (
            None,
            "idle",
        ):
            return dict(previous_metadata)
        if not incomplete_candidates(run):
            return empty_repull_state()
        previous_round = (
            int(previous_metadata.get("round")) if isinstance(previous_metadata, Mapping) else 0
        )
        return self._sku_repull_runner.start(
            actor=actor,
            run=run,
            targets=incomplete_candidates(run),
            previous_round=previous_round,
        )

    def auto_retry_empty_collection(
        self, *, actor: DailySelectionActor, run_id: str
    ) -> Mapping[str, Any]:
        """Automatically re-run collection when a batch collected zero candidates.

        采集接口偶尔因上游波动返回空结果；这里在后台按同一 criteria 自动
        重采最多 WH_DAILY_SELECTION_COLLECT_RETRIES 轮（默认 2），一旦采到
        候选即原位替换该批次，保证不因一次接口波动导致整个批次为空。
        """
        run = self.get_run(actor=actor, run_id=run_id)
        return self._empty_collection_retry_runner.maybe_start(actor=actor, run=run)

    def get_collection_retry_state(
        self, *, actor: DailySelectionActor, run_id: str
    ) -> Mapping[str, Any]:
        run = self.get_run(actor=actor, run_id=run_id)
        return self._empty_collection_retry_runner.state(actor=actor, run=run)

    def get_sku_repull_state(
        self, *, actor: DailySelectionActor, run_id: str
    ) -> Mapping[str, Any]:
        run = self.get_run(actor=actor, run_id=run_id)
        return self._sku_repull_runner.state(actor=actor, run=run)

    def cancel_sku_repull(
        self, *, actor: DailySelectionActor, run_id: str
    ) -> Mapping[str, Any]:
        run = self.get_run(actor=actor, run_id=run_id)
        return self._sku_repull_runner.cancel(actor=actor, run=run)

    def record_feedback(
        self,
        *,
        actor: DailySelectionActor,
        run_id: str,
        candidate_id: str,
        reason: str,
        details: Mapping[str, Any] | None = None,
    ) -> DailySelectionFeedback:
        return self._repository.record_feedback(
            workspace_id=actor.workspace_id,
            run_id=run_id,
            candidate_id=candidate_id,
            reason=reason,
            details=details,
        )

    def confirm_candidates(
        self,
        *,
        actor: DailySelectionActor,
        run_id: str,
        candidate_ids: tuple[str, ...],
    ) -> tuple[DailySelectionHandoff, ...]:
        return self._repository.confirm_candidates(
            workspace_id=actor.workspace_id,
            run_id=run_id,
            candidate_ids=candidate_ids,
        )

    def mark_handoffs_consumed(
        self, *, actor: DailySelectionActor, handoffs: tuple[DailySelectionHandoff, ...]
    ) -> tuple[DailySelectionHandoff, ...]:
        return self._repository.mark_handoffs_consumed(
            workspace_id=actor.workspace_id,
            handoff_ids=(handoff.handoff_id for handoff in handoffs),
        )

    def _build_provider(self, config: Mapping[str, Any]) -> DailySelectionProvider:
        try:
            return self._provider_factory(config)
        except (TypeError, ValueError) as error:
            # Provider constructors may include key/secret names in validation
            # messages.  Keep those operational details out of HTTP responses.
            raise DailySelectionProviderUnavailable(
                "1688 collection provider is not configured"
            ) from error

    def get_image(
        self, *, actor: DailySelectionActor, run_id: str, url: str
    ) -> CachedDailySelectionImage:
        run = self.get_run(actor=actor, run_id=run_id)
        if url not in _recorded_image_urls(run):
            raise DailySelectionImageNotFound("daily-selection image not found")
        validate_public_image_target(url, None)
        if self._image_cache is None:
            raise DailySelectionImageNotFound("daily-selection image cache is unavailable")
        image = self._image_cache.get_or_fetch(
            workspace_id=actor.workspace_id,
            url=url,
            validate_target=validate_public_image_target,
        )
        validate_public_image_target(image.final_url, image.resolved_address)
        if not isinstance(image.content, bytes) or not image.content:
            raise DailySelectionImageNotFound("daily-selection image is empty")
        if not image.media_type.casefold().startswith("image/"):
            raise DailySelectionImageAccessDenied("remote content is not an image")
        return image


def validate_public_image_target(url: str, resolved_address: str | None) -> None:
    """Reject non-HTTP, credentialed, local, or non-global image targets."""
    try:
        parsed = urlsplit(url)
        hostname = parsed.hostname
        port = parsed.port
    except (TypeError, ValueError) as error:
        raise DailySelectionImageAccessDenied("invalid image URL") from error
    if parsed.scheme.casefold() not in {"http", "https"} or hostname is None:
        raise DailySelectionImageAccessDenied("image URL must use http or https")
    if parsed.username is not None or parsed.password is not None:
        raise DailySelectionImageAccessDenied("image URL credentials are forbidden")
    if port is not None and not 1 <= port <= 65535:
        raise DailySelectionImageAccessDenied("image URL port is invalid")
    normalized_host = unquote(hostname).strip(".").casefold()
    if (
        not normalized_host
        or "%" in normalized_host
        or normalized_host == "localhost"
        or normalized_host.endswith((".localhost", ".local", ".internal"))
    ):
        raise DailySelectionImageAccessDenied("local image hosts are forbidden")
    _validate_global_address(normalized_host, allow_hostname=True)
    if resolved_address is not None:
        _validate_global_address(resolved_address, allow_hostname=False)


def _validate_global_address(value: str, *, allow_hostname: bool) -> None:
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        try:
            address = ipaddress.ip_address(socket.inet_aton(value))
        except OSError:
            if allow_hostname:
                return
            raise DailySelectionImageAccessDenied("image address is invalid") from None
    if not address.is_global:
        raise DailySelectionImageAccessDenied("private image addresses are forbidden")


def _recorded_image_urls(run: DailySelectionRun) -> frozenset[str]:
    urls: set[str] = set()
    reference = run.criteria.get("reference_image_url")
    if isinstance(reference, str):
        urls.add(reference)
    for candidate in run.candidates:
        if candidate.main_image_url is not None:
            urls.add(candidate.main_image_url)
        urls.update(candidate.source_image_urls)
        urls.update(candidate.source_detail_image_urls)
        urls.update(
            record.image_url
            for record in candidate.source_variant_records
            if record.image_url is not None
        )
    return frozenset(urls)


def _collection_metadata(collected: Any) -> Mapping[str, Any]:
    budget = collected.budget_state
    return {
        "search_calls": collected.search_calls,
        "image_search_calls": collected.image_search_calls,
        "detail_calls": collected.detail_calls,
        "api_calls": collected.api_calls,
        "errors": [error.model_dump(mode="python") for error in collected.errors],
        "detail_errors": {
            offer_id: error.model_dump(mode="python")
            for offer_id, error in collected.detail_errors.items()
        },
        "expansion_rule_version": collected.expansion_rule_version,
        "derived_image_terms": list(collected.derived_image_terms),
        "budget": {
            "shanghai_date": budget.shanghai_date,
            "api_calls_limit": budget.api_calls_limit,
            "api_calls_used": budget.api_calls_used,
            "api_calls_remaining": budget.api_calls_remaining,
        },
    }


def _report_collection_progress(
    callback: CollectionProgressCallback | None,
    stage: str,
    completed: int,
    total: int,
) -> None:
    ratio = 1.0 if total <= 0 else min(1.0, max(0.0, completed / total))
    if stage == "searching":
        progress = 5 + round(20 * ratio)
        message = "正在搜索商品"
    else:
        progress = 25 + round(65 * ratio)
        message = "正在读取商品详情"
    _report_progress(callback, stage, progress, completed, total, message)


def _report_progress(
    callback: CollectionProgressCallback | None,
    stage: str,
    progress: int,
    completed: int,
    total: int,
    message: str,
) -> None:
    if callback is not None:
        callback(stage, progress, completed, total, message)
