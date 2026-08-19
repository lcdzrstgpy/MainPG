# -*- coding: utf-8 -*-
"""产品处理测试共用环境配置。"""

import os
import socket
from types import SimpleNamespace

import pytest

# 测试环境关闭 AI 供应商请求速率限制，避免令牌桶等待拖慢用例；
# 速率限制逻辑本身由 test_product_processing_rate_limit.py 单独覆盖。
os.environ.setdefault("WH_PRODUCT_AI_RATE_PER_MINUTE", "0")


@pytest.fixture(autouse=True)
def _reset_ai_rate_limiter():
    """每个测试前后重置全局限速器单例。

    测试中 setenv + reset_limiter() 会替换单例（如按 60/min 重建），
    若不重置，后续测试会继续被该速率限制，导致用例缓慢甚至超时。
    """
    from wh_local.modules.product_processing.infrastructure.rate_limit import reset_limiter

    reset_limiter()
    yield
    reset_limiter()


@pytest.fixture(autouse=True)
def _deterministic_public_dns(monkeypatch):
    """Keep public-host tests independent of the runner's network/DNS policy."""
    from wh_local.modules.product_processing.domain import policy

    monkeypatch.setattr(
        policy,
        "socket",
        SimpleNamespace(
            SOCK_STREAM=socket.SOCK_STREAM,
            getaddrinfo=lambda _host, port, **_kwargs: [
                (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", port))
            ],
        ),
    )
    yield
