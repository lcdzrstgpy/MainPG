"""OpenAI 兼容的 AI 客户端，供产品处理模块生成标题/描述与商品图片。

中转地址与 key 来自 provider_config；调用失败时抛出 AiProviderError，
由上层决定降级策略（当前处理流水线暂未接入真实调用，仅保留调用能力与连通性探测）。
"""

from __future__ import annotations

import json
import re
import threading
import time
from collections.abc import Mapping
from typing import Any

import requests

from .infrastructure.rate_limit import global_ai_request_limiter
from .provider_config import DEFAULT_AI_TIMEOUT_SECONDS, resolve_ai_provider


AI_USER_AGENT = "MainPG-ProductProcessing/1.0"
KEY_HAS_NO_ROUTE_PROVIDERS = "key_has_no_route_providers"
HTTP_POOL_CONNECTIONS = 4
HTTP_POOL_MAXSIZE = 8
ROUTE_PROBE_FOLLOWER_WAIT_SECONDS = 0.25
MODEL_TRANSIENT_FAILURE_THRESHOLD = 2
MODEL_COOLDOWN_SECONDS = 30.0

# 进程内共享的有界连接池。Authorization 始终按请求传入，不写入 Session
# 默认头；响应读取后立即关闭归还连接，也不保存响应对象或正文。
_HTTP_SESSION = requests.Session()
_HTTP_ADAPTER = requests.adapters.HTTPAdapter(
    pool_connections=HTTP_POOL_CONNECTIONS,
    pool_maxsize=HTTP_POOL_MAXSIZE,
    max_retries=0,
    pool_block=True,
)
_HTTP_SESSION.mount("https://", _HTTP_ADAPTER)
_HTTP_SESSION.mount("http://", _HTTP_ADAPTER)
_HTTP_CAPACITY = threading.BoundedSemaphore(HTTP_POOL_MAXSIZE)


class AiProviderError(RuntimeError):
    """AI 中转调用失败。"""

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        provider_code: str = "",
    ):
        super().__init__(message)
        self.status_code = status_code
        self.provider_code = str(provider_code or "").strip()


