from __future__ import annotations

import hashlib
import hmac
import logging
import time
from collections.abc import Callable, Mapping
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlencode, urlsplit, urlunsplit

from wh_local.data_collection.public_image_fetch import (
    FetchedPublicImage,
    PublicImageFetchError,
    fetch_public_image,
)

from .infrastructure.assets import ProductProcessingAssets
from .infrastructure.media_asset_repository import (
    MediaAssetRepository,
    MediaMaterializationConflict,
    media_binding_key,
)
from .infrastructure.preview_image_files import validate_preview_image

logger = logging.getLogger(__name__)

_RETRY_DELAY_SECONDS = 30.0
_TRANSIENT_FETCH_TOKENS = (
    "request failed",
    "request was not successful",
    "cannot be resolved",
    "dns",
    "timed out",
    "timeout",
)


def canonical_source_url(value: str) -> str:
    """Normalize a remote image URL for stable source-identity hashing."""
    text = str(value or "").strip()
    if not text:
        return ""
    try:
        parts = urlsplit(text)
        scheme = parts.scheme.casefold()
        if scheme not in {"http", "https"}:
            return text
        hostname = (parts.hostname or "").strip().rstrip(".")
        try:
            hostname = hostname.encode("idna").decode("ascii").casefold()
        except UnicodeError:
            return text
        netloc = hostname
        default_port = 443 if scheme == "https" else 80
        if parts.port not in {None, default_port}:
            netloc = f"{hostname}:{parts.port}"
        return urlunsplit((scheme, netloc, parts.path or "/", parts.query, ""))
    except ValueError:
        return text


