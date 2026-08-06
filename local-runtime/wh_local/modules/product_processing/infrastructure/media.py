"""Reference-image processing adapters used by Product Processing tasks.

The adapter follows the original workbench's operational shape while remaining
module-local: primary/backup OpenAI-compatible image-edit providers, reference
images from the confirmed collection snapshot, optional COS publication, and no
credential values in task results.
"""

from __future__ import annotations

import base64
import mimetypes
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from typing import Any, Callable, Iterable
from urllib.parse import urlsplit

import requests

from ..domain.policy import is_safe_external_url

# 对齐原项目 native_product_engine.DXM_IMAGE_TARGET_SIZE = 800
# 店小秘导入要求图片不小于 800×800，拆分后每格缩放到该尺寸。
DXM_IMAGE_TARGET_SIZE = 800
# 对齐原项目 native_product_engine.IMAGE_JPEG_QUALITY = 90
DXM_IMAGE_JPEG_QUALITY = 90


class MediaConfigurationError(RuntimeError):
    pass


class MediaProcessingError(RuntimeError):
    pass


@dataclass(frozen=True)
class GeneratedMedia:
    stage: str
    content: bytes
    content_type: str
    suffix: str
    provider: str
    model: str
    reference_count: int


class ProductImageProcessor:
    """A small OpenAI-compatible image-edit and COS adapter.

    ``config_provider`` is supplied by the application composition root and may
    contain decrypted credentials.  This class never returns those credentials,
    and exceptions deliberately avoid response bodies to prevent leakage.
    """

    def __init__(self, config_provider: Callable[[], dict[str, Any]] | None = None):
        self._config_provider = config_provider or (lambda: {})

    def status(self) -> dict[str, Any]:
        config = self._config()
        providers = self._providers(config)
        cos = dict(config.get("cos") or {})
        return {
            "image_configured": bool(providers),
            "backup_image_configured": len(providers) > 1,
            "cos_configured": bool(
                cos.get("bucket") and cos.get("region") and cos.get("secret_id") and cos.get("secret_key")
            ),
            "image_provider_names": [item["name"] for item in providers],
        }

    def generate(
        self,
        *,
        stage: str,
        prompt: str,
        reference_values: Iterable[str],
    ) -> GeneratedMedia:
        config = self._config()
        providers = self._providers(config)
        if not providers:
            raise MediaConfigurationError("image provider is not configured")
        default_limit = 1 if stage == "grid_image" else 2
        reference_limit = max(
            1,
            min(
                int((config.get("limits") or {}).get(f"{stage}_reference_max_count") or default_limit),
                4,
            ),
        )
        references = self._load_references(reference_values, limit=reference_limit)
        if not references:
            raise MediaProcessingError("a confirmed source image is required for image processing")
        retries = max(1, min(int((config.get("limits") or {}).get("image_retry_attempts") or 3), 5))
        errors: list[str] = []
        for provider in self._provider_order(providers, config):
            for attempt in range(1, retries + 1):
                try:
                    content, content_type = self._request_edit(provider, prompt, references)
                    suffix = _suffix_for_content_type(content_type)
                    return GeneratedMedia(
                        stage=stage,
                        content=content,
                        content_type=content_type,
                        suffix=suffix,
                        provider=provider["name"],
                        model=provider["reference_model"] or provider["model"],
                        reference_count=len(references),
                    )
                except (requests.RequestException, TimeoutError, ValueError, MediaProcessingError) as exc:
                    errors.append(f"{provider['name']} attempt {attempt}: {_safe_error(exc)}")
        raise MediaProcessingError("; ".join(errors) or "image provider request failed")

    def split_four_grid(self, media: GeneratedMedia) -> list[GeneratedMedia]:
        """Split a generated 2x2 grid into four listing images plus its summary.

        The original workbench uses this contract for 店小秘 carousel images.
        Splitting happens locally so it does not consume four extra image API calls.

        对齐原项目 native_product_engine._split_4grid_to_jpegs / _square_image_to_jpeg_bytes：
        每格 trim 5% 边距后缩放到 DXM_IMAGE_TARGET_SIZE(800×800)，汇总图居中裁方后缩放到 800×800。
        """
        try:
            from PIL import Image  # type: ignore
        except ModuleNotFoundError as exc:
            raise MediaConfigurationError("Pillow is required for four-grid image processing") from exc
        try:
            source = Image.open(BytesIO(media.content)).convert("RGB")
            width, height = source.size
            if width < 2 or height < 2:
                raise ValueError("image is too small")
            x_mid, y_mid = width // 2, height // 2
            # 四象限：左上 → 右上 → 左下 → 右下（对齐原项目拆图顺序）
            boxes = (
                (0, 0, x_mid, y_mid),
                (x_mid, 0, width, y_mid),
                (0, y_mid, x_mid, height),
                (x_mid, y_mid, width, height),
            )
            panels = [source.crop(box) for box in boxes]
        except Exception as exc:
            raise MediaProcessingError("generated four-grid image cannot be split") from exc

        target_size = DXM_IMAGE_TARGET_SIZE
        result: list[GeneratedMedia] = []
        for index, panel in enumerate(panels, start=1):
            # 对齐原项目 _trim_grid_panel_margin：裁掉 5% 边距再缩放到 800×800
            trimmed = _trim_panel_margin(panel)
            resized = trimmed.resize((target_size, target_size), Image.Resampling.LANCZOS)
            content = _image_to_jpeg_bytes(resized)
            result.append(
                GeneratedMedia(
                    stage=f"grid_image_{index}",
                    content=content,
                    content_type="image/jpeg",
                    suffix=".jpg",
                    provider="local-split",
                    model="pillow",
                    reference_count=media.reference_count,
                )
            )
        # 汇总图：居中裁方 + 缩放到 800×800（对齐原项目 _square_image_to_jpeg_bytes）
        square = _center_crop_to_square(source)
        summary_resized = square.resize((target_size, target_size), Image.Resampling.LANCZOS)
        summary_content = _image_to_jpeg_bytes(summary_resized)
        result.append(
            GeneratedMedia(
                stage="grid_image_summary",
                content=summary_content,
                content_type="image/jpeg",
                suffix=".jpg",
                provider=media.provider,
                model=media.model,
                reference_count=media.reference_count,
            )
        )
        return result

    def upload_to_cos(
        self,
        media: GeneratedMedia,
        *,
        task_id: int,
        draft_id: int,
    ) -> str:
        config = self._config()
        cos = dict(config.get("cos") or {})
        required = ("bucket", "region", "secret_id", "secret_key")
        if not all(str(cos.get(key) or "").strip() for key in required):
            raise MediaConfigurationError("COS is not configured")
        try:
            from qcloud_cos import CosConfig, CosS3Client  # type: ignore
        except ModuleNotFoundError as exc:
            raise MediaConfigurationError("COS SDK is not installed") from exc
        bucket = str(cos["bucket"]).strip()
        region = str(cos["region"]).strip()
        prefix = str((config.get("updates") or {}).get("cos_prefix") or "product-processing").strip("/")
        date_path = datetime.now(timezone.utc).strftime("%Y/%m/%d")
        key = "/".join(
            part for part in (
                prefix or "product-processing",
                f"task_{task_id}",
                f"draft_{draft_id}",
                date_path,
                f"{media.stage}_{uuid.uuid4().hex}{media.suffix}",
            ) if part
        )
        try:
            client = CosS3Client(
                CosConfig(
                    Region=region,
                    SecretId=str(cos["secret_id"]),
                    SecretKey=str(cos["secret_key"]),
                    Timeout=60,
                )
            )
            client.put_object(Bucket=bucket, Key=key, Body=media.content, ContentType=media.content_type)
        except Exception as exc:
            raise MediaProcessingError(f"COS upload failed: {_safe_error(exc)}") from exc
        return f"https://{bucket}.cos.{region}.myqcloud.com/{key}"

    def _config(self) -> dict[str, Any]:
        value = self._config_provider()
        return value if isinstance(value, dict) else {}

    @staticmethod
    def _providers(config: dict[str, Any]) -> list[dict[str, str]]:
        providers: list[dict[str, str]] = []
        for name, section_name in (("primary", "image"), ("backup", "backup_image")):
            section = dict(config.get(section_name) or {})
            base_url = str(section.get("base_url") or "").strip().rstrip("/")
            api_key = str(section.get("api_key") or "").strip()
            model = str(section.get("model") or "").strip()
            if not (base_url and api_key and model):
                continue
            parsed = urlsplit(base_url)
            if parsed.scheme not in {"http", "https"} or not parsed.netloc:
                continue
            providers.append(
                {
                    "name": name,
                    "base_url": base_url,
                    "api_key": api_key,
                    "model": model,
                    "reference_model": str(section.get("reference_model") or "").strip(),
                }
            )
        return providers

    @staticmethod
    def _provider_order(providers: list[dict[str, str]], config: dict[str, Any]) -> list[dict[str, str]]:
        strategy = str((config.get("limits") or {}).get("image_provider_strategy") or "balanced").strip()
        if strategy == "backup_first" and len(providers) > 1:
            return [*providers[1:], providers[0]]
        return providers

    def _load_references(self, values: Iterable[str], *, limit: int) -> list[tuple[bytes, str, str]]:
        references: list[tuple[bytes, str, str]] = []
        seen: set[str] = set()
        for raw in values:
            value = str(raw or "").strip()
            if not value or value in seen:
                continue
            seen.add(value)
            if value.startswith("data:image/"):
                try:
                    header, payload = value.split(",", 1)
                    content = base64.b64decode(payload)
                except Exception as exc:
                    raise MediaProcessingError("reference data image is invalid") from exc
                references.append((content, "reference.png", _data_url_content_type(header)))
            elif Path(value).is_file():
                path = Path(value)
                references.append((path.read_bytes(), path.name, mimetypes.guess_type(path.name)[0] or "image/jpeg"))
            else:
                if not is_safe_external_url(value):
                    raise MediaProcessingError("reference image URL is not a safe public HTTP(S) URL")
                response = None
                try:
                    response = requests.get(value, timeout=30, allow_redirects=False)
                    response.raise_for_status()
                    content = bytes(response.content)
                    content_type = str(response.headers.get("Content-Type") or "image/jpeg").split(";", 1)[0]
                except requests.RequestException as exc:
                    raise MediaProcessingError("reference image download failed") from exc
                finally:
                    if response is not None:
                        response.close()
                if not content or not content_type.startswith("image/"):
                    raise MediaProcessingError("reference URL did not return an image")
                references.append((content, _filename_for_url(value), content_type))
            if len(references) >= limit:
                break
        return references

    @staticmethod
    def _request_edit(
        provider: dict[str, str],
        prompt: str,
        references: list[tuple[bytes, str, str]],
    ) -> tuple[bytes, str]:
        files: Any
        if len(references) == 1:
            content, filename, content_type = references[0]
            files = {"image": (filename, BytesIO(content), content_type)}
        else:
            files = [
                ("image[]", (filename, BytesIO(content), content_type))
                for content, filename, content_type in references
            ]
        response = requests.post(
            f"{provider['base_url']}/images/edits",
            headers={"Authorization": f"Bearer {provider['api_key']}"},
            data={
                "model": provider["reference_model"] or provider["model"],
                "prompt": prompt,
                "n": "1",
                "size": "1024x1024",
            },
            files=files,
            timeout=150,
        )
        try:
            if not response.ok:
                raise MediaProcessingError(f"provider returned HTTP {response.status_code}")
            payload = response.json()
        finally:
            response.close()
        item = ((payload.get("data") or [{}])[0] if isinstance(payload, dict) else {})
        if not isinstance(item, dict):
            raise MediaProcessingError("provider response is incompatible")
        if item.get("b64_json"):
            try:
                return base64.b64decode(str(item["b64_json"])), "image/png"
            except Exception as exc:
                raise MediaProcessingError("provider returned invalid image data") from exc
        url = str(item.get("url") or "").strip()
        if not url or not is_safe_external_url(url):
            raise MediaProcessingError("provider response does not contain a safe image result")
        downloaded = requests.get(url, timeout=60, allow_redirects=False)
        try:
            downloaded.raise_for_status()
            content = bytes(downloaded.content)
            content_type = str(downloaded.headers.get("Content-Type") or "image/jpeg").split(";", 1)[0]
        finally:
            downloaded.close()
        if not content or not content_type.startswith("image/"):
            raise MediaProcessingError("provider result is not an image")
        return content, content_type