def is_key_no_route_provider_error(error: BaseException) -> bool:
    """Whether the provider structurally reported that this key has no route."""
    return (
        isinstance(error, AiProviderError)
        and error.provider_code == KEY_HAS_NO_ROUTE_PROVIDERS
    )


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
        # 兼容调用方未提供总预算的旧配置：保留原本"每个候选各自等待"的量级；
        # 新配置则把整条降级链限制在明确总预算内，慢主模型不会被 25 秒误取消。
        self.text_total_timeout_seconds = float(
            provider.get("text_total_timeout_seconds", self.text_timeout_seconds * 4)
        )
        self.image_timeout_seconds = float(provider.get("image_timeout_seconds", 600.0))
        # One task shares one AiClient across product workers. Probe each model route
        # once; workers waiting behind an unroutable model skip it immediately after
        # the first structured no-route response instead of repeating the same 400.
        self._route_states: dict[str, str] = {}
        self._route_condition = threading.Condition()
        self._model_transient_failures: dict[str, int] = {}
        self._model_cooldown_until: dict[str, float] = {}

    def chat(self, messages: list[dict[str, Any]], *, model: str | None = None) -> str:
        """调用 chat/completions，返回首个 assistant 文本。

        按 ``text_model_fallback_order`` 自动降级：连接类失败（超时/网络错误/5xx/429）
        和当前 key 对该模型无路由时尝试后续模型；其他业务 4xx 直接失败。
        """
        last_error: AiProviderError | None = None
        last_non_no_route_error: AiProviderError | None = None
        all_attempts_no_route = True
        attempted_count = 0
        candidates = self._text_model_chain(model)
        deadline = time.monotonic() + max(self.text_timeout_seconds, self.text_total_timeout_seconds)
        for candidate in candidates:
            remaining_seconds = deadline - time.monotonic()
            if remaining_seconds <= 0:
                break
            if self._is_model_cooling_down(candidate):
                all_attempts_no_route = False
                continue
            attempted_count += 1
            route_claim = self._claim_model_route(candidate, remaining_seconds)
            if not route_claim:
                last_error = AiProviderError(
                    f"AI provider has no route for model {candidate}",
                    status_code=400,
                    provider_code=KEY_HAS_NO_ROUTE_PROVIDERS,
                )
                continue
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
                self._mark_model_route(candidate, available=True)
                content = str(data["choices"][0]["message"]["content"] or "").strip()
                self._record_model_success(candidate)
                return content
            except (KeyError, IndexError, TypeError) as exc:
                self._mark_model_route(candidate, available=True)
                raise AiProviderError(f"unexpected chat response: {data}") from exc
            except AiProviderError as exc:
                last_error = exc
                no_route = is_key_no_route_provider_error(exc)
                self._mark_model_route(candidate, available=not no_route)
                if not no_route:
                    all_attempts_no_route = False
                    last_non_no_route_error = exc
                    self._record_transient_model_failure(candidate, exc)
                status = getattr(exc, "status_code", None)
                if status is not None and 400 <= status < 500 and status != 429:
                    if no_route:
                        continue
                    raise  # 业务级 4xx（参数/鉴权错误）重试其他模型也无意义，直接失败
                continue
        if (
            last_error is not None
            and all_attempts_no_route
            and attempted_count == len(candidates)
        ):
            raise last_error
        if last_non_no_route_error is not None:
            raise last_non_no_route_error
        raise AiProviderError("text generation exceeded its total timeout budget")

    def _claim_model_route(self, model: str, timeout_seconds: float) -> bool:
        """Briefly single-flight an unknown route, then let healthy slow calls fan out."""
        wait_seconds = min(
            max(0.0, timeout_seconds),
            ROUTE_PROBE_FOLLOWER_WAIT_SECONDS,
        )
        deadline = time.monotonic() + wait_seconds
        with self._route_condition:
            while self._route_states.get(model) == "probing":
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    # The first call is slow rather than a fast structured no-route.
                    # Continue with the same configured model without speculative
                    # cancellation or output racing.
                    return True
                self._route_condition.wait(timeout=remaining)
            state = self._route_states.get(model)
            if state == "unavailable":
                return False
            if state == "available":
                return True
            self._route_states[model] = "probing"
            return True

    def _is_model_cooling_down(self, model: str) -> bool:
        with self._route_condition:
            cooldown_until = self._model_cooldown_until.get(model, 0.0)
            if cooldown_until <= time.monotonic():
                self._model_cooldown_until.pop(model, None)
                return False
            return True

    def _record_transient_model_failure(
        self,
        model: str,
        error: AiProviderError,
    ) -> None:
        if not _is_transient_model_failure(error):
            return
        with self._route_condition:
            failures = self._model_transient_failures.get(model, 0) + 1
            self._model_transient_failures[model] = failures
            if failures >= MODEL_TRANSIENT_FAILURE_THRESHOLD:
                self._model_cooldown_until[model] = (
                    time.monotonic() + MODEL_COOLDOWN_SECONDS
                )

    def _record_model_success(self, model: str) -> None:
        with self._route_condition:
            self._model_transient_failures.pop(model, None)
            self._model_cooldown_until.pop(model, None)

    def _mark_model_route(self, model: str, *, available: bool) -> None:
        with self._route_condition:
            current = self._route_states.get(model)
            # A follower may fan out after the brief probe window and establish a
            # healthy route before the original probe finishes.  A late no-route
            # response from that original request must not overwrite newer success.
            if not available and current == "available":
                self._route_condition.notify_all()
                return
            self._route_states[model] = "available" if available else "unavailable"
            self._route_condition.notify_all()

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
        return self._send("POST", path, payload=payload, timeout=timeout)

    def _get(self, path: str) -> dict[str, Any]:
        return self._send("GET", path)

    def _send(
        self,
        method: str,
        path: str,
        *,
        payload: dict[str, Any] | None = None,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        effective_timeout = float(timeout if timeout is not None else self.timeout_seconds)
        # 全局速率限制：先取令牌再拿连接，避免多任务叠加时短窗口内请求量过大。
        global_ai_request_limiter().acquire()
        deadline = time.monotonic() + effective_timeout
        if not _HTTP_CAPACITY.acquire(timeout=effective_timeout):
            raise AiProviderError("AI provider connection pool timed out")
        response: requests.Response | None = None
        try:
            remaining_timeout = max(0.001, deadline - time.monotonic())
            headers = {
                "Authorization": f"Bearer {self.api_key}",
                "User-Agent": AI_USER_AGENT,
            }
            if payload is not None:
                headers["Content-Type"] = "application/json"
            response = _HTTP_SESSION.request(
                method,
                f"{self.base_url}{path}",
                headers=headers,
                json=payload,
                timeout=remaining_timeout,
            )
            body = response.content.decode("utf-8", errors="replace")
        except (requests.RequestException, TimeoutError, OSError) as exc:
            raise AiProviderError(f"AI provider unreachable: {exc}") from exc
        finally:
            # The response is fully materialized above. Closing releases the
            # socket back to urllib3's keep-alive pool; no response is cached.
            if response is not None:
                response.close()
            _HTTP_CAPACITY.release()
        if response.status_code >= 400:
            detail = body[:300]
            raise AiProviderError(
                f"AI provider HTTP {response.status_code}: {detail}",
                status_code=response.status_code,
                provider_code=_provider_error_code(body),
            )
        try:
            return json.loads(body)
        except json.JSONDecodeError as exc:
            raise AiProviderError(f"AI provider returned invalid JSON: {body[:200]}") from exc


def _provider_error_code(body: str) -> str:
    """Extract a provider code from JSON fields without substring guessing."""
    try:
        payload = json.loads(body)
    except (TypeError, json.JSONDecodeError):
        return ""
    if not isinstance(payload, Mapping):
        return ""

    candidates: list[object] = [payload.get("code"), payload.get("error_code")]
    for key in ("error", "detail"):
        value = payload.get(key)
        if isinstance(value, Mapping):
            candidates.extend((value.get("code"), value.get("error_code")))
        else:
            candidates.append(value)
    for candidate in candidates:
        if not isinstance(candidate, str):
            continue
        code = candidate.strip()
        if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}", code):
            return code
    return ""


def _is_transient_model_failure(error: AiProviderError) -> bool:
    status = error.status_code
    if status == 429 or (status is not None and status >= 500):
        return True
    cause = error.__cause__
    return isinstance(cause, (requests.Timeout, TimeoutError))
