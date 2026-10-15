"""客户端内置直连密钥（兜底）。

产品处理固定客户端直连：正常情况下使用批次冻结（/batch/freeze）下发的短期
provider 密钥，AI 请求由客户端直接发往第三方（火山方舟 ark / 无印 wuyin）。

维护措施（保证生图/文本请求确实发生在客户端）：
为了让「服务端密钥签发异常导致客户端拿不到密钥」不再触发缺密钥中止，客户端
内置平台密钥作为兜底——即便服务端未下发，生图/文本仍由客户端直连第三方，是否
可用最终由服务端冻结（账户余额/积分门控）决定，绝不回落到平台托管网关。

取值优先级（高 → 低）：
1. 批次冻结下发的短期密钥（server_ai_proxy 的任务级上下文，见 granted_key）
2. 部署/构建环境变量：WH_BUILTIN_WUYIN_KEY / WH_BUILTIN_ARK_KEY
3. 随客户端打包的资源文件 builtin_credentials.json（{"wuyin": "...", "ark": "..."}）
4. 本模块源码常量（打包进客户端代码）

真实密钥为空表示该 provider 无兜底；此时缺密钥的任务仍会中止并把具体原因上报
服务器（见 service._execute_task_direct），由服务端侧排查密钥签发。
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

# 打包进客户端代码的兜底密钥；生产构建应填入平台实际密钥（或改用环境变量/资源文件注入）。
BUILTIN_WUYIN_KEY = ""
BUILTIN_ARK_KEY = ""

# 随客户端打包的可选资源文件（与本源文件同目录）。
_RESOURCE_FILENAME = "builtin_credentials.json"

_PROVIDER_ENV_NAMES = {
    "wuyin": "WH_BUILTIN_WUYIN_KEY",
    "ark": "WH_BUILTIN_ARK_KEY",
}
_PROVIDER_CONSTANTS = {
    "wuyin": BUILTIN_WUYIN_KEY,
    "ark": BUILTIN_ARK_KEY,
}

_resource_cache: dict[str, str] | None = None


def _resource_keys() -> dict[str, str]:
    """读取随客户端打包的兜底密钥资源文件；缺失/损坏时视为无兜底。"""
    global _resource_cache
    if _resource_cache is not None:
        return _resource_cache
    loaded: dict[str, str] = {}
    path = Path(__file__).with_name(_RESOURCE_FILENAME)
    if path.is_file():
        try:
            raw: Any = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raw = None
        if isinstance(raw, dict):
            loaded = {str(key): str(value) for key, value in raw.items() if str(value).strip()}
    _resource_cache = loaded
    return loaded


def builtin_provider_key(provider: str) -> str:
    """返回内置兜底密钥：环境变量 > 打包资源文件 > 源码常量。无兜底返回空串。"""
    name = str(provider or "").strip()
    if name not in _PROVIDER_CONSTANTS:
        return ""
    env_name = _PROVIDER_ENV_NAMES.get(name)
    if env_name:
        from_env = str(os.environ.get(env_name) or "").strip()
        if from_env:
            return from_env
    resource = _resource_keys().get(name)
    if resource:
        return resource
    return str(_PROVIDER_CONSTANTS.get(name) or "").strip()
