from __future__ import annotations

import hashlib
import hmac
import os
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any
from urllib.parse import urlencode, urlsplit

from wh_local.data_collection.public_image_fetch import FetchedPublicImage

from .domain.policy import is_safe_external_url
from .domain.preview_images import (
    MANIFEST_KEY,
    SLOT_INDEX,
    PreviewImageManifest,
    task_item_result_version,
)
from .domain.workbooks import (
    _dxm_export_rows,
    create_result_workbook,
    require_final_public_image_urls,
)
from .infrastructure.assets import ProductProcessingAssets
from .infrastructure.preview_image_files import validate_preview_image
from .infrastructure.preview_image_repository import (
    PreviewImageRepository,
    PreviewPublicationConflict,
    PreviewSourceNotReady,
)
from .infrastructure.repository import ProductProcessingRepository
from .media_asset_service import MediaAssetService


class PreviewImageService:
    """Stable local image manifests plus deferred, idempotent COS publication."""

    def __init__(
        self,
        repository: PreviewImageRepository,
        product_repository: ProductProcessingRepository,
        assets: ProductProcessingAssets,
        publisher: Callable[[bytes, str, str, str, str], str] | None = None,
        trusted_public_url: Callable[[str], bool] | None = None,
        public_image_fetcher: Callable[[str], FetchedPublicImage] | None = None,
        max_publish_workers: int = 4,
        media_assets: MediaAssetService | None = None,
    ):
        if not 1 <= int(max_publish_workers) <= 6:
            raise ValueError("preview publish workers must be between 1 and 6")
        self.repository = repository
        self.product_repository = product_repository
        self.assets = assets
        self.media_assets = media_assets
        self.publisher = publisher
        self.trusted_public_url = trusted_public_url or (lambda _value: False)
        if public_image_fetcher is None:
            from wh_local.data_collection.public_image_fetch import fetch_public_image

            public_image_fetcher = fetch_public_image
        self.public_image_fetcher = public_image_fetcher
        self.max_publish_workers = int(max_publish_workers)
        self._finalize_worker_lock = threading.Lock()
        self._finalize_workers: dict[tuple[str, str], threading.Thread] = {}

    def require_task_draft(self, task_id: int, product_draft_id: int, workspace_id: str) -> None:
        task = self.product_repository.get_task(int(task_id), str(workspace_id))
        owned = {
            int(item["product_draft_id"])
            for item in (task or {}).get("items", [])
            if item.get("product_draft_id") is not None
        }
        if task is None or int(product_draft_id) not in owned:
            raise LookupError("preview image target does not belong to this task")

    def public_asset(self, asset: Mapping[str, Any]) -> dict[str, Any]:
        media_asset_id = str(asset.get("media_asset_id") or "")
        is_proxy = bool(
            media_asset_id
            and not str(asset.get("managed_path") or "")
            and not str(asset.get("source_url") or "")
        )
        if is_proxy and self.media_assets is not None:
            return self._public_media_backed_asset(asset, media_asset_id)
        return {
            "id": str(asset.get("id") or ""),
            "origin": str(asset.get("origin") or "source"),
            "preview_url": self._preview_url(asset),
            "publication_status": str(asset.get("availability") or "local"),
            "public_url": self._safe_public_value(asset.get("public_url")),
            "width": int(asset.get("width") or 0),
            "height": int(asset.get("height") or 0),
            "bucket": self._bucket_for_preview_origin(str(asset.get("origin") or "")),
            "source_kind": str(asset.get("source_kind") or ""),
            "media_asset_id": media_asset_id,
            "media_status": "",
        }

    def _public_media_backed_asset(
        self,
        asset: Mapping[str, Any],
        media_asset_id: str,
    ) -> dict[str, Any]:
        workspace_id = str(asset.get("workspace_id") or "")
        media_asset = self.media_assets.get_asset(media_asset_id, workspace_id)
        if media_asset is None:
            return {
                "id": str(asset.get("id") or ""),
                "origin": str(asset.get("origin") or "source"),
                "preview_url": "",
                "publication_status": str(asset.get("availability") or "local"),
                "public_url": self._safe_public_value(asset.get("public_url")),
                "width": int(asset.get("width") or 0),
                "height": int(asset.get("height") or 0),
                "bucket": "source" if str(asset.get("source_kind") or "") else "processed",
                "source_kind": str(asset.get("source_kind") or ""),
                "media_asset_id": media_asset_id,
                "media_status": "failed",
            }
        media_view = self.media_assets.public_asset(media_asset)
        return {
            "id": str(asset.get("id") or ""),
            "origin": str(asset.get("origin") or "source"),
            "preview_url": str(media_view.get("preview_url") or ""),
            "publication_status": str(asset.get("availability") or "local"),
            "public_url": self._safe_public_value(asset.get("public_url")),
            "width": int(media_asset.get("width") or 0),
            "height": int(media_asset.get("height") or 0),
            "bucket": self._bucket_for_media_origin(str(media_asset.get("origin") or "")),
            "source_kind": str(asset.get("source_kind") or ""),
            "media_asset_id": media_asset_id,
            "media_status": str(media_asset.get("status") or "pending"),
        }

    @staticmethod
    def _bucket_for_preview_origin(origin: str) -> str:
        return "source" if str(origin or "") in {"source", "remote_source"} else "processed"

    @staticmethod
    def _bucket_for_media_origin(origin: str) -> str:
        return "source" if str(origin or "") == "remote_source" else "processed"

    @staticmethod
    def _preview_origin_for_media(origin: str) -> str:
        return {
            "remote_source": "source",
            "ai_generated": "generated",
            "preview_upload": "upload",
            "dimension_rendered": "dimension",
        }.get(str(origin or ""), "source")

    @staticmethod
    def _source_kind_for_role(role: str) -> str:
        return {
            "main": "main",
            "gallery": "gallery",
            "sku": "sku",
            "detail": "detail",
        }.get(str(role or ""), "")

    def media_asset_id_for_preview_url(self, value: str, workspace_id: str) -> str:
        """Resolve the unified media asset id referenced by a preview URL."""
        asset_id = self._preview_asset_id(value)
        if not asset_id:
            return ""
        asset = self.repository.get_asset(asset_id, workspace_id)
        return str(asset.get("media_asset_id") or "") if asset else ""

    def _unified_media_asset_id(
        self,
        workspace_id: str,
        origin: str,
        content: bytes,
        content_type: str,
    ) -> str:
        if self.media_assets is None:
            return ""
        unified = self.media_assets.register_local_asset(
            workspace_id, origin, content, content_type
        )
        return str(unified.get("id") or "")

    def register_media_proxy(
        self,
        *,
        task_id: int,
        product_draft_id: int,
        workspace_id: str,
        media_asset_id: str,
        source_kind: str = "",
        origin: str = "source",
    ) -> dict[str, Any]:
        """Register a stable, no-copy precheck proxy for one unified media asset.

        The proxy keeps ``managed_path``/``source_url`` empty so it never reads or
        writes preview bytes; content and status come from the unified asset.
        """
        media_asset_id = str(media_asset_id or "").strip()
        if not media_asset_id:
            raise ValueError("media proxy requires a media asset id")
        # One upstream asset may be bound as both main and gallery.  Preserve
        # those business roles as separate no-copy proxies so the source pool
        # never loses a category during media-id de-duplication.
        identity = (
            "media-proxy:"
            f"{str(workspace_id or '')}:{int(task_id)}:{int(product_draft_id)}:"
            f"{media_asset_id}:{str(source_kind or '').strip()}"
        )
        identity_hash = hashlib.sha256(identity.encode("utf-8")).hexdigest()
        return self.repository.register_asset(
            workspace_id=workspace_id,
            task_id=task_id,
            product_draft_id=product_draft_id,
            origin=origin,
            identity_hash=identity_hash,
            managed_path="",
            source_url="",
            content_hash="",
            content_type="",
            byte_size=0,
            width=0,
            height=0,
            media_asset_id=media_asset_id,
            source_kind=source_kind,
        )

    def register_upload(
        self,
        *,
        task_id: int,
        product_draft_id: int,
        workspace_id: str,
        filename: str,
        content_type: str,
        content: bytes,
    ) -> dict[str, Any]:
        _ = filename
        self.require_task_draft(task_id, product_draft_id, workspace_id)
        decoded = validate_preview_image(content, content_type)
        path = self.assets.save_preview_asset(
            decoded.content,
            decoded.content_hash,
            decoded.suffix,
            workspace_id=workspace_id,
        )
        media_asset_id = self._unified_media_asset_id(
            workspace_id, "preview_upload", decoded.content, decoded.content_type
        )
        row = self.repository.register_asset(
            workspace_id=workspace_id,
            task_id=task_id,
            product_draft_id=product_draft_id,
            origin="upload",
            identity_hash=decoded.content_hash,
            managed_path=str(path),
            source_url="",
            content_hash=decoded.content_hash,
            content_type=decoded.content_type,
            byte_size=len(decoded.content),
            width=decoded.width,
            height=decoded.height,
            media_asset_id=media_asset_id,
        )
        return self.public_asset(row)

    def register_generated(
        self,
        *,
        task_id: int,
        product_draft_id: int,
        workspace_id: str,
        media: Any,
    ) -> dict[str, Any]:
        self.require_task_draft(task_id, product_draft_id, workspace_id)
        decoded = validate_preview_image(bytes(media.content), str(media.content_type or ""))
        path = self.assets.save_preview_asset(
            decoded.content,
            decoded.content_hash,
            decoded.suffix,
            workspace_id=workspace_id,
        )
        media_asset_id = self._unified_media_asset_id(
            workspace_id, "ai_generated", decoded.content, decoded.content_type
        )
        row = self.repository.register_asset(
            workspace_id=workspace_id,
            task_id=task_id,
            product_draft_id=product_draft_id,
            origin="generated",
            identity_hash=decoded.content_hash,
            managed_path=str(path),
            source_url="",
            content_hash=decoded.content_hash,
            content_type=decoded.content_type,
            byte_size=len(decoded.content),
            width=decoded.width,
            height=decoded.height,
            media_asset_id=media_asset_id,
        )
        return self.public_asset(row)

    def project_item_images(
        self,
        *,
        task_id: int,
        product_draft_id: int,
        result: Mapping[str, Any],
        saved: Mapping[str, Any],
        workspace_id: str,
        media_contract_version: int = 1,
    ) -> dict[str, Any]:
        self.require_task_draft(task_id, product_draft_id, workspace_id)
        if media_contract_version >= 2 and self.media_assets is not None:
            return self._project_item_images_v2(
                task_id=task_id,
                product_draft_id=product_draft_id,
                result=result,
                saved=saved,
                workspace_id=workspace_id,
            )
        existing = self.repository.list_assets(
            product_draft_id,
            workspace_id,
            task_id=task_id,
        )
        result_storage_values = {
            str(value or "").strip()
            for key in (
                "source_image_urls",
                "carousel_image_paths",
                "detail_image_paths",
            )
            for value in result.get(key) or []
            if str(value or "").strip()
        }
        result_storage_values.update(
            str(entry.get("value") or "").strip()
            for entry in result.get("image_manifest") or []
            if isinstance(entry, Mapping) and str(entry.get("value") or "").strip()
        )
        summary_value = str(result.get("grid_image_summary_path") or "").strip()
        if summary_value:
            result_storage_values.add(summary_value)
        by_preview = {
            self._preview_url(asset): asset
            for asset in existing
            if self._preview_url(asset)
        }
        by_id = {str(asset.get("id") or ""): asset for asset in existing}

        def register(value: Any, origin: str) -> dict[str, Any] | None:
            normalized = str(value or "").strip()
            if not normalized:
                return None
            if normalized in by_preview:
                return by_preview[normalized]
            referenced_id = self._preview_asset_id(normalized)
            if referenced_id and referenced_id in by_id:
                return by_id[referenced_id]
            if normalized.startswith("/pp-media/"):
                # Browser paths are display values, never storage authorities.
                return None
            if normalized.lower().startswith(("http://", "https://")):
                identity = hashlib.sha256(f"remote:{normalized}".encode("utf-8")).hexdigest()
                row = self.repository.register_asset(
                    workspace_id=workspace_id,
                    task_id=task_id,
                    product_draft_id=product_draft_id,
                    origin=origin,
                    identity_hash=identity,
                    managed_path="",
                    source_url=normalized,
                    content_hash="",
                    content_type="",
                    byte_size=0,
                    width=0,
                    height=0,
                )
            else:
                if normalized not in result_storage_values:
                    # Legacy override strings are display data, not permission to
                    # read an arbitrary server-managed path.
                    return None
                try:
                    path = self.assets.require_managed_file(normalized)
                    content = path.read_bytes()
                    decoded = validate_preview_image(content, "")
                    stored = self.assets.save_preview_asset(
                        content,
                        decoded.content_hash,
                        decoded.suffix,
                        workspace_id=workspace_id,
                    )
                except (OSError, ValueError):
                    return None
                identity = decoded.content_hash
                row = self.repository.register_asset(
                    workspace_id=workspace_id,
                    task_id=task_id,
                    product_draft_id=product_draft_id,
                    origin=origin,
                    identity_hash=identity,
                    managed_path=str(stored),
                    source_url="",
                    content_hash=decoded.content_hash,
                    content_type=decoded.content_type,
                    byte_size=len(content),
                    width=decoded.width,
                    height=decoded.height,
                )
            existing.append(row)
            by_preview[self._preview_url(row)] = row
            return row

        if MANIFEST_KEY in saved:
            manifest = PreviewImageManifest.from_value(saved.get(MANIFEST_KEY))
        else:
            source_values = {
                str(value or "").strip()
                for value in result.get("source_image_urls") or []
                if str(value or "").strip()
            }
            carousel_values: list[str] = []
            semantic_values: dict[str, str] = {}
            if "carousel_images" in saved:
                carousel_values = [str(value or "").strip() for value in saved.get("carousel_images") or []]
            else:
                legacy_patches = saved.get("image_slot_overrides") or {}
                for entry in result.get("image_manifest") or []:
                    if isinstance(entry, Mapping) and entry.get("value"):
                        slot_id = str(entry.get("slot_id") or "").strip()
                        patch = (
                            legacy_patches.get(slot_id)
                            if isinstance(legacy_patches, Mapping)
                            else None
                        )
                        value = str((patch or {}).get("url") or entry["value"]).strip()
                        carousel_values.append(value)
                        if slot_id:
                            semantic_values[slot_id] = value
                if not carousel_values:
                    carousel_values = [
                        str(value or "").strip()
                        for value in result.get("carousel_image_paths") or []
                    ]
                    if isinstance(legacy_patches, Mapping):
                        for slot_id, index in SLOT_INDEX.items():
                            patch = legacy_patches.get(slot_id)
                            replacement = str((patch or {}).get("url") or "").strip()
                            if replacement and index < len(carousel_values):
                                carousel_values[index] = replacement
                                semantic_values[slot_id] = replacement
                summary = str(result.get("grid_image_summary_path") or "").strip()
                if summary:
                    carousel_values.append(summary)
            carousel_values = list(dict.fromkeys(value for value in carousel_values if value))
            main_value = str(saved.get("main_image") or "").strip()
            if not main_value:
                main_value = carousel_values[0] if carousel_values else ""
            if main_value and main_value not in carousel_values:
                carousel_values.insert(0, main_value)
            detail_values = (
                [str(value or "").strip() for value in saved.get("detail_images") or []]
                if "detail_images" in saved
                else [str(value or "").strip() for value in result.get("detail_image_paths") or []]
            )

            def asset_id(value: str) -> str:
                row = register(value, "source" if value in source_values else "generated")
                return str((row or {}).get("id") or "")

            carousel_ids = [value for raw in carousel_values if (value := asset_id(raw))]
            detail_ids = [value for raw in detail_values if (value := asset_id(raw))]
            main_id = asset_id(main_value) if main_value else ""
            semantic_ids = {
                slot_id: identifier
                for slot_id, raw in semantic_values.items()
                if (identifier := asset_id(raw))
            }
            manifest = PreviewImageManifest(
                main_asset_id=main_id,
                carousel_asset_ids=tuple(carousel_ids),
                detail_asset_ids=tuple(detail_ids),
                semantic_asset_ids=semantic_ids,
            )

        owned = {
            asset["id"]: asset
            for asset in self.repository.list_assets(
                product_draft_id,
                workspace_id,
                task_id=task_id,
            )
        }
        preview_by_id = {asset_id: self._preview_url(asset) for asset_id, asset in owned.items()}
        return {
            "assets": [self.public_asset(asset) for asset in owned.values()],
            "image_manifest": manifest.as_dict(),
            "main_image": preview_by_id.get(manifest.main_asset_id, ""),
            "carousel_images": [
                preview_by_id[asset_id]
                for asset_id in manifest.carousel_asset_ids
                if asset_id in preview_by_id
            ],
            "detail_images": [
                preview_by_id[asset_id]
                for asset_id in manifest.detail_asset_ids
                if asset_id in preview_by_id
            ],
            "exportable": bool(str(result.get("optimized_title") or "").strip()),
        }

    def _project_item_images_v2(
        self,
        *,
        task_id: int,
        product_draft_id: int,
        result: Mapping[str, Any],
        saved: Mapping[str, Any],
        workspace_id: str,
    ) -> dict[str, Any]:
        entries: list[dict[str, Any]] = []
        indexed: dict[tuple[str, str], dict[str, Any]] = {}
        proxy_id_by_media: dict[str, str] = {}

        def add_entry(
            media_id: str,
            source_kind: str,
            role: str,
            sort_order: int,
        ) -> None:
            media_id = str(media_id or "").strip()
            if not media_id:
                return
            entry_key = (media_id, source_kind)
            if entry_key in indexed:
                return
            media_asset = self.media_assets.get_asset(media_id, workspace_id)
            if media_asset is None:
                return
            bucket = self._bucket_for_media_origin(str(media_asset.get("origin") or ""))
            proxy = self.register_media_proxy(
                task_id=task_id,
                product_draft_id=product_draft_id,
                workspace_id=workspace_id,
                media_asset_id=media_id,
                source_kind=source_kind,
                origin=self._preview_origin_for_media(str(media_asset.get("origin") or "")),
            )
            entry = {
                "proxy": proxy,
                "media_id": media_id,
                "bucket": bucket,
                "source_kind": source_kind,
                "role": role,
                "sort_order": sort_order,
            }
            indexed[entry_key] = entry
            proxy_id_by_media.setdefault(media_id, str(proxy["id"]))
            entries.append(entry)

        for binding in self.media_assets.list_bindings(
            workspace_id, product_draft_id=product_draft_id, active_only=True
        ):
            media_id = str(binding.get("asset_id") or "")
            media_asset = self.media_assets.get_asset(media_id, workspace_id)
            if media_asset is None:
                continue
            role = str(binding.get("role") or "")
            is_source = str(media_asset.get("origin") or "") == "remote_source"
            source_kind = self._source_kind_for_role(role) if is_source else ""
            add_entry(
                media_id,
                source_kind,
                role,
                int(binding.get("sort_order") or 0),
            )

        # Processed media referenced by an existing preview asset (generated detail
        # images, uploads) but not yet bound still belong to the processed library.
        for asset in self.repository.list_assets(
            product_draft_id, workspace_id, task_id=task_id
        ):
            media_id = str(asset.get("media_asset_id") or "")
            if not media_id or (media_id, "") in indexed:
                continue
            media_asset = self.media_assets.get_asset(media_id, workspace_id)
            if media_asset is None or str(media_asset.get("origin") or "") == "remote_source":
                continue
            add_entry(media_id, "", "", 0)

        source_rank = {"main": 0, "gallery": 1, "sku": 2, "detail": 3}
        entries.sort(
            key=lambda entry: (
                0 if entry["bucket"] == "source" else 1,
                source_rank.get(entry["source_kind"], 99)
                if entry["bucket"] == "source"
                else entry["sort_order"],
            )
        )

        def proxy_id_for_media(media_id: str) -> str:
            return proxy_id_by_media.get(str(media_id or "").strip(), "")

        proxy_by_id = {str(entry["proxy"]["id"]): entry for entry in entries}

        def resolve_saved_asset_id(saved_id: str) -> str:
            """Map a persisted precheck ID back to the current V2 proxy identity.

            Canvas acceptance and earlier manifests may store a preview-asset row
            ID (``654c..``) or a raw unified media ID, while this projection now
            serves stable no-copy proxy rows (``e388..``).  Resolving both keeps
            previously rendered AI images and dimension renders visible after a
            reload without ever guessing a file path.
            """
            saved_id = str(saved_id or "").strip()
            if not saved_id or saved_id in proxy_by_id:
                return saved_id
            row = self.repository.get_asset(saved_id, workspace_id)
            media_id = str((row or {}).get("media_asset_id") or "")
            if media_id:
                return proxy_id_by_media.get(media_id, "")
            return proxy_id_by_media.get(saved_id, "")

        saved_manifest: PreviewImageManifest | None = None
        if MANIFEST_KEY in saved:
            saved_manifest = PreviewImageManifest.from_value(saved.get(MANIFEST_KEY))
            manifest = PreviewImageManifest(
                main_asset_id=resolve_saved_asset_id(saved_manifest.main_asset_id),
                carousel_asset_ids=tuple(
                    resolve_saved_asset_id(value) for value in saved_manifest.carousel_asset_ids
                ),
                detail_asset_ids=tuple(
                    resolve_saved_asset_id(value) for value in saved_manifest.detail_asset_ids
                ),
                library_asset_ids=tuple(
                    resolve_saved_asset_id(value) for value in saved_manifest.library_asset_ids
                ),
                semantic_asset_ids={
                    str(slot_id or "").strip(): resolve_saved_asset_id(value)
                    for slot_id, value in (saved_manifest.semantic_asset_ids or {}).items()
                    if str(slot_id or "").strip() and str(value or "").strip()
                },
            )
        else:
            v2 = result.get("image_manifest_v2")
            v2 = v2 if isinstance(v2, Mapping) else {}
            raw_semantics = v2.get("semantic_asset_ids") or {}
            semantic = {
                str(slot_id or "").strip(): proxy_id_for_media(asset_id)
                for slot_id, asset_id in (
                    raw_semantics.items()
                    if isinstance(raw_semantics, Mapping)
                    else ()
                )
                if str(slot_id or "").strip() and proxy_id_for_media(asset_id)
            }
            carousel = [
                proxy_id
                for value in (v2.get("carousel_asset_ids") or [])
                if (proxy_id := proxy_id_for_media(value))
            ]
            detail = [
                proxy_id
                for value in (v2.get("detail_asset_ids") or [])
                if (proxy_id := proxy_id_for_media(value))
            ]
            main = proxy_id_for_media(str(v2.get("main_asset_id") or ""))
            manifest = PreviewImageManifest(
                main_asset_id=main,
                carousel_asset_ids=tuple(carousel),
                detail_asset_ids=tuple(detail),
                library_asset_ids=(),
                semantic_asset_ids=semantic,
            )

        # The library always auto-contains every processed proxy. Source proxies
        # only appear when the operator explicitly added them (persisted as
        # library_asset_ids). This keeps the source pool read-only.
        processed_ids = [
            str(entry["proxy"]["id"]) for entry in entries if entry["bucket"] == "processed"
        ]
        source_id_set = {
            str(entry["proxy"]["id"]) for entry in entries if entry["bucket"] == "source"
        }
        library_ids = processed_ids + [
            asset_id for asset_id in manifest.library_asset_ids if asset_id in source_id_set
        ]
        manifest = PreviewImageManifest(
            main_asset_id=manifest.main_asset_id,
            carousel_asset_ids=manifest.carousel_asset_ids,
            detail_asset_ids=manifest.detail_asset_ids,
            library_asset_ids=tuple(library_ids),
            semantic_asset_ids=manifest.semantic_asset_ids,
        )

        public_assets = [self.public_asset(entry["proxy"]) for entry in entries]
        preview_by_id = {asset["id"]: asset["preview_url"] for asset in public_assets}
        return {
            "assets": public_assets,
            "image_manifest": manifest.as_dict(),
            "main_image": preview_by_id.get(manifest.main_asset_id, ""),
            "carousel_images": [
                preview_by_id[asset_id]
                for asset_id in manifest.carousel_asset_ids
                if asset_id in preview_by_id
            ],
            "detail_images": [
                preview_by_id[asset_id]
                for asset_id in manifest.detail_asset_ids
                if asset_id in preview_by_id
            ],
            "exportable": bool(str(result.get("optimized_title") or "").strip()),
        }

    def save_preview(
        self,
        task_id: int,
        items: Sequence[Mapping[str, Any]],
        *,
        workspace_id: str,
    ) -> list[dict[str, Any]]:
        self._require_ready_source_media(task_id, items, workspace_id)
        snapshots = self.repository.save_preview_manifests(
            task_id,
            items,
            workspace_id=workspace_id,
        )
        return [
            {
                "product_draft_id": entry["product_draft_id"],
                "overrides": entry["overrides"],
                "preview_revision": entry["preview_revision"],
            }
            for entry in snapshots
        ]

    def _require_ready_source_media(
        self,
        task_id: int,
        items: Sequence[Mapping[str, Any]],
        workspace_id: str,
    ) -> None:
        """Prevent pending source proxies from entering the library or export manifest."""
        if self.media_assets is None:
            return
        for item in items:
            draft_id = int(item.get("product_draft_id") or 0)
            overrides = item.get("overrides") or {}
            if not draft_id or not isinstance(overrides, Mapping) or MANIFEST_KEY not in overrides:
                continue
            manifest = PreviewImageManifest.from_value(overrides.get(MANIFEST_KEY))
            referenced = tuple(dict.fromkeys((*manifest.live_asset_ids(), *manifest.library_asset_ids)))
            for asset in self.repository.get_assets(
                referenced,
                workspace_id,
                task_id=task_id,
                product_draft_id=draft_id,
            ):
                if not asset.get("source_kind"):
                    continue
                media = self.media_assets.get_asset(
                    str(asset.get("media_asset_id") or ""), workspace_id
                )
                if media is None or str(media.get("status") or "") != "ready":
                    raise PreviewSourceNotReady("处理前图片尚未同步完成，暂不能加入素材库或用于导出")

    def begin_finalize(
        self,
        task_id: int,
        items: Sequence[Mapping[str, Any]],
        *,
        workspace_id: str,
        idempotency_key: str = "",
        launch: bool = True,
    ) -> dict[str, Any]:
        self._require_ready_source_media(task_id, items, workspace_id)
        run = self.repository.create_finalize_run(
            workspace_id=workspace_id,
            task_id=task_id,
            items=items,
            idempotency_key=idempotency_key,
        )
        if launch and run["status"] in {"queued", "publish_failed"}:
            self._launch(run["id"], workspace_id)
        return self._public_run(run)

    def get_finalize(self, run_id: str, *, workspace_id: str) -> dict[str, Any]:
        run = self.repository.get_finalize_run(run_id, workspace_id)
        if run is None:
            raise LookupError("preview finalization run not found")
        return self._public_run(run)

    def retry_finalize(
        self,
        run_id: str,
        *,
        workspace_id: str,
        launch: bool = True,
    ) -> dict[str, Any]:
        run = self.repository.queue_finalize_retry(run_id, workspace_id)
        if launch and run["status"] == "queued":
            self._launch(run_id, workspace_id)
        return self._public_run(run)

    def _launch(self, run_id: str, workspace_id: str) -> bool:
        key = (str(workspace_id), str(run_id))

        def execute() -> None:
            try:
                self.run_finalize(run_id, workspace_id=workspace_id)
            except PreviewPublicationConflict:
                # A retry/recovery worker may have acquired the lease first.
                # The durable run record is authoritative; this worker exits
                # quietly instead of leaking an unhandled daemon exception.
                pass
            finally:
                with self._finalize_worker_lock:
                    self._finalize_workers.pop(key, None)

        with self._finalize_worker_lock:
            current = self._finalize_workers.get(key)
            if current is not None and current.is_alive():
                return False
            thread = threading.Thread(
                target=execute,
                name=f"pp-preview-finalize-{run_id}",
                daemon=True,
            )
            self._finalize_workers[key] = thread
            thread.start()
        return True

    def recover_background_work(self) -> dict[str, int]:
        with self._finalize_worker_lock:
            if any(worker.is_alive() for worker in self._finalize_workers.values()):
                return {"queued": 0, "launched": 0}
        queued = self.repository.recover_interrupted_finalize_runs()
        launched = sum(
            self._launch(str(run["id"]), str(run["workspace_id"]))
            for run in queued
        )
        return {"queued": len(queued), "launched": launched}

    def run_finalize(self, run_id: str, *, workspace_id: str) -> dict[str, Any]:
        claimed = self.repository.claim_finalize_run(run_id, workspace_id)
        if claimed.get("status") in {"completed", "stale"}:
            return self._public_run(claimed)
        token = str(claimed.pop("claim_token", ""))
        stop = threading.Event()
        candidate_workbook: Path | None = None

        def heartbeat() -> None:
            while not stop.wait(30):
                try:
                    self.repository.renew_finalize_claim(run_id, workspace_id, token)
                except Exception:
                    return

        pulse = threading.Thread(target=heartbeat, name=f"pp-preview-heartbeat-{run_id}", daemon=True)
        pulse.start()
        try:
            snapshot = list(claimed.get("snapshot") or [])
            asset_ids = list(
                dict.fromkeys(
                    str(asset_id)
                    for entry in snapshot
                    for asset_id in entry.get("live_asset_ids") or []
                    if str(asset_id or "").strip()
                )
            )
            assets = self.repository.get_assets(asset_ids, workspace_id, task_id=int(claimed["task_id"]))
            if {asset["id"] for asset in assets} != set(asset_ids):
                raise ValueError("finalization snapshot contains a missing image asset")
            materialized = [self._materialize(asset, workspace_id) for asset in assets]
            by_hash: dict[str, dict[str, Any]] = {}
            for asset in materialized:
                digest = str(asset.get("content_hash") or "")
                if not digest:
                    raise ValueError("finalization image has no content hash")
                by_hash.setdefault(digest, asset)

            errors: list[dict[str, Any]] = []
            published: dict[str, str] = {}
            with ThreadPoolExecutor(max_workers=min(self.max_publish_workers, max(1, len(by_hash)))) as pool:
                futures = {
                    pool.submit(self._publish_hash, digest, asset, workspace_id): digest
                    for digest, asset in by_hash.items()
                }
                for future in as_completed(futures):
                    digest = futures[future]
                    try:
                        published[digest] = future.result()
                    except Exception as exc:
                        errors.append(
                            {
                                "content_hash": digest,
                                "code": "preview_publish_failed",
                                "message": self._bounded_error(exc),
                            }
                        )
                    self.repository.update_finalize_progress(
                        run_id,
                        workspace_id,
                        token,
                        published_count=len(published),
                        failed_count=len(errors),
                        errors=errors,
                    )
            if errors:
                return self._public_run(
                    self.repository.mark_finalize_failed(run_id, workspace_id, token, errors)
                )

            if not self._snapshot_current(
                int(claimed["task_id"]), snapshot, workspace_id
            ):
                return self._public_run(
                    self.repository.mark_finalize_stale(run_id, workspace_id, token)
                )
            asset_urls = {
                asset["id"]: published[str(asset["content_hash"])]
                for asset in materialized
            }
            rows = self._export_rows(int(claimed["task_id"]), snapshot, asset_urls, workspace_id)
            exports = [value for row in rows for value in _dxm_export_rows(row)]
            if not exports:
                raise ValueError("task has no exportable rows")
            run_root = self.assets.output_root / f"task_{int(claimed['task_id'])}" / "finalizations" / run_id
            run_root.mkdir(parents=True, exist_ok=True)
            temporary = run_root / f".{token}.xlsx.tmp"
            final = run_root / f"dxm_import_task_{int(claimed['task_id'])}_{token}.xlsx"
            create_result_workbook(rows, temporary)
            if not self._snapshot_current(
                int(claimed["task_id"]), snapshot, workspace_id
            ):
                temporary.unlink(missing_ok=True)
                return self._public_run(
                    self.repository.mark_finalize_stale(run_id, workspace_id, token)
                )
            os.replace(temporary, final)
            candidate_workbook = final
            completed = self.repository.mark_finalize_completed(
                run_id,
                workspace_id,
                token,
                workbook_path=str(final),
                row_count=len(exports),
                product_count=len(rows),
                snapshot=snapshot,
            )
            if completed.get("status") != "completed":
                final.unlink(missing_ok=True)
            else:
                candidate_workbook = None
            return self._public_run(completed)
        except PreviewPublicationConflict:
            if candidate_workbook is not None:
                candidate_workbook.unlink(missing_ok=True)
            raise
        except Exception as exc:
            if candidate_workbook is not None:
                candidate_workbook.unlink(missing_ok=True)
            error = {
                "code": "preview_finalize_failed",
                "message": self._bounded_error(exc),
            }
            try:
                failed = self.repository.mark_finalize_failed(
                    run_id,
                    workspace_id,
                    token,
                    [error],
                )
                return self._public_run(failed)
            except PreviewPublicationConflict:
                raise
        finally:
            stop.set()
            pulse.join(timeout=1)

    def finalize_download_path(self, run_id: str, task_id: int, *, workspace_id: str) -> Path:
        run = self.repository.get_finalize_run(run_id, workspace_id)
        if run is None or int(run.get("task_id") or 0) != int(task_id):
            raise LookupError("preview finalization run not found")
        if run.get("status") != "completed" or not run.get("workbook_path"):
            raise FileNotFoundError("preview finalization workbook is not ready")
        return self.assets.require_managed_file(str(run["workbook_path"]))

    def preview_asset_content(
        self,
        asset_id: str,
        *,
        workspace_id: str,
        expires: int,
        signature: str,
    ) -> tuple[Path, str]:
        asset = self.repository.get_asset(asset_id, workspace_id)
        if asset is None or not asset.get("managed_path"):
            raise LookupError("preview image asset not found")
        now = int(time.time())
        if int(expires) < now or int(expires) > now + 3 * 3600:
            raise LookupError("preview image asset link expired")
        expected = self._asset_access_signature(asset, int(expires))
        if not hmac.compare_digest(expected, str(signature or "")):
            raise LookupError("preview image asset not found")
        path = self.assets.require_workspace_preview_asset(
            str(asset["managed_path"]),
            workspace_id=workspace_id,
        )
        return path, str(asset.get("content_type") or "image/jpeg")

    def _materialize(self, asset: dict[str, Any], workspace_id: str) -> dict[str, Any]:
        media_asset_id = str(asset.get("media_asset_id") or "")
        if media_asset_id and self.media_assets is not None:
            path, content_type = self.media_assets.require_ready_managed_file(
                media_asset_id, workspace_id=workspace_id
            )
            content = path.read_bytes()
            return {
                **asset,
                "content_hash": hashlib.sha256(content).hexdigest(),
                "content_type": content_type,
                "byte_size": len(content),
            }
        managed = str(asset.get("managed_path") or "")
        if managed and asset.get("content_hash"):
            path = self.assets.require_workspace_preview_asset(managed, workspace_id=workspace_id)
            content = path.read_bytes()
            if hashlib.sha256(content).hexdigest() != str(asset["content_hash"]):
                raise ValueError("managed preview asset hash mismatch")
            return asset
        source_url = str(asset.get("source_url") or "")
        if not source_url or not is_safe_external_url(source_url):
            raise ValueError("preview asset source is unavailable")
        if self._trusted(source_url):
            # Reusing a trusted configured COS URL needs no download or PUT.
            url_identity = hashlib.sha256(f"trusted-url:{source_url}".encode("utf-8")).hexdigest()
            return self.repository.mark_asset_reused_public_url(
                asset["id"],
                workspace_id,
                source_url,
                content_hash=url_identity,
            )
        claim = self.repository.claim_materialization(asset["id"], workspace_id)
        claim_token = str(claim.pop("materialize_claim_token", ""))
        if not claim_token:
            return claim
        try:
            fetched = self.public_image_fetcher(source_url)
            decoded = validate_preview_image(bytes(fetched.content), str(fetched.media_type or ""))
            path = self.assets.save_preview_asset(
                decoded.content,
                decoded.content_hash,
                decoded.suffix,
                workspace_id=workspace_id,
            )
            return self.repository.mark_materialization_succeeded(
                asset["id"],
                workspace_id,
                claim_token,
                managed_path=str(path),
                content_hash=decoded.content_hash,
                content_type=decoded.content_type,
                byte_size=len(decoded.content),
                width=decoded.width,
                height=decoded.height,
            )
        except Exception as exc:
            self.repository.mark_materialization_failed(
                asset["id"],
                workspace_id,
                claim_token,
                "preview_materialization_failed",
                self._bounded_error(exc),
            )
            raise

    def _publish_hash(self, digest: str, asset: dict[str, Any], workspace_id: str) -> str:
        existing = self.repository.get_publication(workspace_id, digest)
        if existing and existing.get("status") == "published":
            if self._trusted(existing.get("public_url")):
                return str(existing["public_url"])
            self.repository.invalidate_publication(
                workspace_id,
                digest,
                str(existing.get("public_url") or ""),
            )
        if asset.get("availability") == "published" and self._trusted(asset.get("public_url") or asset.get("source_url")):
            return str(asset.get("public_url") or asset.get("source_url"))
        if self.publisher is None:
            raise ValueError("COS publisher is not configured")
        try:
            claim = self.repository.claim_publication(
                workspace_id,
                digest,
                content_type=str(asset.get("content_type") or "image/jpeg"),
                byte_size=int(asset.get("byte_size") or 0),
            )
        except PreviewPublicationConflict:
            for delay in (0.25, 0.5, 1.0, 2.0, 2.0):
                time.sleep(delay)
                current = self.repository.get_publication(workspace_id, digest)
                if current and current.get("status") == "published" and self._trusted(current.get("public_url")):
                    return str(current["public_url"])
                if current and current.get("status") == "publish_failed":
                    break
            claim = self.repository.claim_publication(
                workspace_id,
                digest,
                content_type=str(asset.get("content_type") or "image/jpeg"),
                byte_size=int(asset.get("byte_size") or 0),
            )
        if claim.get("status") == "published":
            url = str(claim.get("public_url") or "")
            if self._trusted(url):
                return url
            raise ValueError("persisted COS object is no longer publicly readable")
        token = str(claim.pop("claim_token", ""))
        try:
            media_asset_id = str(asset.get("media_asset_id") or "")
            if media_asset_id and self.media_assets is not None:
                path, _content_type = self.media_assets.require_ready_managed_file(
                    media_asset_id, workspace_id=workspace_id
                )
            else:
                path = self.assets.require_workspace_preview_asset(
                    str(asset.get("managed_path") or ""),
                    workspace_id=workspace_id,
                )
            content = path.read_bytes()
            if hashlib.sha256(content).hexdigest() != digest:
                raise ValueError("preview publication bytes do not match content hash")
            suffix = {"image/png": ".png", "image/webp": ".webp"}.get(
                str(asset.get("content_type") or "").casefold(),
                ".jpg",
            )
            url = str(
                self.publisher(
                    content,
                    str(asset.get("content_type") or "image/jpeg"),
                    suffix,
                    digest,
                    workspace_id,
                )
                or ""
            )
            if not self._trusted(url):
                raise ValueError("COS returned an untrusted or non-public image URL")
            self.repository.mark_publication_succeeded(workspace_id, digest, token, url)
            return url
        except Exception as exc:
            self.repository.mark_publication_failed(
                workspace_id,
                digest,
                token,
                "preview_publish_failed",
                self._bounded_error(exc),
            )
            raise

    def _export_rows(
        self,
        task_id: int,
        snapshot: list[dict[str, Any]],
        asset_urls: Mapping[str, str],
        workspace_id: str,
    ) -> list[dict[str, Any]]:
        task = self.product_repository.get_task(task_id, workspace_id)
        if task is None:
            raise LookupError("product processing task not found")
        by_draft = {
            int(item["product_draft_id"]): item
            for item in task.get("items") or []
            if item.get("product_draft_id") is not None
        }
        rows: list[dict[str, Any]] = []
        for entry in snapshot:
            draft_id = int(entry["product_draft_id"])
            result = dict((by_draft.get(draft_id) or {}).get("result") or {})
            manifest = PreviewImageManifest.from_value(entry.get("manifest"))
            main = asset_urls.get(manifest.main_asset_id, "")
            carousel = [asset_urls.get(asset_id, "") for asset_id in manifest.carousel_asset_ids]
            details = [asset_urls.get(asset_id, "") for asset_id in manifest.detail_asset_ids]
            require_final_public_image_urls([main, *carousel, *details])
            overrides = dict(entry.get("overrides") or {})
            overrides.update(
                {
                    "main_image": main,
                    "carousel_images": carousel,
                    "detail_images": details,
                }
            )
            overrides.pop("image_slot_overrides", None)
            result["preview_overrides"] = overrides
            rows.append(result)
        return rows

    def _snapshot_current(
        self,
        task_id: int,
        snapshot: list[dict[str, Any]],
        workspace_id: str,
    ) -> bool:
        task = self.product_repository.get_task(int(task_id), workspace_id)
        if task is None:
            return False
        items_by_draft = {
            int(item["product_draft_id"]): item
            for item in task.get("items") or []
            if item.get("product_draft_id") is not None
        }
        for entry in snapshot:
            draft = self.product_repository.get_draft(
                int(entry.get("product_draft_id") or 0),
                workspace_id=workspace_id,
            )
            if draft is None or int(draft.get("preview_revision") or 0) != int(
                entry.get("preview_revision") or -1
            ):
                return False
            item = items_by_draft.get(int(entry.get("product_draft_id") or 0))
            if item is None or task_item_result_version(
                item.get("result") or {}
            ) != str(entry.get("result_version") or "").casefold():
                return False
        return True

    def _preview_url(self, asset: Mapping[str, Any]) -> str:
        public = self._safe_public_value(asset.get("public_url"))
        if public:
            return public
        managed = str(asset.get("managed_path") or "").strip()
        if managed:
            try:
                self.assets.require_workspace_preview_asset(
                    managed,
                    workspace_id=str(asset.get("workspace_id") or ""),
                )
            except (ValueError, OSError):
                # Compatibility for a former canvas acceptance path which stored
                # this API display URL in ``managed_path``.  Resolve only an
                # asset from the same task/draft/workspace; a browser URL never
                # becomes a storage authority.
                referenced_id = self._preview_asset_id(managed)
                referenced = (
                    self.repository.get_asset(
                        referenced_id, str(asset.get("workspace_id") or "")
                    )
                    if referenced_id
                    else None
                )
                if (
                    referenced is not None
                    and str(referenced.get("id") or "") != str(asset.get("id") or "")
                    and int(referenced.get("task_id") or 0) == int(asset.get("task_id") or 0)
                    and int(referenced.get("product_draft_id") or 0)
                    == int(asset.get("product_draft_id") or 0)
                ):
                    return self._preview_url(referenced)
                return ""
            expires = ((int(time.time()) // 3600) + 2) * 3600
            query = urlencode(
                {
                    "workspace_id": str(asset.get("workspace_id") or ""),
                    "expires": expires,
                    "signature": self._asset_access_signature(asset, expires),
                }
            )
            return (
                "/api/product-processing/preview/assets/"
                f"{str(asset.get('id') or '')}/content?{query}"
            )
        source = str(asset.get("source_url") or "").strip()
        return source if is_safe_external_url(source) else ""

    @staticmethod
    def _preview_asset_id(value: str) -> str:
        try:
            parts = urlsplit(str(value or "")).path.rstrip("/").split("/")
        except ValueError:
            return ""
        if len(parts) >= 3 and parts[-3] == "assets" and parts[-1] == "content":
            return parts[-2]
        return ""

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
    def _safe_public_value(value: Any) -> str:
        normalized = str(value or "").strip()
        return normalized if normalized.lower().startswith("https://") and is_safe_external_url(normalized) else ""

    def _trusted(self, value: Any) -> bool:
        normalized = self._safe_public_value(value)
        if not normalized:
            return False
        try:
            return bool(self.trusted_public_url(normalized))
        except Exception:
            return False

    def _public_run(self, run: Mapping[str, Any]) -> dict[str, Any]:
        task_id = int(run.get("task_id") or 0)
        run_id = str(run.get("id") or "")
        ready = bool(run.get("status") == "completed" and run.get("workbook_path"))
        return {
            "id": run_id,
            "task_id": task_id,
            "status": str(run.get("status") or "queued"),
            "total_count": int(run.get("total_count") or 0),
            "published_count": int(run.get("published_count") or 0),
            "failed_count": int(run.get("failed_count") or 0),
            "errors": [
                {
                    "content_hash": str(error.get("content_hash") or ""),
                    "code": str(error.get("code") or "preview_finalize_failed"),
                    "message": str(error.get("message") or "")[:240],
                }
                for error in run.get("errors") or []
                if isinstance(error, Mapping)
            ],
            "workbook_ready": ready,
            "file": Path(str(run.get("workbook_path") or "")).name if ready else "",
            "row_count": int(run.get("row_count") or 0),
            "product_count": int(run.get("product_count") or 0),
            "download": (
                f"/api/product-processing/tasks/{task_id}/preview/finalize/{run_id}/download"
                if ready
                else ""
            ),
        }

    @staticmethod
    def _bounded_error(exc: Exception) -> str:
        return (str(exc).replace("\r", " ").replace("\n", " ").strip() or type(exc).__name__)[:240]
