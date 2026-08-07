from __future__ import annotations

import base64
import hashlib
import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ...db import transaction
from .schemas import SystemConfigUpdate


CONFIG_KEY = "system_config"
PRIMARY_AI_BASE_URL = "https://station-88.aicoming.top"

# 这些字段不进入普通配置 JSON，避免 GET 接口把密钥明文返回给前端。
SECRET_FIELDS: tuple[tuple[str, str], ...] = (
    ("ai", "api_key"),
    ("image", "api_key"),
    ("backup_image", "api_key"),
    ("cos", "secret_id"),
    ("cos", "secret_key"),
)


@dataclass(frozen=True)
class RuntimeAiConfig:
    """AI provider settings for backend-only business module calls."""

    base_url: str
    model: str
    api_key: str
    reference_model: str = ""

    @property
    def configured(self) -> bool:
        return bool(self.base_url and self.model and self.api_key)


@dataclass(frozen=True)
class RuntimeSystemConfig:
    """Decrypted runtime config consumed inside backend modules, never by frontend."""

    text_ai: RuntimeAiConfig
    image_ai: RuntimeAiConfig
    backup_image_ai: RuntimeAiConfig
    limits: dict[str, Any]
    updates: dict[str, Any]


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def default_system_config() -> dict[str, Any]:
    # 默认值来自旧工作台系统配置页和开发文档里的本地演示配置。
    # 文本/图片统一走低价档模型，控制 token 成本（见 provider_config 注释）。
    return {
        "ai": {"base_url": PRIMARY_AI_BASE_URL, "model": "gpt-5.6-terra"},
        "image": {
            "base_url": PRIMARY_AI_BASE_URL,
            "model": "gpt-image-2-1k",
            "reference_model": "gpt-image-2-1k",
        },
        "backup_image": {"base_url": "", "model": "", "reference_model": ""},
        "cos": {"bucket": "", "region": "ap-guangzhou"},
        "limits": {
            "text_workers": 30,
            "image_workers": 15,
            "text_request_limit": 30,
            "image_request_limit": 15,
            "image_retry_attempts": 3,
            "image_provider_strategy": "balanced",
            "provider_backup_share_percent": 0,
            "image_stop_after_billable_failure": True,
            "max_parallel_drafts": 5,
            "max_parallel_drafts_limit": 20,
        },
        "updates": {"cos_prefix": "temu-y2-control", "public_base_url": ""},
    }


