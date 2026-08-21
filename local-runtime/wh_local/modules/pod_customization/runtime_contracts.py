from __future__ import annotations

from concurrent.futures import Future
from dataclasses import dataclass
from typing import Any, Callable, Protocol

from wh_local.modules.product_processing.infrastructure.media import GeneratedMedia

from .billing_contract import PodExecutionGrant


SUPPORTED_TEMPLATE_IMAGE_CONTENT_TYPES = frozenset({"image/jpeg", "image/png", "image/webp"})


@dataclass(frozen=True)
class PatternGridRequest:
    batch_id: str
    call_kind: str
    call_index: int
    prompt: str
    model_id: str = "gpt-image-2-2k"
    size: str = "2048x2048"


@dataclass(frozen=True)
class SceneOptimizationRequest:
    batch_id: str
    item_id: str
    instruction: str
    prompt: str
    pattern_image: bytes
    fixed_composite_image: bytes
    template_image: bytes
    model_id: str = "gpt-image-2-1k"
    size: str = "1024x1024"


@dataclass(frozen=True)
class DirectListingGridRequest:
    trial_id: str
    template_id: str
    template_image: bytes
    template_content_type: str
    prompt: str
    attempt: int
    model_id: str = "gpt-image-2-2k"
    size: str = "1024x1024"


class PodAiRuntime(Protocol):
    """POD-specific operations hosted by one isolated ``AiRuntime`` instance."""

    def submit(self, function: Callable[..., Any], /, *args: Any, **kwargs: Any) -> Future[Any]: ...

    def generate_pattern_grid(self, request: PatternGridRequest, *, grant: PodExecutionGrant, call_id: str) -> bytes: ...

    def optimize_scene(self, request: SceneOptimizationRequest, *, grant: PodExecutionGrant, call_id: str) -> bytes: ...

    def generate_listing_grid(self, request: DirectListingGridRequest, *, grant: PodExecutionGrant, call_id: str) -> GeneratedMedia: ...

    def split_listing_grid(self, media: GeneratedMedia) -> list[GeneratedMedia]: ...

    def publish_listing_image(self, media: GeneratedMedia, *, namespace: str, role: str) -> str: ...
