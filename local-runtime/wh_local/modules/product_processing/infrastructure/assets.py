from __future__ import annotations

import hashlib
import mimetypes
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path

from ..domain.workbooks import create_error_report, create_result_workbook, create_video_manifest
from .database import default_storage_root


@dataclass(frozen=True)
class TaskOutputPaths:
    workbook: Path
    errors: Path
    video_manifest: Path | None


class ProductProcessingAssets:
    def __init__(self, root: Path | None = None):
        self.root = (root or default_storage_root()).resolve()
        self.upload_root = self.root / "draft-images"
        self.library_root = self.root / "source-image-library"
        self.output_root = self.root / "outputs"
        self.preview_asset_root = self.output_root / "preview-assets"
        self.media_asset_root = self.output_root / "media-assets"
        for path in (
            self.upload_root,
            self.library_root,
            self.output_root,
            self.preview_asset_root,
            self.media_asset_root,
        ):
            path.mkdir(parents=True, exist_ok=True)

    def save_draft_image(self, content: bytes, filename: str, content_type: str = "") -> Path:
        if not content:
            raise ValueError("uploaded draft image is empty")
        suffix = self._image_suffix(filename, content_type)
        digest = hashlib.sha256(content).hexdigest()
        path = self.upload_root / digest[:2] / f"{digest}{suffix}"
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            path.write_bytes(content)
        return path

    def save_source_image(self, content: bytes, filename: str, content_type: str = "") -> Path:
        if not content:
            raise ValueError("source image is empty")
        suffix = self._image_suffix(filename, content_type)
        digest = hashlib.sha256(content).hexdigest()
        path = self.library_root / digest[:2] / f"{digest}{suffix}"
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            path.write_bytes(content)
        return path

    def materialize_source_manifest(self, task_id: int, image_urls: list[str]) -> Path:
        path = self.library_root / f"task_{task_id}_source_images.txt"
        unique_urls = list(dict.fromkeys(item.strip() for item in image_urls if item and item.strip()))
        path.write_text("\n".join(unique_urls), encoding="utf-8")
        return path

    def save_generated_image(
        self,
        task_id: int,
        draft_id: int,
        stage: str,
        content: bytes,
        suffix: str = ".jpg",
    ) -> Path:
        """Persist an AI-generated image under the task output tree and return its path."""
        if not content:
            raise ValueError("generated image is empty")
        safe_suffix = suffix if suffix in {".jpg", ".jpeg", ".png", ".webp"} else ".jpg"
        safe_stage = "".join(ch for ch in str(stage) if ch.isalnum() or ch in {"_", "-"}) or "generated"
        task_root = self.output_root / f"task_{task_id}" / "images" / f"draft_{draft_id}"
        task_root.mkdir(parents=True, exist_ok=True)
        path = task_root / f"{safe_stage}{safe_suffix}"
        path.write_bytes(content)
        return path

    def save_dimension_asset(
        self,
        content: bytes,
        *,
        kind: str,
        suffix: str,
        workspace_id: str = "local",
    ) -> Path:
        if not content:
            raise ValueError("dimension asset is empty")
        safe_suffix = str(suffix or "").lower()
        allowed_suffixes = {".png", ".jpg", ".jpeg", ".webp"} if kind == "source" else {".png", ".jpg", ".jpeg"}
        if safe_suffix not in allowed_suffixes:
            raise ValueError("unsupported dimension asset suffix")
        digest = hashlib.sha256(content).hexdigest()
        safe_kind = kind if kind in {"source", "master", "published"} else "published"
        workspace_root = self._dimension_workspace_root(workspace_id)
        root = (workspace_root / safe_kind / digest[:2]).resolve()
        if workspace_root != root and workspace_root not in root.parents:
            raise ValueError("dimension asset path is outside the workspace root")
        root.mkdir(parents=True, exist_ok=True)
        path = (root / f"{digest}{safe_suffix}").resolve()
        if root != path.parent:
            raise ValueError("dimension asset path is outside the managed root")
        if not path.exists():
            path.write_bytes(content)
        return path

    def require_workspace_dimension_asset(
        self, raw_path: str, *, workspace_id: str
    ) -> Path:
        """Resolve a persisted dimension output inside one workspace namespace.

        Callers must still obtain ``raw_path`` from a workspace-scoped asset row;
        arbitrary client paths and URLs are not accepted as asset identities.
        """

        if not raw_path or "://" in raw_path:
            raise ValueError("dimension asset must be a managed local path")
        workspace_root = self._dimension_workspace_root(workspace_id)
        path = Path(raw_path).resolve()
        if workspace_root != path and workspace_root not in path.parents:
            raise ValueError("dimension asset is outside the workspace root")
        if not path.is_file():
            raise FileNotFoundError("dimension asset does not exist")
        return path

    def save_preview_asset(
        self,
        content: bytes,
        content_hash: str,
        suffix: str,
        *,
        workspace_id: str = "local",
    ) -> Path:
        """Store original validated bytes in one workspace content namespace."""

        if not content:
            raise ValueError("preview asset is empty")
        digest = str(content_hash or "").strip().casefold()
        if (
            len(digest) != 64
            or any(character not in "0123456789abcdef" for character in digest)
            or hashlib.sha256(content).hexdigest() != digest
        ):
            raise ValueError("preview asset content hash does not match its bytes")
        safe_suffix = str(suffix or "").strip().casefold()
        if safe_suffix == ".jpeg":
            safe_suffix = ".jpg"
        if safe_suffix not in {".jpg", ".png", ".webp"}:
            raise ValueError("unsupported preview asset suffix")

        workspace_root = self._preview_workspace_root(workspace_id)
        parent = (workspace_root / digest[:2]).resolve()
        if workspace_root != parent and workspace_root not in parent.parents:
            raise ValueError("preview asset path is outside the workspace root")
        parent.mkdir(parents=True, exist_ok=True)
        path = (parent / f"{digest}{safe_suffix}").resolve()
        if path.parent != parent:
            raise ValueError("preview asset path is outside the managed root")
        temporary_path: Path | None = None
        if not path.exists():
            try:
                with tempfile.NamedTemporaryFile(
                    mode="wb",
                    prefix=f".{digest}.",
                    suffix=".tmp",
                    dir=parent,
                    delete=False,
                ) as temporary:
                    temporary_path = Path(temporary.name)
                    temporary.write(content)
                    temporary.flush()
                    os.fsync(temporary.fileno())
                # Do not replace a concurrent writer's completed object. Both
                # writers address the same digest, so the winner is equivalent.
                if path.exists():
                    temporary_path.unlink(missing_ok=True)
                    temporary_path = None
                else:
                    os.replace(temporary_path, path)
                    temporary_path = None
            finally:
                if temporary_path is not None:
                    temporary_path.unlink(missing_ok=True)
        if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            raise ValueError("stored preview asset does not match its content address")
        return path

    def require_workspace_preview_asset(
        self,
        raw_path: str,
        *,
        workspace_id: str,
    ) -> Path:
        """Resolve a database-owned preview file within the caller's workspace."""

        if not raw_path or "://" in raw_path:
            raise ValueError("preview asset must be a managed local path")
        workspace_root = self._preview_workspace_root(workspace_id)
        path = Path(raw_path).resolve()
        if workspace_root != path and workspace_root not in path.parents:
            raise ValueError("preview asset is outside the workspace root")
        if not path.is_file():
            raise FileNotFoundError("preview asset does not exist")
        return path

    def save_media_asset(
        self,
        content: bytes,
        content_hash: str,
        suffix: str,
        *,
        workspace_id: str = "local",
    ) -> Path:
        """Store verified bytes in the content-addressed unified media root."""

        if not content:
            raise ValueError("media asset is empty")
        digest = str(content_hash or "").strip().casefold()
        if (
            len(digest) != 64
            or any(ch not in "0123456789abcdef" for ch in digest)
            or hashlib.sha256(content).hexdigest() != digest
        ):
            raise ValueError("media asset content hash does not match its bytes")
        safe_suffix = str(suffix or "").strip().casefold()
        if safe_suffix == ".jpeg":
            safe_suffix = ".jpg"
        if safe_suffix not in {".jpg", ".png", ".webp"}:
            raise ValueError("unsupported media asset suffix")

        workspace_root = self._media_workspace_root(workspace_id)
        parent = (workspace_root / digest[:2]).resolve()
        if workspace_root != parent and workspace_root not in parent.parents:
            raise ValueError("media asset path is outside the workspace root")
        parent.mkdir(parents=True, exist_ok=True)
        path = (parent / f"{digest}{safe_suffix}").resolve()
        if path.parent != parent:
            raise ValueError("media asset path is outside the managed root")
        temporary_path: Path | None = None
        if not path.exists():
            try:
                with tempfile.NamedTemporaryFile(
                    mode="wb",
                    prefix=f".{digest}.",
                    suffix=".tmp",
                    dir=parent,
                    delete=False,
                ) as temporary:
                    temporary_path = Path(temporary.name)
                    temporary.write(content)
                    temporary.flush()
                    os.fsync(temporary.fileno())
                if path.exists():
                    temporary_path.unlink(missing_ok=True)
                    temporary_path = None
                else:
                    os.replace(temporary_path, path)
                    temporary_path = None
            finally:
                if temporary_path is not None:
                    temporary_path.unlink(missing_ok=True)
        if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            raise ValueError("stored media asset does not match its content address")
        return path

    def require_workspace_media_asset(
        self,
        raw_path: str,
        *,
        workspace_id: str,
    ) -> Path:
        """Resolve a database-owned unified media file within the caller's workspace."""

        if not raw_path or "://" in raw_path:
            raise ValueError("media asset must be a managed local path")
        workspace_root = self._media_workspace_root(workspace_id)
        path = Path(raw_path).resolve()
        if workspace_root != path and workspace_root not in path.parents:
            raise ValueError("media asset is outside the workspace root")
        if not path.is_file():
            raise FileNotFoundError("media asset does not exist")
        return path

    def _media_workspace_root(self, workspace_id: str) -> Path:
        workspace_key = hashlib.sha256(
            str(workspace_id or "").encode("utf-8")
        ).hexdigest()[:24]
        root = (self.media_asset_root / "workspaces" / workspace_key).resolve()
        media_root = self.media_asset_root.resolve()
        if media_root != root and media_root not in root.parents:
            raise ValueError("media workspace path is outside the managed root")
        return root

    def import_dimension_as_preview_asset(
        self,
        raw_path: str,
        *,
        workspace_id: str,
        content_hash: str,
        content_type: str,
    ) -> Path:
        """Copy verified dimension bytes into the preview publication namespace."""
        source = self.require_workspace_dimension_asset(
            raw_path,
            workspace_id=workspace_id,
        )
        content = source.read_bytes()
        digest = hashlib.sha256(content).hexdigest()
        if digest != str(content_hash or "").strip().casefold():
            raise ValueError("dimension preview asset hash does not match managed bytes")
        suffix = {
            "image/png": ".png",
            "image/webp": ".webp",
        }.get(str(content_type or "").split(";", 1)[0].strip().casefold(), ".jpg")
        return self.save_preview_asset(
            content,
            digest,
            suffix,
            workspace_id=workspace_id,
        )

    def write_task_outputs(
        self,
        task_id: int,
        successes: list[dict],
        failures: list[dict],
        *,
        include_video_manifest: bool,
    ) -> TaskOutputPaths:
        task_root = self.output_root / f"task_{task_id}"
        workbook = task_root / f"dxm_import_task_{task_id}.xlsx"
        errors = task_root / f"error_report_task_{task_id}.csv"
        video = task_root / f"product_video_manifest_task_{task_id}.csv"
        create_result_workbook(successes, workbook)
        create_error_report(failures, errors)
        if include_video_manifest:
            create_video_manifest(successes, video)
        return TaskOutputPaths(workbook, errors, video if include_video_manifest else None)

    def require_managed_file(self, raw_path: str) -> Path:
        if not raw_path:
            raise FileNotFoundError("product processing output is not available")
        path = Path(raw_path).resolve()
        if self.root != path and self.root not in path.parents:
            raise ValueError("product processing file is outside the managed storage root")
        if not path.is_file():
            raise FileNotFoundError("product processing file does not exist")
        return path

    def _dimension_workspace_root(self, workspace_id: str) -> Path:
        workspace_key = hashlib.sha256(
            str(workspace_id or "").encode("utf-8")
        ).hexdigest()[:24]
        root = (
            self.output_root
            / "dimension-canvas"
            / "workspaces"
            / workspace_key
        ).resolve()
        output_root = self.output_root.resolve()
        if output_root != root and output_root not in root.parents:
            raise ValueError("dimension workspace path is outside the managed root")
        return root

    def _preview_workspace_root(self, workspace_id: str) -> Path:
        workspace_key = hashlib.sha256(
            str(workspace_id or "").encode("utf-8")
        ).hexdigest()[:24]
        root = (self.preview_asset_root / "workspaces" / workspace_key).resolve()
        preview_root = self.preview_asset_root.resolve()
        if preview_root != root and preview_root not in root.parents:
            raise ValueError("preview workspace path is outside the managed root")
        return root

    @staticmethod
    def _image_suffix(filename: str, content_type: str) -> str:
        suffix = Path(filename or "").suffix.lower()
        if suffix in {".jpg", ".jpeg", ".png", ".webp", ".gif"}:
            return suffix
        guessed = mimetypes.guess_extension(content_type or "") or ".jpg"
        return guessed if guessed in {".jpg", ".jpeg", ".png", ".webp", ".gif"} else ".jpg"