def _safe_error(error: BaseException) -> str:
    message = str(error).replace("\n", " ").strip()
    return message[:180] or error.__class__.__name__


def _trim_panel_margin(image: Any) -> Any:
    """裁掉四宫格单格 5% 边距（对齐原项目 _trim_grid_panel_margin）。"""
    margin_w = int(image.size[0] * 0.05)
    margin_h = int(image.size[1] * 0.05)
    if margin_w > 0 and margin_h > 0 and image.size[0] > margin_w * 2 and image.size[1] > margin_h * 2:
        return image.crop((margin_w, margin_h, image.size[0] - margin_w, image.size[1] - margin_h))
    return image


def _center_crop_to_square(image: Any) -> Any:
    """居中裁切成正方形（对齐原项目 _square_image_to_jpeg_bytes）。"""
    width, height = image.size
    side = min(width, height)
    left = max((width - side) // 2, 0)
    top = max((height - side) // 2, 0)
    return image.crop((left, top, left + side, top + side))


def _image_to_jpeg_bytes(image: Any) -> bytes:
    """PIL Image → JPEG bytes, quality=DXM_IMAGE_JPEG_QUALITY（对齐原项目 _image_to_jpeg_bytes）。"""
    buffer = BytesIO()
    image.save(buffer, format="JPEG", quality=DXM_IMAGE_JPEG_QUALITY, optimize=True)
    return buffer.getvalue()


def _suffix_for_content_type(content_type: str) -> str:
    suffix = mimetypes.guess_extension(content_type or "") or ".png"
    return suffix if suffix in {".png", ".jpg", ".jpeg", ".webp"} else ".png"


def _data_url_content_type(header: str) -> str:
    value = header.removeprefix("data:").split(";", 1)[0]
    return value if value.startswith("image/") else "image/png"


def _filename_for_url(value: str) -> str:
    name = Path(urlsplit(value).path).name
    return name or "reference.jpg"