class MediaAssetService:
    """Canonical registration, binding, and lease-based materialization."""

    def __init__(
        self,
        repository: MediaAssetRepository,
        assets: ProductProcessingAssets,
        public_image_fetcher: Callable[[str], FetchedPublicImage] | None = None,
    ):
        self.repository = repository
        self.assets = assets
        self.public_image_fetcher = public_image_fetcher or fetch_public_image

    def register_remote_asset(self, workspace_id: str, source_url: str) -> dict[str, Any]:
        canonical = canonical_source_url(source_url)
        if not canonical:
            raise ValueError("remote asset source url is required")
        identity = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        row = self.repository.register_remote_asset(workspace_id, canonical, identity)
        return self.public_asset(row)

    def register_local_asset(
        self,
        workspace_id: str,
        origin: str,
        content: bytes,
        content_type: str,
    ) -> dict[str, Any]:
        decoded = validate_preview_image(bytes(content), str(content_type or ""))
        path = self.assets.save_media_asset(
            decoded.content,
            decoded.content_hash,
            decoded.suffix,
            workspace_id=workspace_id,
        )
        row = self.repository.register_local_asset(
            workspace_id,
            origin,
            content_hash=decoded.content_hash,
            managed_path=str(path),
            content_type=decoded.content_type,
            byte_size=len(decoded.content),
            width=decoded.width,
            height=decoded.height,
        )
        return self.public_asset(row)

    def bind_asset(
        self,
        *,
        workspace_id: str,
        asset_id: str,
        product_draft_id: int,
        role: str,
        slot_id: str = "",
        sku_id: str = "",
        variant_label: str = "",
        sort_order: int = 0,
        task_id: int = 0,
        task_item_id: int = 0,
    ) -> dict[str, Any]:
        asset = self.repository.get_asset(asset_id, workspace_id)
        if asset is None:
            raise LookupError("media asset not found")
        source_identity = str(
            asset.get("source_identity_hash")
            or asset.get("content_hash")
            or asset.get("id")
            or ""
        )
        binding_key = media_binding_key(
            product_draft_id,
            role,
            slot_id,
            sku_id,
            variant_label,
            source_identity,
            sort_order,
        )
        return self.repository.create_binding(
            workspace_id=workspace_id,
            asset_id=asset_id,
            product_draft_id=product_draft_id,
            task_id=task_id,
            task_item_id=task_item_id,
            role=role,
            slot_id=slot_id,
            sku_id=sku_id,
            variant_label=variant_label,
            sort_order=sort_order,
            binding_key=binding_key,
        )

    def get_asset(self, asset_id: str, workspace_id: str) -> dict[str, Any] | None:
        return self.repository.get_asset(asset_id, workspace_id)

    def list_bindings(
        self,
        workspace_id: str,
        *,
        product_draft_id: int | None = None,
        active_only: bool = True,
    ) -> list[dict[str, Any]]:
        return self.repository.list_bindings(
            workspace_id,
            product_draft_id=product_draft_id,
            active_only=active_only,
        )

    def read_ready_asset(self, asset_id: str, *, workspace_id: str) -> bytes:
        path, _content_type = self.require_ready_managed_file(asset_id, workspace_id=workspace_id)
        content = path.read_bytes()
        asset = self.repository.get_asset(asset_id, workspace_id)
        if asset and asset.get("content_hash") and hashlib.sha256(content).hexdigest() != asset["content_hash"]:
            raise ValueError("managed media asset hash mismatch")
        return content

    def require_ready_managed_file(self, asset_id: str, *, workspace_id: str) -> tuple[Path, str]:
        """Return a workspace-scoped ready unified asset; never accept a URL or raw path."""
        asset = self.repository.get_asset(asset_id, workspace_id)
        if asset is None or asset.get("status") != "ready" or not asset.get("managed_path"):
            raise LookupError("media asset is not ready")
        path = self.assets.require_workspace_media_asset(
            str(asset["managed_path"]),
            workspace_id=workspace_id,
        )
        return path, str(asset.get("content_type") or "image/jpeg")

    def media_asset_content(
        self,
        asset_id: str,
        *,
        workspace_id: str,
        expires: int,
        signature: str,
    ) -> tuple[Path, str]:
        asset = self.repository.get_asset(asset_id, workspace_id)
        if asset is None or asset.get("status") != "ready" or not asset.get("managed_path"):
            raise LookupError("media asset not found")
        now = int(time.time())
        if int(expires) < now or int(expires) > now + 3 * 3600:
            raise LookupError("media asset link expired")
        expected = self._asset_access_signature(asset, int(expires))
        if not hmac.compare_digest(expected, str(signature or "")):
            raise LookupError("media asset not found")
        path = self.assets.require_workspace_media_asset(
            str(asset["managed_path"]),
            workspace_id=workspace_id,
        )
        return path, str(asset.get("content_type") or "image/jpeg")

    def list_draft_media(self, workspace_id: str, product_draft_id: int) -> dict[str, list[dict[str, Any]]]:
        groups: dict[str, list[dict[str, Any]]] = {
            "main": [],
            "gallery": [],
            "detail": [],
            "sku": [],
            "carousel": [],
            "dimension": [],
        }
        for binding in self.repository.list_bindings(
            workspace_id,
            product_draft_id=product_draft_id,
            active_only=True,
        ):
            asset = self.repository.get_asset(binding["asset_id"], workspace_id)
            if asset is None:
                continue
            role = str(binding.get("role") or "gallery")
            groups.setdefault(role, []).append(self._media_view(binding, asset))
        return groups

    def _media_view(self, binding: Mapping[str, Any], asset: Mapping[str, Any]) -> dict[str, Any]:
        ready = str(asset.get("status") or "") == "ready"
        return {
            "binding_id": str(binding.get("id") or ""),
            "asset_id": str(asset.get("id") or ""),
            "role": str(binding.get("role") or "gallery"),
            "slot_id": str(binding.get("slot_id") or ""),
            "sku_id": str(binding.get("sku_id") or ""),
            "variant_label": str(binding.get("variant_label") or ""),
            "sort_order": int(binding.get("sort_order") or 0),
            "status": str(asset.get("status") or "pending"),
            "preview_url": self._preview_url(asset) if ready else "",
            "width": int(asset.get("width") or 0),
            "height": int(asset.get("height") or 0),
            "content_type": str(asset.get("content_type") or ""),
            "error_code": str(asset.get("error_code") or ""),
            "error_message": self._bounded_error_text(str(asset.get("error_message") or "")),
        }

    def materialize_pending(
        self,
        *,
        workspace_id: str | None = None,
        limit: int = 20,
    ) -> dict[str, int]:
        claimed = self.repository.claim_materialization(workspace_id=workspace_id, limit=limit)
        ready = retryable = failed = 0
        for asset in claimed:
            target_workspace = str(asset["workspace_id"] or "")
            try:
                fetched = self.public_image_fetcher(str(asset["source_url"] or ""))
                decoded = validate_preview_image(
                    bytes(fetched.content),
                    str(getattr(fetched, "media_type", "") or ""),
                )
                path = self.assets.save_media_asset(
                    decoded.content,
                    decoded.content_hash,
                    decoded.suffix,
                    workspace_id=target_workspace,
                )
                self.repository.mark_materialization_succeeded(
                    asset["id"],
                    target_workspace,
                    asset["claim_token"],
                    managed_path=str(path),
                    content_hash=decoded.content_hash,
                    content_type=decoded.content_type,
                    byte_size=len(decoded.content),
                    width=decoded.width,
                    height=decoded.height,
                )
                ready += 1
            except Exception as exc:  # noqa: BLE001 - persist a bounded status per asset
                try:
                    if self._is_transient(exc):
                        retry_at = (datetime.now(timezone.utc) + timedelta(seconds=_RETRY_DELAY_SECONDS)).isoformat()
                        self.repository.mark_materialization_retryable(
                            asset["id"],
                            target_workspace,
                            asset["claim_token"],
                            "materialization_transient",
                            self._bounded_error(exc),
                            retry_at,
                        )
                        retryable += 1
                    else:
                        self.repository.mark_materialization_failed(
                            asset["id"],
                            target_workspace,
                            asset["claim_token"],
                            "materialization_invalid",
                            self._bounded_error(exc),
                        )
                        failed += 1
                except (MediaMaterializationConflict, LookupError):
                    # 该素材的抢占 token / 状态已被其他 worker 变更（并发抢占）或已被删除：
                    # 属业务良性冲突，跳过本次状态回写，交由后续调度重新认领。绝不能把该
                    # 异常向上冒泡，否则会穿透 lifespan 上下文拖垮整个 uvicorn 进程。
                    logger.warning(
                        "media materialization claim changed for asset %s; skip status write",
                        asset["id"],
                    )
        return {"claimed": len(claimed), "ready": ready, "retryable": retryable, "failed": failed}

    def materialize_until_idle(
        self,
        *,
        workspace_id: str | None = None,
        batch_size: int = 20,
    ) -> dict[str, int]:
        """Drain every currently claimable remote asset in bounded claim batches.

        A single batch is intentionally limited so claims remain short-lived.  The
        caller that owns the background worker must, however, continue claiming
        batches until none are left; otherwise assets beyond the first batch are
        permanently left in ``pending`` with zero attempts.
        """
        total = {"claimed": 0, "ready": 0, "retryable": 0, "failed": 0}
        while True:
            batch = self.materialize_pending(
                workspace_id=workspace_id,
                limit=max(1, int(batch_size)),
            )
            for key in total:
                total[key] += int(batch.get(key) or 0)
            if int(batch["claimed"] or 0) < max(1, int(batch_size)):
                return total

    def retry_asset(self, asset_id: str, *, workspace_id: str) -> dict[str, Any]:
        row = self.repository.reset_asset_for_retry(asset_id, workspace_id)
        return self.public_asset(row)

    def public_asset(self, asset: Mapping[str, Any]) -> dict[str, Any]:
        status = str(asset.get("status") or "pending")
        return {
            "id": str(asset.get("id") or ""),
            "origin": str(asset.get("origin") or "remote_source"),
            "status": status,
            "content_type": str(asset.get("content_type") or ""),
            "width": int(asset.get("width") or 0),
            "height": int(asset.get("height") or 0),
            "preview_url": self._preview_url(asset) if status == "ready" else "",
            "error_code": str(asset.get("error_code") or ""),
            "error_message": self._bounded_error_text(str(asset.get("error_message") or "")),
        }

    def _preview_url(self, asset: Mapping[str, Any]) -> str:
        expires = ((int(time.time()) // 3600) + 2) * 3600
        query = urlencode(
            {
                "workspace_id": str(asset.get("workspace_id") or ""),
                "expires": expires,
                "signature": self._asset_access_signature(asset, expires),
            }
        )
        return f"/api/product-processing/media-assets/{str(asset.get('id') or '')}/content?{query}"

    @staticmethod
    def _asset_access_signature(asset: Mapping[str, Any], expires: int) -> str:
        secret = str(asset.get("access_token") or "")
        if not secret:
            return ""
        message = "\n".join(
            (
                str(asset.get("id") or ""),
                str(asset.get("workspace_id") or ""),
                str(int(expires)),
            )
        ).encode("utf-8")
        return hmac.new(secret.encode("utf-8"), message, hashlib.sha256).hexdigest()

    @staticmethod
    def _is_transient(exc: Exception) -> bool:
        if isinstance(exc, (OSError, TimeoutError)):
            return True
        if isinstance(exc, PublicImageFetchError):
            message = str(exc).casefold()
            return any(token in message for token in _TRANSIENT_FETCH_TOKENS)
        return False

    @staticmethod
    def _bounded_error(exc: Exception) -> str:
        return MediaAssetService._bounded_error_text(str(exc) or type(exc).__name__)

    @staticmethod
    def _bounded_error_text(message: str) -> str:
        return message.replace("\r", " ").replace("\n", " ").strip()[:240]