@dataclass
class SystemConfigService:
    database_path: Path

    def get_config(self) -> dict[str, Any]:
        # 前端读取时拿到公开配置、密钥配置状态、以及一个便于展示/发布的摘要。
        config = self._load_public_config()
        secret_flags = self._secret_flags()
        return {"ok": True, **config, "secrets": secret_flags, "summary": self._summary(config, secret_flags)}

    def save_config(self, payload: SystemConfigUpdate, actor_id: str) -> dict[str, Any]:
        # 普通字段和密钥字段分开保存：普通字段进 workbench_settings，密钥进 secret_values。
        current = self._load_raw_config()
        merged = self._merge_public_fields(current, payload)
        now = utc_now()
        with transaction(self.database_path) as conn:
            conn.execute(
                """
                INSERT INTO workbench_settings(key, value_json, updated_by, updated_at)
                VALUES(?, ?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET
                    value_json = excluded.value_json,
                    updated_by = excluded.updated_by,
                    updated_at = excluded.updated_at
                """,
                (CONFIG_KEY, json.dumps(merged, ensure_ascii=False, sort_keys=True), actor_id, now),
            )
            self._apply_secret_updates(conn, payload, actor_id, now)
            conn.execute(
                """
                INSERT INTO action_logs(actor_id, action, target_type, target_id, request_json, result_json, created_at)
                VALUES(?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    actor_id,
                    "system_config.save",
                    "system_config",
                    CONFIG_KEY,
                    json.dumps(_redacted_payload(payload), ensure_ascii=False, sort_keys=True),
                    json.dumps({"ok": True}, ensure_ascii=False),
                    now,
                ),
            )
        result = self.get_config()
        result["message"] = "系统配置已保存"
        return result

    def publish_manifest(self, actor_id: str) -> dict[str, Any]:
        # 发布摘要只包含配置状态和 hash，方便后续同步给运行任务，不泄露密钥。
        config = self.get_config()
        manifest = {
            "version": "system-config-" + datetime.now().strftime("%Y%m%d_%H%M%S"),
            "created_at": utc_now(),
            "config_name": "system_config.json",
            "config_sha256": hashlib.sha256(
                json.dumps(config["summary"], ensure_ascii=False, sort_keys=True).encode("utf-8")
            ).hexdigest(),
            "summary": config["summary"],
            "required_restart": True,
        }
        with transaction(self.database_path) as conn:
            conn.execute(
                """
                INSERT INTO action_logs(actor_id, action, target_type, target_id, result_json, created_at)
                VALUES(?, ?, ?, ?, ?, ?)
                """,
                (
                    actor_id,
                    "system_config.publish_manifest",
                    "system_config",
                    CONFIG_KEY,
                    json.dumps(manifest, ensure_ascii=False, sort_keys=True),
                    utc_now(),
                ),
            )
        return {"ok": True, "manifest": manifest}

    def get_runtime_config(self) -> RuntimeSystemConfig:
        """Return decrypted settings for backend modules that need to call AI providers."""
        config = self._load_public_config()
        secrets = self._load_secret_values()
        return RuntimeSystemConfig(
            text_ai=RuntimeAiConfig(
                base_url=config["ai"].get("base_url", ""),
                model=config["ai"].get("model", ""),
                api_key=secrets.get(("ai", "api_key"), ""),
            ),
            image_ai=RuntimeAiConfig(
                base_url=config["image"].get("base_url", ""),
                model=config["image"].get("model", ""),
                reference_model=config["image"].get("reference_model", ""),
                api_key=secrets.get(("image", "api_key"), ""),
            ),
            backup_image_ai=RuntimeAiConfig(
                base_url=config["backup_image"].get("base_url", ""),
                model=config["backup_image"].get("model", ""),
                reference_model=config["backup_image"].get("reference_model", ""),
                api_key=secrets.get(("backup_image", "api_key"), ""),
            ),
            limits=dict(config["limits"]),
            updates=dict(config["updates"]),
        )

    def _load_raw_config(self) -> dict[str, Any]:
        # 数据库没有保存过配置时，直接返回默认配置，保证页面首次打开也能渲染。
        with transaction(self.database_path) as conn:
            row = conn.execute("SELECT value_json FROM workbench_settings WHERE key = ?", (CONFIG_KEY,)).fetchone()
        base = default_system_config()
        if row is None:
            return base
        try:
            loaded = json.loads(row["value_json"])
        except json.JSONDecodeError:
            return base
        return _with_fixed_provider_defaults(_deep_merge(base, loaded if isinstance(loaded, dict) else {}))

    def _load_public_config(self) -> dict[str, Any]:
        config = self._load_raw_config()
        return {
            "ai": dict(config["ai"]),
            "image": dict(config["image"]),
            "backup_image": dict(config["backup_image"]),
            "cos": dict(config["cos"]),
            "limits": dict(config["limits"]),
            "updates": dict(config["updates"]),
        }

    def _secret_flags(self) -> dict[str, dict[str, bool]]:
        # 页面只需要知道“已配置/未配置”，不需要也不应该拿到密钥原文。
        flags: dict[str, dict[str, bool]] = {}
        with transaction(self.database_path) as conn:
            rows = conn.execute("SELECT scope, name FROM secret_values").fetchall()
        configured = {(row["scope"], row["name"]) for row in rows}
        for scope, name in SECRET_FIELDS:
            flags.setdefault(scope, {})[f"{name}_configured"] = (scope, name) in configured
        return flags

    def _load_secret_values(self) -> dict[tuple[str, str], str]:
        # 只给后端运行时使用；前端 GET 接口仍然只能看到配置状态。
        with transaction(self.database_path) as conn:
            rows = conn.execute("SELECT scope, name, ciphertext FROM secret_values").fetchall()
        values: dict[tuple[str, str], str] = {}
        for row in rows:
            values[(row["scope"], row["name"])] = decrypt_secret(row["ciphertext"])
        return values

    def _merge_public_fields(self, current: dict[str, Any], payload: SystemConfigUpdate) -> dict[str, Any]:
        return {
            "ai": {"base_url": PRIMARY_AI_BASE_URL, "model": payload.ai.model.strip()},
            "image": {
                "base_url": PRIMARY_AI_BASE_URL,
                "model": payload.image.model.strip(),
                "reference_model": payload.image.reference_model.strip(),
            },
            "backup_image": {
                "base_url": payload.backup_image.base_url.strip(),
                "model": payload.backup_image.model.strip(),
                "reference_model": payload.backup_image.reference_model.strip(),
            },
            "cos": {
                "bucket": payload.cos.bucket.strip(),
                "region": payload.cos.region.strip() or current["cos"].get("region", "ap-guangzhou"),
            },
            "limits": payload.limits.model_dump(),
            "updates": payload.updates.model_dump(),
        }

    def _apply_secret_updates(self, conn: Any, payload: SystemConfigUpdate, actor_id: str, now: str) -> None:
        # 空字符串代表“留空不修改”，clear_* 才代表用户明确要求删除旧密钥。
        updates = {
            ("ai", "api_key"): (payload.ai.api_key, payload.ai.clear_api_key),
            ("image", "api_key"): (payload.image.api_key, payload.image.clear_api_key),
            ("backup_image", "api_key"): (payload.backup_image.api_key, payload.backup_image.clear_api_key),
            ("cos", "secret_id"): (payload.cos.secret_id, payload.cos.clear_secret_id),
            ("cos", "secret_key"): (payload.cos.secret_key, payload.cos.clear_secret_key),
        }
        for (scope, name), (value, clear) in updates.items():
            if clear:
                conn.execute("DELETE FROM secret_values WHERE scope = ? AND name = ?", (scope, name))
                continue
            if value is None or value == "":
                continue
            ciphertext = encrypt_secret(value)
            conn.execute(
                """
                INSERT INTO secret_values(scope, name, ciphertext, updated_by, updated_at)
                VALUES(?, ?, ?, ?, ?)
                ON CONFLICT(scope, name) DO UPDATE SET
                    ciphertext = excluded.ciphertext,
                    updated_by = excluded.updated_by,
                    updated_at = excluded.updated_at
                """,
                (scope, name, ciphertext, actor_id, now),
            )

    @staticmethod
    def _summary(config: dict[str, Any], secret_flags: dict[str, dict[str, bool]]) -> dict[str, Any]:
        return {
            "ai_configured": bool(config["ai"].get("base_url") and secret_flags.get("ai", {}).get("api_key_configured")),
            "image_configured": bool(
                config["image"].get("base_url") and secret_flags.get("image", {}).get("api_key_configured")
            ),
            "backup_image_configured": bool(
                config["backup_image"].get("base_url")
                and secret_flags.get("backup_image", {}).get("api_key_configured")
            ),
            "cos_configured": bool(
                config["cos"].get("bucket")
                and config["cos"].get("region")
                and secret_flags.get("cos", {}).get("secret_id_configured")
                and secret_flags.get("cos", {}).get("secret_key_configured")
            ),
            "text_workers": config["limits"].get("text_workers"),
            "image_workers": config["limits"].get("image_workers"),
            "cos_region": config["cos"].get("region"),
            "update_public_base_url_configured": bool(config["updates"].get("public_base_url")),
        }


def encrypt_secret(value: str) -> str:
    # 使用 AES-GCM 加密后落库；缺少 cryptography 时返回 503，提醒补依赖。
    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    except ModuleNotFoundError as exc:
        raise RuntimeError("cryptography is required to store secrets with AES-256-GCM") from exc
    nonce = os.urandom(12)
    encrypted = AESGCM(_secret_key()).encrypt(nonce, value.encode("utf-8"), None)
    return "AESGCM:" + base64.urlsafe_b64encode(nonce + encrypted).decode("ascii")


def decrypt_secret(ciphertext: str) -> str:
    # 仅供后端业务模块运行时读取密钥；不要通过前端接口返回这个结果。
    if not ciphertext:
        return ""
    if not ciphertext.startswith("AESGCM:"):
        raise RuntimeError("unsupported secret ciphertext format")
    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    except ModuleNotFoundError as exc:
        raise RuntimeError("cryptography is required to read stored secrets with AES-256-GCM") from exc
    try:
        raw = base64.urlsafe_b64decode(ciphertext.removeprefix("AESGCM:").encode("ascii"))
        nonce, encrypted = raw[:12], raw[12:]
        return AESGCM(_secret_key()).decrypt(nonce, encrypted, None).decode("utf-8")
    except Exception as exc:
        raise RuntimeError("stored secret could not be decrypted") from exc


def _secret_key() -> bytes:
    # 生产环境建议通过 WH_LOCAL_SECRET_KEY 固定密钥；开发环境用机器信息派生。
    raw = os.environ.get("WH_LOCAL_SECRET_KEY", "").strip()
    if raw:
        return hashlib.sha256(raw.encode("utf-8")).digest()
    machine_seed = f"{os.environ.get('COMPUTERNAME', '')}:{os.environ.get('USERNAME', '')}:wh-local-runtime"
    return hashlib.sha256(machine_seed.encode("utf-8")).digest()


def _deep_merge(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    result = dict(base)
    for key, value in overlay.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def _with_fixed_provider_defaults(config: dict[str, Any]) -> dict[str, Any]:
    """Keep hidden provider base URLs fixed even when an old local DB has stale values."""
    config["ai"]["base_url"] = PRIMARY_AI_BASE_URL
    config["image"]["base_url"] = PRIMARY_AI_BASE_URL
    return config


def _redacted_payload(payload: SystemConfigUpdate) -> dict[str, Any]:
    data = payload.model_dump()
    for scope, name in SECRET_FIELDS:
        section = data.get(scope)
        if isinstance(section, dict) and name in section:
            section[name] = "<redacted>" if section.get(name) else ""
    return data
