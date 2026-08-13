"""OpenAI 兼容的 AI 客户端，供产品处理模块生成标题/描述与商品图片。

中转地址与 key 来自 provider_config；调用失败时抛出 AiProviderError，
由上层决定降级策略（当前处理流水线暂未接入真实调用，仅保留调用能力与连通性探测）。
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from typing import Any

from .provider_config import DEFAULT_AI_TIMEOUT_SECONDS, resolve_ai_provider


AI_USER_AGENT = "MainPG-ProductProcessing/1.0"


class AiProviderError(RuntimeError):
    """AI 中转调用失败。"""

    def __init__(self, message: str, *, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


class AiClient:
    def __init__(self) -> None:
        provider = resolve_ai_provider()
        if not provider["api_key"]:
            raise AiProviderError("AI api key is not configured")
        self.base_url = provider["base_url"]
        self.api_key = provider["api_key"]
        self.text_model = provider["text_model"]
        self.text_model_fallback_order = provider["text_model_fallback_order"]
        self.image_model = provider["image_model"]
        self.image_size = provider["image_size"]
        self.image_quality = provider["image_quality"]
        self.timeout_seconds = float(provider.get("timeout_seconds", DEFAULT_AI_TIMEOUT_SECONDS))
        self.text_timeout_seconds = float(provider.get("text_timeout_seconds", 300.0))
        # 兼容调用方未提供总预算的旧配置：保留原本“每个候选各自等待”的量级；
        # 新配置则把整条降级链限制在明确总预算内，慢主模型不会被 25 秒误取消。
        self.text_total_timeout_seconds = float(
            provider.get("text_total_timeout_seconds", self.text_timeout_seconds * 4)
        )
        self.image_timeout_seconds = float(provider.get("image_timeout_seconds", 600.0))

    def chat(self, messages: list[dict[str, Any]], *, model: str | None = None) -> str:
        """调用 chat/completions，返回首个 assistant 文本。

        按 ``text_model_fallback_order`` 自动降级：仅连接类失败（超时/网络错误/5xx/429）
        才依次尝试后续模型，业务 4xx 直接失败，避免无意义的等待与调用。
        """
        last_error: AiProviderError | None = None
        candidates = self._text_model_chain(model)
        deadline = time.monotonic() + max(self.text_timeout_seconds, self.text_total_timeout_seconds)
        for candidate in candidates:
            remaining_seconds = deadline - time.monotonic()
            if remaining_seconds <= 0:
                break
            payload = {
                "model": candidate,
                "messages": messages,
                "temperature": 0.7,
            }
            try:
                data = self._post(
                    "/chat/completions",
                    payload,
                    timeout=min(self.text_timeout_seconds, remaining_seconds),
                )
                return str(data["choices"][0]["message"]["content"] or "").strip()
            except (KeyError, IndexError, TypeError) as exc:
                raise AiProviderError(f"unexpected chat response: {data}") from exc
            except AiProviderError as exc:
                last_error = exc
                status = getattr(exc, "status_code", None)
                if status is not None and 400 <= status < 500 and status != 429:
                    raise  # 业务级 4xx（参数/鉴权错误）重试其他模型也无意义，直接失败
                continue
        raise last_error or AiProviderError("text generation exceeded its total timeout budget")

    def _text_model_chain(self, model: str | None) -> list[str]:
        """主模型（显式指定 > 配置文本模型）加 fallback 链，去重保序。"""
        chain: list[str] = []
        for candidate in (model, self.text_model, *self.text_model_fallback_order):
            if candidate and candidate not in chain:
                chain.append(candidate)
        return chain

    def generate_image(self, prompt: str, *, model: str | None = None, size: str | None = None) -> str:
        """调用 images/generations，返回生成的图片 URL 或 b64 json。"""
        payload: dict[str, Any] = {
            "model": model or self.image_model,
            "prompt": prompt,
            "size": size or self.image_size,
            "n": 1,
            "quality": self.image_quality,
        }
        data = self._post("/images/generations", payload, timeout=self.image_timeout_seconds)
        try:
            item = data["data"][0]
        except (KeyError, IndexError, TypeError) as exc:
            raise AiProviderError(f"unexpected image response: {data}") from exc
        return str(item.get("url") or item.get("b64_json") or "").strip()

    def ping(self) -> dict[str, Any]:
        """探测中转连通性（返回模型清单），失败抛 AiProviderError。"""
        data = self._get("/models")
        names = [str(model.get("id", "")) for model in data.get("data", []) if isinstance(model, dict)]
        return {
            "ok": True,
            "base_url": self.base_url,
            "model_count": len(names),
            "models_sample": names[:10],
        }

    def _post(self, path: str, payload: dict[str, Any], *, timeout: float | None = None) -> dict[str, Any]:
        request = urllib.request.Request(
            f"{self.base_url}{path}",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
                "User-Agent": AI_USER_AGENT,
            },
            method="POST",
        )
        return self._send(request, timeout=timeout)

    def _get(self, path: str) -> dict[str, Any]:
        request = urllib.request.Request(
            f"{self.base_url}{path}",
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "User-Agent": AI_USER_AGENT,
            },
            method="GET",
        )
        return self._send(request)

    def _send(self, request: urllib.request.Request, *, timeout: float | None = None) -> dict[str, Any]:
        effective_timeout = timeout if timeout is not None else self.timeout_seconds
        try:
            with urllib.request.urlopen(request, timeout=effective_timeout) as response:
                body = response.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:300]
            raise AiProviderError(f"AI provider HTTP {exc.code}: {detail}", status_code=exc.code) from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise AiProviderError(f"AI provider unreachable: {exc}") from exc
        try:
            return json.loads(body)
        except json.JSONDecodeError as exc:
            raise AiProviderError(f"AI provider returned invalid JSON: {body[:200]}") from exc
