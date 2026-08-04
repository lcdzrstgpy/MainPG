"""Host-independent orchestration for the daily-selection HTTP boundary."""

from __future__ import annotations

import ipaddress
import socket
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import unquote, urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .budget import SQLiteDailyApiBudget
from .collector import DailySelectionCollector, DailySelectionProvider
from .criteria import DailySelectionCriteria
from .filtering import filter_and_score_candidates
from .repository import (
    DailySelectionFeedback,
    DailySelectionRepository,
    DailySelectionRun,
    DailySelectionRunSummary,
)
from .handoff import DailySelectionHandoff


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
    resolved_address: str


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


class RunIdFactory(Protocol):
    def __call__(self) -> str: ...


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
            budget=SQLiteDailyApiBudget(database_path),
            provider_config_resolver=provider_config_resolver,
            provider_factory=provider_factory,
            image_cache=image_cache,
            run_id_factory=run_id_factory,
        )

    def preview(
        self, *, actor: DailySelectionActor, request: Mapping[str, Any]
    ) -> DailySelectionRun:
        criteria = DailySelectionCriteria.model_validate(dict(request))
        provider_config = self._provider_config_resolver(actor)
        if not isinstance(provider_config, Mapping):
            raise TypeError("provider config resolver must return a mapping")
        provider = self._provider_factory(provider_config)
        collected = DailySelectionCollector(
            workspace_id=actor.workspace_id,
            provider=provider,
            budget=self._budget,
            provider_credentials=provider_config,
        ).collect(criteria)
        filtered = filter_and_score_candidates(
            tuple(item.candidate for item in collected.candidates), criteria
        )
        public_candidates = (
            *filtered.candidates[: criteria.target_count],
            *filtered.filtered,
        )
        return self._repository.save_run(
            workspace_id=actor.workspace_id,
            run_id=self._run_id_factory(),
            status=collected.status,
            candidates=public_candidates,
            criteria=criteria,
            metadata=_collection_metadata(collected),
        )

    def list_runs(
        self, *, actor: DailySelectionActor
    ) -> tuple[DailySelectionRunSummary, ...]:
        return self._repository.list_runs(workspace_id=actor.workspace_id)

    def get_run(
        self, *, actor: DailySelectionActor, run_id: str
    ) -> DailySelectionRun:
        return self._repository.get_run(
            workspace_id=actor.workspace_id, run_id=run_id
        )

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
