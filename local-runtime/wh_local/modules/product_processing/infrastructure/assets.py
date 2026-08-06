from __future__ import annotations

import hashlib
import mimetypes
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
        for path in (self.upload_root, self.library_root, self.output_root):
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

    @staticmethod
    def _image_suffix(filename: str, content_type: str) -> str:
        suffix = Path(filename or "").suffix.lower()
        if suffix in {".jpg", ".jpeg", ".png", ".webp", ".gif"}:
            return suffix
        guessed = mimetypes.guess_extension(content_type or "") or ".jpg"
        return guessed if guessed in {".jpg", ".jpeg", ".png", ".webp", ".gif"} else ".jpg"
