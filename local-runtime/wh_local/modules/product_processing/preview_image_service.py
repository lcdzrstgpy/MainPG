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
from .domain.preview_images import MANIFEST_KEY, SLOT_INDEX, PreviewImageManifest
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
)
from .infrastructure.repository import ProductProcessingRepository


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
    ):
        if not 1 <= int(max_publish_workers) <= 6:
            raise ValueError("preview publish workers must be between 1 and 6")
        self.repository = repository
        self.product_repository = product_repository
        self.assets = assets
        self.publisher = publisher
        self.trusted_public_url = trusted_public_url or (lambda _value: False)
        if public_image_fetcher is None:
            from wh_local.data_collection.public_image_fetch import fetch_public_image

            public_image_fetcher = fetch_public_image
        self.public_image_fetcher = public_image_fetcher
        self.max_publish_workers = int(max_publish_workers)

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
        return {
            "id": str(asset.get("id") or ""),
            "origin": str(asset.get("origin") or "source"),
            "preview_url": self._preview_url(asset),
            "publication_status": str(asset.get("availability") or "local"),
            "public_url": self._safe_public_value(asset.get("public_url")),
            "width": int(asset.get("width") or 0),
            "height": int(asset.get("height") or 0),
        }

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
    ) -> dict[str, Any]:
        self.require_task_draft(task_id, product_draft_id, workspace_id)
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

    def save_preview(
        self,
        task_id: int,
        items: Sequence[Mapping[str, Any]],
        *,
        workspace_id: str,
    ) -> list[dict[str, Any]]:
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

    def begin_finalize(
        self,
        task_id: int,
        items: Sequence[Mapping[str, Any]],
        *,
        workspace_id: str,
        idempotency_key: str = "",
        launch: bool = True,
    ) -> dict[str, Any]:
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

    def _launch(self, run_id: str, workspace_id: str) -> None:
        thread = threading.Thread(
            target=self.run_finalize,
            kwargs={"run_id": run_id, "workspace_id": workspace_id},
            name=f"pp-preview-finalize-{run_id}",
            daemon=True,
        )
        thread.start()

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

            if not self._snapshot_current(snapshot, workspace_id):
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
            if not self._snapshot_current(snapshot, workspace_id):
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

    def _snapshot_current(self, snapshot: list[dict[str, Any]], workspace_id: str) -> bool:
        for entry in snapshot:
            draft = self.product_repository.get_draft(
                int(entry.get("product_draft_id") or 0),
                workspace_id=workspace_id,
            )
            if draft is None or int(draft.get("preview_revision") or 0) != int(
                entry.get("preview_revision") or -1
            ):
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
