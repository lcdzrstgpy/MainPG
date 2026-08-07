"""OpenAI 兼容的 AI 客户端，供产品处理模块生成标题/描述与商品图片。

中转地址与 key 来自 provider_config；调用失败时抛出 AiProviderError，
由上层决定降级策略（当前处理流水线暂未接入真实调用，仅保留调用能力与连通性探测）。
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any

from .provider_config import DEFAULT_AI_TIMEOUT_SECONDS, resolve_ai_provider


class AiProviderError(RuntimeError):
    """AI 中转调用失败。"""


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

    def chat(self, messages: list[dict[str, Any]], *, model: str | None = None) -> str:
        """调用 chat/completions，返回首个 assistant 文本。

        按 ``text_model_fallback_order`` 自动降级：主模型调用失败（超时/4xx/5xx）时
        依次尝试后续模型，全部失败才抛最后一个错误。
        """
        last_error: AiProviderError | None = None
        for candidate in self._text_model_chain(model):
            payload = {
                "model": candidate,
                "messages": messages,
                "temperature": 0.7,
            }
            try:
                data = self._post("/chat/completions", payload)
                return str(data["choices"][0]["message"]["content"] or "").strip()
            except (KeyError, IndexError, TypeError) as exc:
                raise AiProviderError(f"unexpected chat response: {data}") from exc
            except AiProviderError as exc:
                last_error = exc
                continue
        raise last_error or AiProviderError("no text model candidate available")

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
        data = self._post("/images/generations", payload)
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

    def _post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        request = urllib.request.Request(
            f"{self.base_url}{path}",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        return self._send(request)

    def _get(self, path: str) -> dict[str, Any]:
        request = urllib.request.Request(
            f"{self.base_url}{path}",
            headers={"Authorization": f"Bearer {self.api_key}"},
            method="GET",
        )
        return self._send(request)

    def _send(self, request: urllib.request.Request) -> dict[str, Any]:
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                body = response.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:300]
            raise AiProviderError(f"AI provider HTTP {exc.code}: {detail}") from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise AiProviderError(f"AI provider unreachable: {exc}") from exc
        try:
            return json.loads(body)
        except json.JSONDecodeError as exc:
            raise AiProviderError(f"AI provider returned invalid JSON: {body[:200]}") from exc
