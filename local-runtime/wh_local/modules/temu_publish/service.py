"""Temu 商品发布配套服务。

职责：
- 凭证存取：服务商 client/token 与店铺 access_token 加密存入 secret_values，环境变量兜底。
- 发布配套查询：类目、属性、父规格、包装清单、运费模板、仓库、图片上传。
- 发布请求体组装与校验：只生成 goods.add 的 payload，不提交。
"""

from __future__ import annotations

import base64
import hashlib
import ipaddress
import json
import os
import socket
import time
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import requests

from ...db import transaction
from ..basic_settings.service import decrypt_secret, encrypt_secret
from .client import (
    TemuCredentials,
    TemuCredentialMissing,
    TemuGatewayError,
    TemuProductClient,
)
from .schemas import DetailImageInput, GoodsDraftInput
from . import submissions as submission_ledger

SECRET_SCOPE = "temu_publish"
# 每个账号一份凭证：scope 形如 temu_publish:cust_xxx。
# 历史的全局 scope（temu_publish）只在「当初保存它的人」第一次登录时迁到自己名下，
# 绝不共用 —— 否则 A 会拿着 B 的 token 把商品发到别人店铺。
LEGACY_SCOPE = SECRET_SCOPE
# 历史全局凭证里，updated_by 是这些标记的表示“系统初始化写入的”，没有真正的归属账号。
# 这种凭证允许被第一个登录的账号认领一次（认领后改成该账号），之后别人不会自动继承。
BOOTSTRAP_ACTORS = {"", "system-bootstrap", "local-demo-admin", "system"}
SECRET_NAMES = ("yl_client", "yl_token", "access_token")
# 多店铺：每个店铺存成一条 name = "shop:<id>" 的加密 JSON（含店铺名与 token），
# 当前启用的店铺 id 存在 "active_shop"；`access_token` 始终是「当前店铺」的镜像，
# 这样 client()/发布等所有既有链路都不用改。
SHOP_PREFIX = "shop:"
ACTIVE_SHOP_NAME = "active_shop"
ENV_FALLBACKS = {
    "yl_client": "WH_TEMU_YL_CLIENT",
    "yl_token": "WH_TEMU_YL_TOKEN",
    "access_token": "WH_TEMU_ACCESS_TOKEN",
}


class TemuCredentialStore:
    """按账号存取凭证。优先级：本人数据库加密存储 > 历史迁移 > 环境变量。"""

    def __init__(self, database_path: Path, owner_id: str = "") -> None:
        self.database_path = database_path
        self.owner_id = str(owner_id or "").strip()

    def scope(self) -> str:
        """账号维度作用域；没有账号上下文（脚本/测试）时退回全局作用域。"""
        return f"{SECRET_SCOPE}:{self.owner_id}" if self.owner_id else LEGACY_SCOPE

    def load(self) -> TemuCredentials:
        values = self._load_values()
        return TemuCredentials(
            client=values.get("yl_client", ""),
            token=values.get("yl_token", ""),
            access_token=values.get("access_token", ""),
        )

    def _load_values(self) -> dict[str, str]:
        stored = self._load_stored()
        if not stored:
            # 第一次用：把「当初自己配的」历史全局凭证迁到本人名下
            self._adopt_legacy()
            stored = self._load_stored()
        values: dict[str, str] = {}
        for name in SECRET_NAMES:
            value = stored.get(name) or os.environ.get(ENV_FALLBACKS[name], "")
            values[name] = str(value or "").strip()
        return values

    def _load_stored(self) -> dict[str, str]:
        with transaction(self.database_path) as conn:
            rows = conn.execute(
                "SELECT name, ciphertext FROM secret_values WHERE scope = ?", (self.scope(),)
            ).fetchall()
        values: dict[str, str] = {}
        for row in rows:
            name = str(row["name"])
            if name in SECRET_NAMES:
                try:
                    values[name] = decrypt_secret(str(row["ciphertext"]))
                except RuntimeError:
                    # 单条密文损坏不应让整个设置页崩掉；按未配置处理。
                    continue
        return values

    def _adopt_legacy(self) -> None:
        """历史全局凭证迁移。

        规则：
        1. updated_by 就是当前账号 → 直接迁到自己名下；
        2. 全部是系统初始化的（BOOTSTRAP_ACTORS）→ 让第一个来的账号认领一次，
           认领后把历史行的 updated_by 改成该账号，后面的人不会再继承 —— 否则
           第二个账号登录就会拿着同一个 token 往别人店铺发商品。
        3. 是别的账号配的 → 一律不迁。
        """
        if not self.owner_id:
            return
        with transaction(self.database_path) as conn:
            rows = conn.execute(
                "SELECT name, ciphertext, updated_by FROM secret_values WHERE scope = ?",
                (LEGACY_SCOPE,),
            ).fetchall()
            if not rows:
                return
            # 只看凭证字段：shop:/active_shop 是派生数据，混进来会让判定失真
            rows = [row for row in rows if str(row["name"]) in SECRET_NAMES]
            if not rows:
                return
            owners = {str(row["updated_by"] or "") for row in rows}
            mine = owners == {self.owner_id}
            claimable = owners <= BOOTSTRAP_ACTORS
            if not mine and not claimable:
                return
            now = datetime.now().astimezone().isoformat(timespec="seconds")
            for row in rows:
                conn.execute(
                    """
                    INSERT INTO secret_values(scope, name, ciphertext, updated_by, updated_at)
                    VALUES(?, ?, ?, ?, ?)
                    ON CONFLICT(scope, name) DO UPDATE SET
                        ciphertext = excluded.ciphertext,
                        updated_by = excluded.updated_by,
                        updated_at = excluded.updated_at
                    """,
                    (self.scope(), str(row["name"]), str(row["ciphertext"]), self.owner_id, now),
                )
            if claimable:
                for row in rows:
                    conn.execute(
                        "UPDATE secret_values SET updated_by = ? WHERE scope = ? AND name = ?",
                        (self.owner_id, LEGACY_SCOPE, str(row["name"])),
                    )

    def clear(self, actor_id: str) -> None:
        """解绑本人的店铺：**只删店铺授权（access_token）**。

        `yl_client` / `yl_token` 是服务商级别的凭据，跟具体哪家店铺无关：
        换一家店、重新授权都还要用同一对，所以解绑时保留，否则每次换店都得让对方再发一遍。

        历史全局作用域（LEGACY_SCOPE）里 `updated_by == 本人` 的 access_token 也要删：
        只删自己 scope 的话，下次读凭证 `_adopt_legacy` 会把它原样迁回来（解绑了又复活）。
        """
        with transaction(self.database_path) as conn:
            conn.execute(
                "DELETE FROM secret_values WHERE scope = ? AND name IN (?, ?)",
                (self.scope(), "access_token", ACTIVE_SHOP_NAME),
            )
            if self.owner_id:
                conn.execute(
                    "DELETE FROM secret_values WHERE scope = ? AND name = ? AND updated_by = ?",
                    (LEGACY_SCOPE, "access_token", self.owner_id),
                )

    def save(self, payload: Any, actor_id: str) -> None:
        updates = {
            "yl_client": (payload.yl_client, payload.clear_yl_client),
            "yl_token": (payload.yl_token, payload.clear_yl_token),
            "access_token": (payload.access_token, payload.clear_access_token),
        }
        now = datetime.now().astimezone().isoformat(timespec="seconds")
        with transaction(self.database_path) as conn:
            for name, (value, clear) in updates.items():
                if clear:
                    conn.execute(
                        "DELETE FROM secret_values WHERE scope = ? AND name = ?",
                        (self.scope(), name),
                    )
                    continue
                if value is None or str(value).strip() == "":
                    continue
                conn.execute(
                    """
                    INSERT INTO secret_values(scope, name, ciphertext, updated_by, updated_at)
                    VALUES(?, ?, ?, ?, ?)
                    ON CONFLICT(scope, name) DO UPDATE SET
                        ciphertext = excluded.ciphertext,
                        updated_by = excluded.updated_by,
                        updated_at = excluded.updated_at
                    """,
                    (self.scope(), name, encrypt_secret(str(value).strip()), actor_id, now),
                )

    # ---------- 多店铺 ----------

    @staticmethod
    def shop_id_of(access_token: str) -> str:
        """店铺 id 由 token 推出：同一个 token 重复保存不会出现两条。"""
        digest = hashlib.sha256(str(access_token or "").strip().encode("utf-8")).hexdigest()
        return f"shop_{digest[:12]}"

    def _shop_rows(self) -> dict[str, dict[str, str]]:
        shops = self._read_shop_rows()
        if not shops:
            # 老账号只存过单个 access_token：补一条店铺记录，否则列表是空的、
            # 用户会以为要重新贴一遍（其实 token 还在）。
            self._adopt_current_token_as_shop()
            shops = self._read_shop_rows()
        return shops

    def _adopt_current_token_as_shop(self) -> None:
        # 只有「某个账号」才补店铺记录：全局作用域（脚本/无 owner）不该产生这类行，
        # 否则会在 legacy 归属判定里混进 updated_by="" 的行，把「只迁给本人」判坏。
        if not self.owner_id:
            return
        token = self._load_stored().get("access_token", "")
        if not token:
            return
        try:
            self.save_shop(token, "", "")
        except Exception:  # noqa: BLE001 - 迁移失败不能影响读状态
            return

    def _read_shop_rows(self) -> dict[str, dict[str, str]]:
        with transaction(self.database_path) as conn:
            rows = conn.execute(
                "SELECT name, ciphertext FROM secret_values WHERE scope = ? AND name LIKE ?",
                (self.scope(), f"{SHOP_PREFIX}%"),
            ).fetchall()
        shops: dict[str, dict[str, str]] = {}
        for row in rows:
            try:
                payload = json.loads(decrypt_secret(str(row["ciphertext"])))
            except Exception:  # noqa: BLE001 - 单条损坏不能让整个设置页崩
                continue
            if not isinstance(payload, dict):
                continue
            shop_id = str(row["name"])[len(SHOP_PREFIX) :]
            shops[shop_id] = {
                "id": shop_id,
                "mall_name": str(payload.get("mall_name") or ""),
                "access_token": str(payload.get("access_token") or ""),
                "added_at": str(payload.get("added_at") or ""),
                "last_used_at": str(payload.get("last_used_at") or ""),
            }
        return shops

    def _active_shop_id(self) -> str:
        with transaction(self.database_path) as conn:
            row = conn.execute(
                "SELECT ciphertext FROM secret_values WHERE scope = ? AND name = ?",
                (self.scope(), ACTIVE_SHOP_NAME),
            ).fetchone()
        if not row:
            return ""
        try:
            return str(decrypt_secret(str(row["ciphertext"]))).strip()
        except RuntimeError:
            return ""

    def list_shops(self) -> list[dict[str, Any]]:
        """已保存的店铺（按最近使用排在前面），当前启用那条标 active。"""
        shops = self._shop_rows()
        active = self._active_shop_id()
        rows = list(shops.values())
        rows.sort(key=lambda item: item.get("last_used_at") or item.get("added_at") or "", reverse=True)
        return [
            {
                "id": item["id"],
                "mall_name": item["mall_name"],
                "masked_token": _hint(item["access_token"]),
                "added_at": item["added_at"],
                "last_used_at": item["last_used_at"],
                "active": item["id"] == active,
            }
            for item in rows
        ]

    def save_shop(self, access_token: str, mall_name: str, actor_id: str) -> str:
        """保存（或更新）一个店铺并设为当前。返回店铺 id。"""
        token = str(access_token or "").strip()
        if not token:
            raise ValueError("店铺 access_token 不能为空")
        shop_id = self.shop_id_of(token)
        now = datetime.now().astimezone().isoformat(timespec="seconds")
        existing = self._read_shop_rows().get(shop_id) or {}
        payload = {
            "mall_name": str(mall_name or existing.get("mall_name") or ""),
            "access_token": token,
            "added_at": existing.get("added_at") or now,
            "last_used_at": now,
        }
        with transaction(self.database_path) as conn:
            self._write_secret(conn, f"{SHOP_PREFIX}{shop_id}", json.dumps(payload, ensure_ascii=False), actor_id, now)
            self._write_secret(conn, ACTIVE_SHOP_NAME, shop_id, actor_id, now)
            # access_token 镜像 = 当前店铺，保持既有链路（client()/发布）不变
            self._write_secret(conn, "access_token", token, actor_id, now)
        return shop_id

    def rename_active_shop(self, mall_name: str) -> None:
        """校验拿到店铺名后，把名字补回当前店铺记录（列表里就不用显示「未命名」）。"""
        name = str(mall_name or "").strip()
        shop_id = self._active_shop_id()
        if not name or not shop_id:
            return
        target = self._read_shop_rows().get(shop_id)
        if target is None or target.get("mall_name") == name:
            return
        now = datetime.now().astimezone().isoformat(timespec="seconds")
        payload = {
            "mall_name": name,
            "access_token": target["access_token"],
            "added_at": target.get("added_at") or now,
            "last_used_at": target.get("last_used_at") or now,
        }
        with transaction(self.database_path) as conn:
            self._write_secret(conn, f"{SHOP_PREFIX}{shop_id}", json.dumps(payload, ensure_ascii=False), "", now)

    def activate_shop(self, shop_id: str, actor_id: str) -> str:
        shops = self._shop_rows()
        target = shops.get(str(shop_id))
        if target is None:
            raise ValueError("没有这个店铺，可能已被删除")
        now = datetime.now().astimezone().isoformat(timespec="seconds")
        payload = {
            "mall_name": target["mall_name"],
            "access_token": target["access_token"],
            "added_at": target["added_at"] or now,
            "last_used_at": now,
        }
        with transaction(self.database_path) as conn:
            self._write_secret(conn, f"{SHOP_PREFIX}{shop_id}", json.dumps(payload, ensure_ascii=False), actor_id, now)
            self._write_secret(conn, ACTIVE_SHOP_NAME, str(shop_id), actor_id, now)
            self._write_secret(conn, "access_token", target["access_token"], actor_id, now)
        return str(shop_id)

    def remove_shop(self, shop_id: str, actor_id: str) -> None:
        """删掉一个店铺；删的是当前店铺时，顺手停用（不再自动启用别的店）。"""
        with transaction(self.database_path) as conn:
            conn.execute(
                "DELETE FROM secret_values WHERE scope = ? AND name = ?",
                (self.scope(), f"{SHOP_PREFIX}{shop_id}"),
            )
        if self._active_shop_id() == str(shop_id):
            with transaction(self.database_path) as conn:
                conn.execute(
                    "DELETE FROM secret_values WHERE scope = ? AND name IN (?, ?)",
                    (self.scope(), ACTIVE_SHOP_NAME, "access_token"),
                )

    def _write_secret(self, conn: Any, name: str, value: str, actor_id: str, now: str) -> None:
        conn.execute(
            """
            INSERT INTO secret_values(scope, name, ciphertext, updated_by, updated_at)
            VALUES(?, ?, ?, ?, ?)
            ON CONFLICT(scope, name) DO UPDATE SET
                ciphertext = excluded.ciphertext,
                updated_by = excluded.updated_by,
                updated_at = excluded.updated_at
            """,
            (self.scope(), name, encrypt_secret(value), actor_id, now),
        )

    def status(self) -> dict[str, Any]:
        values = self._load_values()
        stored = set(self._load_stored())
        scope = self.scope()
        configured = {name: bool(values.get(name)) for name in SECRET_NAMES}
        return {
            "configured": {
                "yl_client": configured["yl_client"],
                "yl_token": configured["yl_token"],
                "access_token": configured["access_token"],
                "ready": all(configured.values()),
            },
            "source": {
                name: ("database" if name in stored else ("environment" if values.get(name) else "unset"))
                for name in SECRET_NAMES
            },
            "hints": {name: _hint(values.get(name, "")) for name in SECRET_NAMES},
            "owner": {"account_id": self.owner_id, "scope": scope, "personal": scope != LEGACY_SCOPE},
            # 已保存的店铺（可以多个）：换店点一下就切，不用重新贴 token
            "shops": self.list_shops(),
            "active_shop_id": self._active_shop_id(),
        }


_SERVICE_CACHE: dict[tuple[str, str], "TemuPublishService"] = {}


def _cached_service(
    database_path: Path, owner_id: str, client: TemuProductClient | None = None
) -> "TemuPublishService":
    key = (str(database_path), str(owner_id or ""))
    cached = _SERVICE_CACHE.get(key)
    if cached is None:
        cached = TemuPublishService(database_path, client=client, owner_id=owner_id)
        _SERVICE_CACHE[key] = cached
    return cached


class TemuPublishService:
    def __init__(
        self,
        database_path: Path,
        *,
        client: TemuProductClient | None = None,
        owner_id: str = "",
    ) -> None:
        self.database_path = database_path
        self.owner_id = str(owner_id or "").strip()
        self.store = TemuCredentialStore(database_path, owner_id=self.owner_id)
        self._client_override = client
        self._client: TemuProductClient | None = client
        self._client_key: tuple[str, str, str] | None = None
        # token 校验结果缓存：进页面先用缓存渲染店铺名，点「重新校验」才真去打接口
        self._token_cache: tuple[float, dict[str, Any]] | None = None
        # 店铺级只读数据（父规格/运费/仓库/类目属性）短缓存：这些几乎不变，
        # 每次进页面都重现拉一遍纯属浪费（网关一次往返 1~3 秒）。
        self._read_cache: dict[str, tuple[float, Any]] = {}

    def with_owner(self, owner_id: str) -> "TemuPublishService":
        """同一个服务换账号上下文：每个请求按当前登录账号取自己的店铺凭证。

        实例按 (库, 账号) 缓存：复用里面的 requests.Session（连接池 + TLS 复用），
        也复用 account 级的 token 校验缓存。否则每个接口都要重新建连，页面上就是「连接很慢」。
        """
        return _cached_service(self.database_path, owner_id, self._client_override)

    def client(self) -> TemuProductClient:
        if self._client_override is not None:
            return self._client_override
        credentials = self.store.load()
        if not credentials.complete():
            raise TemuCredentialMissing(
                "Temu 发布凭证未配置完整：需要服务商 client/token 与店铺 access_token"
            )
        key = (credentials.client, credentials.token, credentials.access_token)
        if self._client is None or self._client_key != key:
            self._client = TemuProductClient(credentials)
            self._client_key = key
        return self._client

    # ---------- 设置与验证 ----------

    def settings_status(self) -> dict[str, Any]:
        return self.store.status()

    def clear_settings(self, actor_id: str) -> dict[str, Any]:
        self.store.clear(actor_id)
        self._client = self._client_override
        self._client_key = None
        self._token_cache = None
        return self.settings_status()

    def save_settings(self, payload: Any, actor_id: str) -> dict[str, Any]:
        self.store.save(payload, actor_id)
        # 凭证变更后丢弃旧客户端与缓存，避免继续使用旧 token。
        self._client = self._client_override
        self._client_key = None
        self._token_cache = None
        return self.settings_status()

    def _reset_client(self) -> None:
        """凭证变了：丢掉旧客户端与所有缓存（否则还在用上一家店的 token）。"""
        self._client = self._client_override
        self._client_key = None
        self._token_cache = None
        self._read_cache.clear()

    def list_shops(self) -> dict[str, Any]:
        return self.settings_status()

    def add_shop(self, access_token: str, mall_name: str, actor_id: str) -> dict[str, Any]:
        """保存一家店并启用：**先用这条 token 打一次网关，成功才落库**。

        失败（token 无效/没绑应用/网络）原样抛出去，前端会显示具体原因，
        库里不会留下一条坏店铺 —— 这就是「成功就可以保存」的含义。
        """
        token = str(access_token or "").strip()
        if not token:
            raise ValueError("店铺 access_token 不能为空")
        creds = self.store.load()
        if not (creds.client and creds.token):
            raise TemuCredentialMissing("服务商 client/token 还没配置，无法校验店铺 token")
        probe = TemuProductClient(
            TemuCredentials(client=creds.client, token=creds.token, access_token=token),
            timeout=30,
            max_attempts=1,
        )
        info = probe.token_info()
        resolved_name = str(mall_name or info.get("mallName") or "").strip()
        if not resolved_name and not info:
            raise ValueError("这条 token 没有返回店铺信息，请确认是店铺的 access_token")
        self.store.save_shop(token, resolved_name, actor_id)
        self._reset_client()
        return {**self.settings_status(), "verified": True, "mall_name": resolved_name}

    def activate_shop(self, shop_id: str, actor_id: str) -> dict[str, Any]:
        self.store.activate_shop(shop_id, actor_id)
        self._reset_client()
        return self.settings_status()

    def remove_shop(self, shop_id: str, actor_id: str) -> dict[str, Any]:
        self.store.remove_shop(shop_id, actor_id)
        self._reset_client()
        return self.settings_status()

    TOKEN_CACHE_TTL = 600.0

    def verify_token(self, *, refresh: bool = False) -> dict[str, Any]:
        cached = self._token_cache
        if cached and not refresh:
            import time as _time  # noqa: PLC0415

            if _time.time() - cached[0] < self.TOKEN_CACHE_TTL:
                return cached[1]
        result = self.client().token_info()
        expired = result.get("expiredTime")
        expired_iso = ""
        remaining_days: int | None = None
        if isinstance(expired, (int, float)) and expired > 0:
            moment = datetime.fromtimestamp(float(expired)).astimezone()
            expired_iso = moment.isoformat(timespec="seconds")
            remaining_days = (moment - datetime.now().astimezone()).days
        scope = result.get("apiScopeList")
        info = {
            "mall_name": result.get("mallName") or "",
            "expired_at": expired_iso,
            "remaining_days": remaining_days,
            "scope_count": len(scope) if isinstance(scope, list) else 0,
            "scopes": scope if isinstance(scope, list) else [],
            "expiring_soon": remaining_days is not None and remaining_days <= 14,
        }
        import time as _time  # noqa: PLC0415

        self._token_cache = (_time.time(), info)
        # 校验顺手拿到店铺名：补进列表，用户不用再猜哪条是哪家店
        self.store.rename_active_shop(info["mall_name"])
        return info

    # ---------- 发布配套查询 ----------

    READ_CACHE_TTL = 180.0

    def _read_cached(self, key: str, fetch: Any) -> Any:
        import time as _time  # noqa: PLC0415

        hit = self._read_cache.get(key)
        if hit and _time.time() - hit[0] < self.READ_CACHE_TTL:
            return hit[1]
        value = fetch()
        self._read_cache[key] = (_time.time(), value)
        return value

    def categories(self, parent_cat_id: int = 0) -> dict[str, Any]:
        return self._read_cached(
            f"categories:{parent_cat_id}", lambda: self.client().categories(parent_cat_id)
        )

    def match_category(self, search_text: str, site_id: int = 100) -> dict[str, Any]:
        return self.client().match_category(search_text, site_id)

    def attributes(self, cat_id: int) -> dict[str, Any]:
        return self._read_cached(f"attrs:{cat_id}", lambda: self.client().attributes(cat_id))

    def parent_specs(self) -> dict[str, Any]:
        return self._read_cached("parent_specs", lambda: self.client().parent_specs())

    def create_spec(self, parent_spec_id: int, spec_name: str) -> dict[str, Any]:
        result = self.client().create_spec(parent_spec_id, spec_name)
        # 新建规格后父规格列表会变：service 这一层自己的短缓存也要清
        # （client 那层的缓存由 client.create_spec 负责失效）
        self._read_cache.pop("parent_specs", None)
        return result

    def accessories(self, page: int = 1, page_size: int = 20, fuzzy_value: str = "") -> dict[str, Any]:
        return self.client().accessories(page, page_size, fuzzy_value)

    def freight_templates(self, site_ids: list[int]) -> dict[str, Any]:
        key = "freight:" + ",".join(str(item) for item in site_ids)
        return self._read_cached(key, lambda: self.client().freight_templates(site_ids))

    def warehouses(self, site_ids: list[int], supplier_id: int | None = None) -> dict[str, Any]:
        key = f"warehouses:{','.join(str(item) for item in site_ids)}:{supplier_id or ''}"
        return self._read_cached(key, lambda: self.client().warehouses(site_ids, supplier_id))

    def upload_image(
        self,
        image: str,
        *,
        image_biz_type: int = 0,
        size_mode: int = 0,
        cate_id: int | None = None,
    ) -> dict[str, Any]:
        return self.client().upload_image(
            image, image_biz_type=image_biz_type, size_mode=size_mode, cate_id=cate_id
        )

    def upload_detail_image(
        self,
        image: str,
        *,
        target_width: int = 800,
        cate_id: int | None = None,
    ) -> dict[str, Any]:
        """详情图上传：先统一宽度，再传 Temu，返回图床地址与真实尺寸。

        Temu 商详校验所有楼层等宽，原始图片宽度不一致（1688 常见 1392 与 800 混排）
        会报 `Detail graphic article N floor image width is incorrect`（2000306）。
        """
        raw = _load_image_bytes(image)
        normalized, width, height = _resize_image_to_width(raw, target_width)
        data_url = "data:image/jpeg;base64," + base64.b64encode(normalized).decode("ascii")
        result = dict(self.upload_image(data_url, cate_id=cate_id))
        result["width"] = width
        result["height"] = height
        return result

    def upload_image_from_url(
        self,
        url: str,
        *,
        image_biz_type: int = 0,
        size_mode: int = 0,
        cate_id: int | None = None,
    ) -> dict[str, Any]:
        """服务端下载公网图片后转 base64 上传 Temu，规避浏览器跨域限制。"""
        _assert_public_https_url(url)
        try:
            response = requests.get(url, timeout=30, stream=True)
            response.raise_for_status()
        except requests.RequestException as exc:
            raise ValueError(f"图片下载失败：{exc}") from exc
        content_type = str(response.headers.get("content-type") or "").split(";")[0].strip().lower()
        if content_type and not content_type.startswith("image/"):
            raise ValueError(f"URL 不是图片（content-type={content_type}）")
        limit = 20 * 1024 * 1024
        buffer = bytearray()
        for chunk in response.iter_content(64 * 1024):
            if not chunk:
                continue
            buffer.extend(chunk)
            if len(buffer) > limit:
                raise ValueError("图片超过 20MB，无法上传")
        if not buffer:
            raise ValueError("图片下载为空")
        data_url = f"data:{content_type or 'image/jpeg'};base64,{base64.b64encode(bytes(buffer)).decode('ascii')}"
        return self.upload_image(
            data_url,
            image_biz_type=image_biz_type,
            size_mode=size_mode,
            cate_id=cate_id,
        )

    # ---------- 组装 goods.add 请求体（不提交） ----------

    def build_goods_payload(self, draft: GoodsDraftInput) -> dict[str, Any]:
        warnings: list[str] = []
        # catalog 无论是否校验必填都要取：productSpecPropertyReqs 里的 pid / refPid / templatePid
        # 必须来自类目属性表，拿不到就只能退回父规格 id，Temu 会直接判「请重置规格模版」。
        # 之前只在 validate_catalog=True 时取，导致「跳过校验直接发」必然踩这个坑。
        catalog: dict[str, Any] | None = None
        try:
            catalog = self.client().attributes(int(draft.category_ids[-1]))
        except Exception as exc:  # noqa: BLE001 - 取不到不阻断组装，但要告警
            warnings.append(f"类目属性获取失败（规格声明只能退回父规格 id）：{exc}")
        if draft.validate_catalog and catalog is not None:
            _validate_against_catalog(draft, catalog, warnings)
        if not (catalog or {}).get("properties"):
            warnings.append(
                "类目属性模版为空：规格声明的 pid/refPid/templatePid 只能退回父规格 id，"
                "Temu 可能判「请重置规格模版」。建议在 Temu 后台确认该叶子类目可正常建品。"
            )
        # 属性名称/refPid/templatePid 以类目属性表为准补齐，避免前端漏填导致发布被拒。
        payload = _assemble_payload(draft, warnings, catalog)

        sku_prices = [
            sku.supplier_price for skc in draft.skcs for sku in skc.skus
        ]
        return {
            "payload": payload,
            "warnings": warnings,
            "summary": {
                "category_ids": list(draft.category_ids),
                "leaf_cat_id": int(draft.category_ids[-1]),
                "skc_count": len(draft.skcs),
                "sku_count": sum(len(skc.skus) for skc in draft.skcs),
                "site_ids": payload["productSemiManagedReq"]["bindSiteIds"],
                "warehouse_ids": [
                    item["warehouseId"]
                    for item in payload["productWarehouseRouteReq"]["targetRouteList"]
                ],
                "price_min": min(sku_prices) if sku_prices else 0,
                "price_max": max(sku_prices) if sku_prices else 0,
            },
        }

    # ---------- 发布前校验：类目必填配置 ----------

    def check_mandatory(self, draft: GoodsDraftInput) -> dict[str, Any]:
        """发布前校验这个叶子类目还缺什么。

        优先走文档里的 `bg.glo.goods.catsmandatory.get`；但**网关实测不支持它**
        （2026-10-26 真调，PARTNER / CN 两个网关都返回 `sign is invalid`），
        所以失败时退回本地 `attrs.get` 的必填标记做校验 —— 结论一样能用，只是少了
        网关侧的额外规则。

        只做校验，不会提交商品。
        """
        built = self.build_goods_payload(draft)
        payload = built["payload"]
        leaf = int(draft.category_ids[-1])
        sites = payload["productSemiManagedReq"]["bindSiteIds"]
        try:
            result = self.client().mandatory_config(
                leaf, sites, payload.get("productPropertyReqs") or []
            )
            return {
                "source": "catsmandatory.get",
                "result": result,
                "summary": built["summary"],
                "warnings": built["warnings"],
            }
        except TemuGatewayError as exc:
            catalog = self.client().attributes(leaf)
            missing = _missing_required_properties(draft, catalog)
            return {
                "source": "attrs.get（网关不支持 catsmandatory，已回退）",
                "result": {
                    "unsupported": "bg.glo.goods.catsmandatory.get",
                    "reason": str(exc),
                    "requiredMissing": missing,
                },
                "summary": built["summary"],
                "warnings": built["warnings"],
            }

    def search_goods(
        self, *, ext_code: str = "", title: str = "", page: int = 1, page_size: int = 20
    ) -> dict[str, Any]:
        """按 SKC 货号 / 标题查店铺里的商品（列表接口，比详情接口稳）。"""
        filters: dict[str, Any] = {"page": int(page), "pageSize": int(page_size)}
        if str(ext_code or "").strip():
            filters["skcExtCode"] = str(ext_code).strip()
        if str(title or "").strip():
            filters["productName"] = str(title).strip()[:120]
        cache_key = "goods:" + "|".join(f"{k}={v}" for k, v in sorted(filters.items()))
        result = self._read_cached(cache_key, lambda: self.client().goods_list(**filters))
        items = [_compact_goods_item(item) for item in result.get("data") or [] if isinstance(item, dict)]
        return {"total": int(result.get("totalCount") or 0), "items": items}

    def low_stock_goods(
        self,
        *,
        limit: int = 60,
        page_size: int = 20,
        max_pages: int = 30,
        only_zero: bool = True,
    ) -> dict[str, Any]:
        """扫店铺商品，挑出「有 SKU 库存为 0」的商品（待补库存清单）。

        只走 `bg.glo.goods.list.get`（这个接口有权限），逐页翻到 limit 或翻完为止；
        每页结果复用 180 秒读缓存，重复点开不会重复打网关。

        实测网关**每页最多返回 20 条**（pageSize 传 50/100 也还是 20），一页约 1.5 秒，
        所以默认只扫到「命中 limit 个商品」就停（本店 95% 商品都是 0 库存，约 4 页）。

        ⚠️ 改库存的 `bg.btg.goods.stock.quantity.update` 目前**未开通**，
        所以这里只产出清单（导出去后台改），不写任何数据。
        """
        want = max(1, int(limit))
        # 20 是网关的硬上限，传更大只会让我们误以为翻了 100 条
        size = max(1, min(20, int(page_size)))
        items: list[dict[str, Any]] = []
        scanned = 0
        total = 0
        page = 1
        while page <= max(1, int(max_pages)) and len(items) < want:
            cache_key = f"goods:page={page}|pageSize={size}"
            result = self._read_cached(
                cache_key, lambda p=page: self.client().goods_list(page=p, page_size=size)
            )
            rows = [row for row in result.get("data") or [] if isinstance(row, dict)]
            if not rows:
                break
            total = int(result.get("totalCount") or total or 0)
            for row in rows:
                compact = _compact_goods_item(row)
                zero_count = int(compact["zero_stock_sku_count"])
                if only_zero and zero_count <= 0:
                    continue
                items.append(compact)
                if len(items) >= want:
                    break
            scanned += len(rows)
            if total and scanned >= total:
                break
            page += 1
        zero_skus = sum(int(item["zero_stock_sku_count"]) for item in items)
        return {
            "scanned": scanned,
            "total": total,
            "items": items,
            "zero_sku_count": zero_skus,
            "only_zero": bool(only_zero),
            # 改库存接口没权限，界面要如实说明「这是清单，不是已经改好了」
            "can_update_stock": False,
        }

    def goods_detail(self, product_id: int) -> dict[str, Any]:
        """商品详情。个别商品会返回「无权访问」（991000001），由调用方提示用户。"""
        return self._read_cached(
            f"goods-detail:{int(product_id)}", lambda: self.client().goods_detail(int(product_id))
        )

    def dimension_check(
        self, image_url: str, *, mall_id: str, cat_id: int | None = None, class_id: int | None = None
    ) -> dict[str, Any]:
        """提交尺寸图检测（异步）：返回 uniqueId，再用 dimension_check_result 查结果。"""
        if not str(mall_id or "").strip():
            raise ValueError("尺寸图检测需要 mallId（Temu 店铺 id），请在卖家后台查到后填入")
        return self.client().dimension_image_check(
            image_url, mall_id=mall_id, cat_id=cat_id, class_id=class_id
        )

    def dimension_check_result(self, task_id: str, *, mall_id: str) -> dict[str, Any]:
        if not str(mall_id or "").strip():
            raise ValueError("查询尺寸图检测结果需要 mallId")
        return self.client().dimension_image_check_result(task_id, mall_id=mall_id)

    def find_published_goods(self, draft: GoodsDraftInput, *, within_seconds: int = 1800) -> dict[str, Any] | None:
        """在店铺里按 SKC 货号反查这件商品（发布后核对用）。

        只认「带同样 skc extCode + 最近半小时内创建」的那条，避免同名老商品误判。
        查不到（或接口不可用）返回 None —— 调用方自己决定怎么处理。
        """
        codes = [str(skc.ext_code).strip() for skc in draft.skcs if str(skc.ext_code).strip()][:5]
        sites = sorted({int(sku.site_id) for skc in draft.skcs for sku in skc.skus if int(sku.site_id) > 0})
        started = int(time.time()) - max(60, int(within_seconds))
        for code in codes:
            try:
                result = self.client().goods_list(
                    skcExtCode=code,
                    bindSiteIds=sites or None,
                    createdAtStart=started,
                    page=1,
                    pageSize=10,
                )
            except Exception:  # noqa: BLE001 - 反查失败不能影响主流程
                continue
            for item in result.get("data") or []:
                if not isinstance(item, dict):
                    continue
                if str(item.get("extCode") or "").strip() != code:
                    continue
                created_ms = int(item.get("createdAt") or 0)
                if created_ms and created_ms / 1000 < started - 120:
                    continue
                return {
                    "product_id": str(item.get("productId") or ""),
                    "product_name": str(item.get("productName") or ""),
                    "ext_code": code,
                    "created_at": created_ms,
                    "site_status": item.get("skcSiteStatus"),
                }
        return None

    # ---------- 真正提交发布（bg.glo.goods.add，非幂等） ----------

    def publish_goods(self, draft: GoodsDraftInput, *, force: bool = False) -> dict[str, Any]:
        """组装并提交到 Temu 店铺。

        复用 build_goods_payload 的校验与组装，提交前若校验不通过直接抛 ValueError，
        不会把半成品发到店铺。add_goods 内部强制不重试（非幂等），
        网络异常时错误原样抛给调用方，由人判断是否已创建成功。

        `goods.add` 没有幂等键：所以这里先查本地提交台账，24 小时内同一条草稿已经
        成功过（或正在提交中）就抛 `DuplicateSubmission`，前端弹二次确认后可以
        `force=True` 再提。台账只防手滑/防超时重试，不做对账。
        """
        built = self.build_goods_payload(draft)
        payload = built["payload"]
        fingerprint = submission_ledger.draft_fingerprint(draft)
        submission_ledger.ensure_table(self.database_path)
        if not force:
            prior = submission_ledger.find_recent(self.database_path, self.owner_id, fingerprint)
            if prior is not None:
                when = prior["created_at"]
                state = "提交成功" if prior["status"] == "success" else "正在提交"
                gid = f"，商品 id {prior['gid']}" if prior["gid"] else ""
                raise submission_ledger.DuplicateSubmission(
                    f"重复提交：这条商品在 {when} 已经{state}{gid}。"
                    "接口不支持幂等，再提交会在店铺里多出一件商品。",
                    previous_status=prior["status"],
                    previous_at=when,
                )
        sku_count = int(built["summary"].get("sku_count") or 0) or sum(
            len(skc.get("productSkuReqs") or []) for skc in payload.get("productSkcReqs") or []
        )
        submission_id = submission_ledger.begin(
            self.database_path, self.owner_id, fingerprint, draft.title, sku_count
        )
        try:
            result = self.client().add_goods(payload)
        except Exception as exc:  # noqa: BLE001 - 失败也要回写台账再原样抛出
            # 网络类失败（超时/连接被掐）很可能其实已经建成功了：去店铺里反查一次，
            # 查到就按「已创建」处理，避免用户重试导致重复上架。
            if isinstance(exc, TemuGatewayError) and exc.error_code in {
                "NETWORK_ERROR",
                "UPSTREAM_ERROR",
            }:
                found = self.find_published_goods(draft)
                if found:
                    submission_ledger.finish(
                        self.database_path,
                        submission_id,
                        "success",
                        gid=str(found.get("product_id") or ""),
                    )
                    warnings = list(built["warnings"]) + [
                        "提交时网络中断，但在店铺里已经查到这件商品"
                        f"（id {found.get('product_id') or '未知'}），按已创建处理：先不要再提交，"
                        "去后台确认内容和库存即可。"
                    ]
                    return {
                        "submitted": True,
                        "confirmed_by_lookup": True,
                        "matched_goods": found,
                        "api_type": "bg.glo.goods.add",
                        "result": {},
                        "summary": built["summary"],
                        "warnings": warnings,
                        "request_payload": payload,
                        "submission_id": submission_id,
                    }
            submission_ledger.finish(self.database_path, submission_id, "failed", error=str(exc))
            raise
        submission_ledger.finish(
            self.database_path, submission_id, "success", gid=submission_ledger.goods_id_from_result(result)
        )
        return {
            "submitted": True,
            "api_type": "bg.glo.goods.add",
            "result": result,
            "summary": built["summary"],
            "warnings": built["warnings"],
            "request_payload": payload,
            "submission_id": submission_id,
        }


def _assemble_payload(
    draft: GoodsDraftInput,
    warnings: list[str],
    catalog: dict[str, Any] | None = None,
) -> dict[str, Any]:
    category_ids = [int(value) for value in draft.category_ids]
    if any(value <= 0 for value in category_ids):
        raise ValueError("category_ids 必须是正整数")
    _validate_https_urls("carouselImageUrls", draft.carousel_image_urls)
    _validate_https_urls("outerPackageImageReqs", draft.outer_package_image_urls, required=False)
    _validate_https_urls("goodsLayerDecorationReqs", draft.detail_image_urls, required=False)
    # 带尺寸的详情图是另一条分支（goodsLayerDecorationReqs），之前漏在校验之外
    _validate_https_urls(
        "goodsLayerDecorationReqs.detailImages", [item.url for item in draft.detail_images], required=False
    )
    for skc in draft.skcs:
        _validate_https_urls(f"previewImgUrls({skc.ext_code})", skc.preview_image_urls)
        if skc.color_image_url:
            _validate_https_urls(f"colorImageUrl({skc.ext_code})", [skc.color_image_url])
        for sku in skc.skus:
            if sku.thumb_url:
                _validate_https_urls(f"thumbUrl({sku.ext_code})", [sku.thumb_url])

    # 库存 0 是允许的（Temu 会建品但前台不可售），但要让人知道自己发的是什么状态
    if any(int(sku.stock) == 0 for skc in draft.skcs for sku in skc.skus):
        warnings.append(
            "有 SKU 库存为 0：Temu 允许提交，商品会处于「售罄/不可售」状态，开卖前记得补库存。"
        )

    _ensure_unique([skc.ext_code for skc in draft.skcs], "SKC ext_code")
    _ensure_unique(
        [sku.ext_code for skc in draft.skcs for sku in skc.skus], "SKU ext_code"
    )

    site_ids = sorted({int(value) for value in draft.site_ids if int(value) > 0})
    if not site_ids:
        site_ids = sorted({int(sku.site_id) for skc in draft.skcs for sku in skc.skus})
    if not site_ids:
        raise ValueError("缺少站点 ID（site_ids 或 SKU.site_id）")

    cat_fields = {
        f"cat{index}Id": (category_ids[index - 1] if index <= len(category_ids) else 0)
        for index in range(1, 11)
    }

    product_spec_reqs: list[dict[str, Any]] = []
    seen_specs: set[tuple[int, int]] = set()
    warehouse_sites: dict[str, set[int]] = {}
    all_skus: list[dict[str, Any]] = []
    skc_reqs: list[dict[str, Any]] = []

    sale_pids: set[int] = set()
    for skc in draft.skcs:
        for spec in _all_specs(skc):
            key = (int(spec.parent_spec_id), int(spec.spec_id))
            if key not in seen_specs:
                seen_specs.add(key)
                product_spec_reqs.append(_spec_decl_payload(spec, catalog))
        sale_pids |= _sale_property_pids(catalog, _all_specs(skc))

        sku_reqs: list[dict[str, Any]] = []
        for sku in skc.skus:
            warehouse_sites.setdefault(sku.warehouse_id, set()).add(int(sku.site_id))
            sku_req: dict[str, Any] = {
                "extCode": sku.ext_code,
                "currencyType": sku.currency_type,
                "siteSupplierPrices": [
                    {"siteId": int(sku.site_id), "supplierPrice": int(sku.supplier_price)}
                ],
                "productSkuWhExtAttrReq": {
                    "productSkuWeightReq": {"value": int(sku.weight_mg)},
                    "productSkuVolumeReq": {
                        "len": int(sku.length_mm),
                        "width": int(sku.width_mm),
                        "height": int(sku.height_mm),
                    },
                    "productSkuSensitiveAttrReq": {
                        "isSensitive": 1 if sku.is_sensitive else 0,
                        "sensitiveList": [],
                        "sensitiveTypes": [],
                    },
                    "productSkuSensitiveLimitReq": {},
                },
                "productSkuStockQuantityReq": {
                    "warehouseStockQuantityReqs": [
                        {
                            "warehouseId": sku.warehouse_id,
                            "targetStockAvailable": int(sku.stock),
                        }
                    ]
                },
                "productSkuSpecReqs": [_spec_payload(spec) for spec in sku.specs],
            }
            if sku.thumb_url:
                sku_req["thumbUrl"] = sku.thumb_url
            if sku.suggested_price:
                sku_req["productSkuSuggestedPriceReq"] = {
                    "suggestedPriceCurrencyType": sku.currency_type,
                    "suggestedPrice": int(sku.suggested_price),
                }
            sku_reqs.append(sku_req)

        all_skus.extend(sku_reqs)

    # Temu 半托管：多颜色不是「多个 SKC」，而是 **一个 SKC + 多个 SKU**（颜色写在 SKU 规格里）。
    # 实测（2026-10-27 真机）：
    #   - 多个 SKC 各自填主规格 → 「主销售属性不合法」
    #   - 多个 SKC 全填全零对象 → 「Main Sales Specification ... Duplicated」
    #   - 省略 mainProductSkuSpecReqs → 「Main Sales Specification List Cannot Be Empty」
    #   - 主规格放两个规格 → 「Primary Sales Specification cannot exceed 1」
    #   - 单 SKC + 多个 SKU（颜色+长度都在 SKU 规格）→ 提交成功
    first_skc = draft.skcs[0]
    skc_req: dict[str, Any] = {
        "extCode": first_skc.ext_code,
        "previewImgUrls": list(first_skc.preview_image_urls),
        "mainProductSkuSpecReqs": [dict(_EMPTY_MAIN_SPEC)],
        "productSkuReqs": all_skus,
    }
    if first_skc.color_image_url:
        skc_req["colorImageUrl"] = first_skc.color_image_url
    skc_reqs.append(skc_req)

    # 属性联动：父级取值不满足的子属性不要发（Temu 会判「属性不合法」）
    applicable_pids, dropped_names = _resolve_applicable_properties(catalog, draft.properties)
    kept_properties = [
        item
        for item in draft.properties
        if int(item.pid) not in sale_pids and int(item.pid) in applicable_pids
    ]
    if dropped_names:
        extra = "…" if len(dropped_names) > 6 else ""
        warnings.append(
            "已忽略不适用的属性（上一级取值不满足）：" + "、".join(dropped_names[:6]) + extra
        )

    payload: dict[str, Any] = {
        "productSemiManagedReq": {
            "bindSiteIds": site_ids,
            "semiManagedSiteMode": int(draft.semi_managed_site_mode),
            "semiLanguageStrategy": int(draft.semi_language_strategy),
        },
        "productName": draft.title.strip(),
        # 文档 §7.4 的 goods.add 示例里带了 productI18nReqs（多语言标题）。
        # Temu 美国站要求英文标题：标题是英文才自动带一份 en，
        # 中文标题硬塞 en 反而会被判「英文标题不合规」，这里只给警告。
        **_i18n_title(draft.title.strip(), draft.title_en.strip(), warnings),
        "carouselImageUrls": list(draft.carousel_image_urls),
        # 实测：不带 materialImgUrl 会被拒「Material Image cannot be empty」。
        # 文档没写这个字段；缺省用第一张轮播图（Temu 后台的素材图就是商品主图）。
        "materialImgUrl": (draft.material_image_url or "").strip()
        or (draft.carousel_image_urls[0] if draft.carousel_image_urls else ""),
        "productShipmentReq": {
            "freightTemplateId": draft.freight_template_id.strip(),
            "shipmentLimitSecond": int(draft.shipment_limit_seconds),
        },
        **cat_fields,
        "productSpecPropertyReqs": product_spec_reqs,
        "productPropertyReqs": [
            _property_payload(item, _catalog_property_map(catalog).get(int(item.pid)))
            for item in kept_properties
        ],
        "productSkcReqs": skc_reqs,
        "productWarehouseRouteReq": {
            "targetRouteList": [
                {"warehouseId": warehouse_id, "siteIdList": sorted(sites)}
                for warehouse_id, sites in sorted(warehouse_sites.items())
            ]
        },
        "productWhExtAttrReq": {
            "outerGoodsUrl": draft.outer_goods_url.strip(),
            "productOrigin": {
                "region1ShortName": draft.origin_region_short_name.strip() or "CN",
                # 实测：region2Id 传 0 会被拒「不合法的二级区域id」
                "region2Id": int(draft.origin_region2_id),
            },
        },
    }

    # 这两个字段官方成功样例里有，缺了不影响但不传容易触发额外校验
    payload.setdefault("productSecondHandReq", {"isSecondHand": False})
    # 定制/POD 商品要显式打开，否则前台看不到定制入口
    payload["personalizationSwitch"] = 1 if draft.personalization else 0

    if draft.outer_package_image_urls:
        payload["productOuterPackageImageReqs"] = [
            {"imageUrl": url} for url in draft.outer_package_image_urls
        ]
    # 详情图：实测一个图片模块（key=DecImage）只能放**一张**图，塞多张会被拒
    # 「商详装修前置校验:每个图片模块只能有一张图片」，所以每张单独一层。
    # width/height 必须是图片真实尺寸：乱填会被 Temu 判
    # 「Detail graphic article N floor image width is incorrect」（错误码 2000306）。
    detail_items = list(draft.detail_images)
    if not detail_items:
        detail_items = [
            DetailImageInput(url=url) for url in draft.detail_image_urls
        ] if draft.detail_image_urls else []
    if detail_items:
        payload["goodsLayerDecorationReqs"] = [
            {
                "lang": "en",
                "type": "image",
                "priority": index,
                # 楼层 id 必须唯一，否则前台会把它当成同一层覆盖
                "key": "DecImage",
                "contentList": [
                    {
                        "imgUrl": item.url,
                        "width": int(item.width) if item.width else 800,
                        "height": int(item.height) if item.height else 800,
                    }
                ],
            }
            for index, item in enumerate(detail_items, start=1)
        ]
        missing = [item.url for item in detail_items if not item.width or not item.height]
        if missing:
            warnings.append(
                f"有 {len(missing)} 张详情图没带尺寸，按 800×800 提交，"
                "Temu 可能判「Detail graphic article N floor image width is incorrect」。"
            )
        widths = {int(item.width) for item in detail_items if item.width}
        if len(widths) > 1:
            warnings.append(
                "详情图宽度不一致（" + "、".join(str(v) for v in sorted(widths)) + "）："
                "Temu 商详通常要求所有楼层同宽，建议统一后再提交。"
            )
    if any(sku.is_sensitive for skc in draft.skcs for sku in skc.skus):
        warnings.append(
            "已标记为敏感货：接口的敏感类型（sensitiveTypes）暂未维护，提交后可能被要求补类型。"
        )
    if draft.size_template_ids:
        payload["sizeTemplateIds"] = [int(value) for value in draft.size_template_ids]
    if draft.show_size_template_ids:
        payload["showSizeTemplateIds"] = [int(value) for value in draft.show_size_template_ids]
    if draft.goods_label_name.strip():
        payload["productCustomReq"] = {"goodsLabelName": draft.goods_label_name.strip()}

    return payload


def _validate_against_catalog(
    draft: GoodsDraftInput, catalog: dict[str, Any], warnings: list[str]
) -> None:
    properties = catalog.get("properties")
    if not isinstance(properties, list):
        warnings.append("类目属性返回结构异常，跳过必填校验。")
        return
    by_pid = {int(item.get("pid") or 0): item for item in properties if isinstance(item, dict)}
    provided = {int(item.pid): item for item in draft.properties}
    # 联动属性只有在父级取值满足时才算必填（否则「电池属性=不带电池」还会被要求填三种电池）
    applicable, _ = _resolve_applicable_properties(catalog, draft.properties)

    missing = []
    for pid, item in by_pid.items():
        if pid not in applicable:
            continue
        # 销售属性（isSale）会被组装阶段从商品属性里剔除，改由「销售规格」承载，
        # 所以这里不能再按商品属性校验，否则会误报「缺少必填属性：颜色」。
        if item.get("required") and not item.get("isSale") and pid not in provided:
            missing.append(_property_label(item))
    if missing:
        raise ValueError("缺少必填属性：" + "、".join(missing))

    for pid, item in provided.items():
        catalog_item = by_pid.get(pid)
        if catalog_item is None:
            warnings.append(f"属性 pid={pid} 不在当前类目属性表中，请确认是否误填。")
            continue
        values = catalog_item.get("values") or []
        allowed = {
            int(value.get("vid"))
            for value in values
            if isinstance(value, dict) and value.get("vid") is not None
        }
        if allowed and int(item.vid) not in allowed:
            raise ValueError(
                f"属性「{_property_label(catalog_item)}」的 vid={item.vid} 不在可选值内"
            )


def _property_label(item: dict[str, Any]) -> str:
    return str(
        item.get("propertyName")
        or item.get("name")
        or item.get("propName")
        or f"pid={item.get('pid')}"
    )


def _catalog_property_map(catalog: dict[str, Any] | None) -> dict[int, dict[str, Any]]:
    if not catalog:
        return {}
    properties = catalog.get("properties")
    if not isinstance(properties, list):
        return {}
    return {
        int(item.get("pid") or 0): item
        for item in properties
        if isinstance(item, dict) and item.get("pid")
    }


def _property_payload(item: Any, catalog_item: dict[str, Any] | None = None) -> dict[str, Any]:
    pid = int(item.pid)
    catalog_item = catalog_item or {}
    template_pid = int(item.template_pid or catalog_item.get("templatePid") or pid)
    ref_pid = int(item.ref_pid or catalog_item.get("refPid") or pid)
    prop_name = item.prop_name or str(catalog_item.get("name") or "")
    prop_value = item.prop_value or _catalog_value_text(catalog_item, int(item.vid))
    value_unit = item.value_unit
    if not value_unit:
        units = catalog_item.get("valueUnit")
        if isinstance(units, list) and units:
            value_unit = str(units[0] or "")
    return {
        "pid": pid,
        "refPid": ref_pid,
        "templatePid": template_pid,
        "vid": int(item.vid),
        "propName": prop_name,
        "propValue": prop_value,
        "valueUnit": value_unit,
        "numberInputValue": item.number_input_value,
        "valueGroupId": int(item.value_group_id),
        "valueGroupName": item.value_group_name,
    }


def _catalog_value_text(catalog_item: dict[str, Any], vid: int) -> str:
    for value in catalog_item.get("values") or []:
        if isinstance(value, dict) and int(value.get("vid") or 0) == vid:
            return str(value.get("value") or "")
    return ""


# 主销售规格实测必须传全 0 的空对象：填真实规格反而被判「主销售属性不合法」。
_EMPTY_MAIN_SPEC = {"specId": 0, "specName": "", "parentSpecId": 0, "parentSpecName": ""}


def _spec_payload(spec: Any) -> dict[str, Any]:
    """SKC / SKU 级规格：**只发 4 个字段**。

    实测多带 pid / vid 等字段会被判「主销售属性不合法」或「Sales Attribute is Invalid」。
    """
    return {
        "parentSpecId": int(spec.parent_spec_id),
        "parentSpecName": spec.parent_spec_name,
        "specId": int(spec.spec_id),
        "specName": spec.spec_name,
    }


def _spec_decl_payload(spec: Any, catalog: dict[str, Any] | None = None) -> dict[str, Any]:
    """商品级「销售属性声明」（productSpecPropertyReqs 的一项）。

    实测（2026-10-26 真实上架成功的样例）：
    ``pid`` / ``refPid`` / ``templatePid`` 取**类目属性的**，``parentSpecId`` 取**父规格 id**，
    ``vid`` 必须是 **0**。
      - vid 填属性值 id（颜色透明=514）→「主销售属性不合法」
      - pid 填父规格 id（1001）→「请重置规格模版」
    """
    parent = int(spec.parent_spec_id)
    attr = _catalog_property_by_parent(catalog, parent, spec.parent_spec_name)
    spec_name = spec.spec_name
    prop_name = str((attr or {}).get("name") or spec.parent_spec_name)
    return {
        "pid": int((attr or {}).get("pid") or parent),
        "vid": 0,
        "refPid": int((attr or {}).get("refPid") or parent),
        "templatePid": int((attr or {}).get("templatePid") or parent),
        "specId": int(spec.spec_id),
        "propName": prop_name,
        "specName": spec_name,
        "propValue": spec_name,
        "valueUnit": "",
        "parentSpecId": parent,
        "parentSpecName": spec.parent_spec_name,
        "valueGroupId": 0,
        "valueGroupName": "",
    }


def _catalog_property_by_parent(
    catalog: dict[str, Any] | None, parent_spec_id: int, parent_spec_name: str
) -> dict[str, Any] | None:
    """在类目属性里找「父规格 id 对得上」的那一项（销售属性）。"""
    items = [i for i in ((catalog or {}).get("properties") or []) if isinstance(i, dict)]
    for item in items:
        if int(item.get("parentSpecId") or 0) == int(parent_spec_id):
            return item
    target = str(parent_spec_name or "").strip()
    for item in items:
        if str(item.get("name") or item.get("propertyName") or "").strip() == target:
            return item
    return None


def _all_specs(skc: Any) -> list[Any]:
    """收集一个 SKC 下出现过的全部规格（主规格 + 每个 SKU 的规格）。"""
    specs = list(skc.main_specs)
    for sku in skc.skus:
        specs.extend(sku.specs)
    return specs


def _sale_property_pids(catalog: dict[str, Any] | None, specs: list[Any]) -> set[int]:
    """找出「销售属性」对应的 pid —— 它们不能出现在 productPropertyReqs 里。

    实测：销售属性同时写进商品属性和销售规格，会被判父子关系/规格不合法。
    两类都算：类目属性里 isSale=true 的；以及父规格 id 与本次规格对得上的属性。
    """
    pids: set[int] = set()
    parents = {int(spec.parent_spec_id) for spec in specs}
    for item in (catalog or {}).get("properties") or []:
        if not isinstance(item, dict):
            continue
        pid = int(item.get("pid") or 0)
        if item.get("isSale"):
            pids.add(pid)
        elif int(item.get("parentSpecId") or 0) in parents:
            pids.add(pid)
    return pids


def _catalog_property_by_name(catalog: dict[str, Any] | None, name: str) -> dict[str, Any] | None:
    """按属性名在类目属性表里找一项（规格值要和类目属性对齐才能发）。"""
    target = str(name or "").strip()
    if not catalog or not target:
        return None
    for item in catalog.get("properties") or []:
        if not isinstance(item, dict):
            continue
        label = str(item.get("name") or item.get("propertyName") or "").strip()
        if label == target:
            return item
    return None


def _validate_https_urls(label: str, urls: list[str], *, required: bool = True) -> None:
    if required and not urls:
        raise ValueError(f"{label} 至少需要一张图片")
    for url in urls:
        if not str(url).startswith("https://"):
            raise ValueError(f"{label} 的图片必须是 https 地址：{url}")


def _ensure_unique(values: list[str], label: str) -> None:
    seen: set[str] = set()
    for value in values:
        if value in seen:
            raise ValueError(f"{label} 重复：{value}")
        seen.add(value)


# 解码上限：超过这个像素数的图直接拒绝（防止解压炸弹把内存打满）
MAX_IMAGE_PIXELS = 60_000_000


def _load_image_bytes(source: str) -> bytes:
    """把 data URL / 公网 URL 统一读成字节。"""
    text = str(source or "").strip()
    limit = 20 * 1024 * 1024
    if text.startswith("data:"):
        _, _, payload = text.partition(",")
        # base64 膨胀 4/3：先按编码后长度挡一次，避免把超大字符串解进内存
        if len(payload) > limit * 4 // 3 + 1024:
            raise ValueError("图片超过 20MB，无法上传")
        try:
            data = base64.b64decode(payload)
        except Exception as exc:  # noqa: BLE001
            raise ValueError("图片 data URL 解析失败") from exc
        if len(data) > limit:
            raise ValueError("图片超过 20MB，无法上传")
        return data
    _assert_public_https_url(text)
    try:
        # 不跟重定向：只校验了第一跳地址，302 到 169.254.169.254 就能绕过内网限制
        response = requests.get(text, timeout=30, stream=True, allow_redirects=False)
        if response.is_redirect or response.is_permanent_redirect:
            raise ValueError("图片地址发生了跳转，出于安全考虑不跟随（请直接用最终地址）")
        response.raise_for_status()
    except requests.RequestException as exc:
        raise ValueError(f"图片下载失败：{exc}") from exc
    buffer = bytearray()
    for chunk in response.iter_content(64 * 1024):
        if not chunk:
            continue
        buffer.extend(chunk)
        if len(buffer) > limit:
            raise ValueError("图片超过 20MB，无法上传")
    if not buffer:
        raise ValueError("图片下载为空")
    return bytes(buffer)


def _resize_image_to_width(data: bytes, target_width: int) -> tuple[bytes, int, int]:
    """等比缩放到指定宽度（0 表示不缩放），统一转 JPEG。返回 (字节, 宽, 高)。"""
    from io import BytesIO  # noqa: PLC0415

    from PIL import Image  # noqa: PLC0415 - 只在用到时引入，启动更快

    try:
        image = Image.open(BytesIO(data))
        # 一张 20000×20000 的 PNG 解码后接近 1.6GB：先按像素数挡掉解压炸弹，再解码
        if image.size[0] * image.size[1] > MAX_IMAGE_PIXELS:
            raise ValueError(
                f"图片像素过大（{image.size[0]}×{image.size[1]}），请先压缩后再上传"
            )
        image.load()
    except ValueError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise ValueError(f"图片解码失败：{exc}") from exc
    image = image.convert("RGB")
    width, height = image.size
    if target_width > 0 and width != target_width:
        ratio = target_width / float(width)
        image = image.resize((int(target_width), max(1, round(height * ratio))), Image.LANCZOS)
        width, height = image.size
    buffer = BytesIO()
    image.save(buffer, format="JPEG", quality=92, optimize=True)
    return buffer.getvalue(), int(width), int(height)


def _assert_public_https_url(url: str) -> None:
    """只允许 https 公网地址，阻止访问本机/内网（SSRF 防护）。"""
    parsed = urlparse(str(url or ""))
    host = (parsed.hostname or "").strip().lower()
    if parsed.scheme != "https" or not host:
        raise ValueError("只支持 https 公网图片地址")
    if host == "localhost" or host.endswith(".local"):
        raise ValueError("不允许访问本地地址")
    try:
        addresses = {item[4][0] for item in socket.getaddrinfo(host, None)}
    except OSError as exc:
        raise ValueError(f"域名解析失败：{host}") from exc
    for address in addresses:
        try:
            ip = ipaddress.ip_address(address)
        except ValueError:
            continue
        if not ip.is_global:
            raise ValueError("不允许访问内网地址")


def _hint(value: str) -> str:
    text = str(value or "")
    if not text:
        return ""
    if len(text) <= 8:
        return "*" * len(text)
    return f"{text[:3]}{'*' * 6}{text[-4:]}"


def _i18n_title(title: str, title_en: str, warnings: list[str]) -> dict[str, Any]:
    """生成 productI18nReqs。

    英文标题是独立字段：填了就优先用它（标题仍可写中文供自己看），
    没填则沿用旧规则——标题本身是英文才自动补一份 en。
    """
    text = str(title or "").strip()
    english = str(title_en or "").strip()
    if english:
        if any("\u4e00" <= ch <= "\u9fff" for ch in english):
            warnings.append("英文标题里含中文，Temu 可能判「英文标题不合规」，提交前请改。")
        return {"productI18nReqs": [{"language": "en", "productName": english}]}
    if not text:
        return {}
    if any("\u4e00" <= ch <= "\u9fff" for ch in text):
        warnings.append("标题含中文，未生成英文标题（productI18nReqs）；Temu 美国站通常要英文标题，提交前请改。")
        return {}
    return {"productI18nReqs": [{"language": "en", "productName": text}]}


def _resolve_applicable_properties(
    catalog: dict[str, Any] | None, properties: list[Any]
) -> tuple[set[int], list[str]]:
    """按 Temu 的属性联动规则，算出当前选择下哪些属性是「适用」的。

    Temu 的联动字段（实测 attrs.get 会返回）：
    - 子属性带 `templatePropertyValueParentList = [{parentVidList: [...], vidList: [...]}]`
    - `parentTemplatePid` 指向**父属性的 templatePid**
    规则：父属性没选、或选的 vid 不在任何一组的 parentVidList 里 → 子属性不出现
    （即使子属性 required=true 也不该填，填了会被判「属性不合法」）。

    返回 (适用的 pid 集合, 被剔除的属性名)。
    """
    items = [item for item in (catalog or {}).get("properties") or [] if isinstance(item, dict)]
    if not items:
        return {int(getattr(item, "pid", 0) or 0) for item in properties}, []

    by_template = {
        int(item.get("templatePid") or 0): item for item in items if item.get("templatePid")
    }
    selected = {
        int(getattr(item, "pid", 0) or 0): int(getattr(item, "vid", 0) or 0) for item in properties
    }

    applicable: set[int] = set()
    dropped: list[str] = []
    for item in items:
        pid = int(item.get("pid") or 0)
        rules = item.get("templatePropertyValueParentList") or []
        if not rules:
            applicable.add(pid)
            continue
        parent = by_template.get(int(item.get("parentTemplatePid") or 0))
        if parent is None:
            # 结构拿不到父属性：宁可显示也不误杀
            applicable.add(pid)
            continue
        parent_vid = selected.get(int(parent.get("pid") or 0), 0)
        matched = bool(parent_vid) and any(
            parent_vid in (rule.get("parentVidList") or []) for rule in rules
        )
        if matched:
            applicable.add(pid)
        else:
            dropped.append(
                str(item.get("name") or item.get("propertyName") or f"pid={pid}")
            )
    return applicable, dropped


def _sku_stock(sku: dict[str, Any]) -> int:
    """SKU 的可售库存。列表接口给的字段是 virtualStock（没有就退回 stock）。"""
    for key in ("virtualStock", "stock", "stockAvailable", "targetStockAvailable"):
        value = sku.get(key)
        if value is None:
            continue
        try:
            return max(0, int(value))
        except (TypeError, ValueError):
            continue
    return 0


def _compact_goods_item(item: dict[str, Any]) -> dict[str, Any]:
    """列表项压成前端好展示的样子（含每个 SKU 的库存，补库存清单要用）。"""
    leaf = item.get("leafCat") if isinstance(item.get("leafCat"), dict) else {}
    skus = item.get("productSkuSummaries") if isinstance(item.get("productSkuSummaries"), list) else []
    sku_rows = [
        {
            "sku_id": str(sku.get("productSkuId") or ""),
            "ext_code": str(sku.get("extCode") or ""),
            "stock": _sku_stock(sku),
            "specs": " / ".join(
                str(spec.get("specName") or "")
                for spec in (sku.get("productSkuSpecList") or [])
                if isinstance(spec, dict) and spec.get("specName")
            ),
        }
        for sku in skus
        if isinstance(sku, dict)
    ]
    return {
        "product_id": str(item.get("productId") or ""),
        "title": str(item.get("productName") or ""),
        "skc_ext_code": str(item.get("extCode") or ""),
        "image": str(item.get("mainImageUrl") or ""),
        "created_at": item.get("createdAt"),
        "site_status": item.get("skcSiteStatus"),
        "category": str(leaf.get("catName") or ""),
        "sku_codes": [row["ext_code"] for row in sku_rows][:20],
        "skus": sku_rows[:50],
        "stock_total": sum(row["stock"] for row in sku_rows),
        "zero_stock_sku_count": sum(1 for row in sku_rows if row["stock"] <= 0),
    }


def _missing_required_properties(draft: GoodsDraftInput, catalog: dict[str, Any]) -> list[dict[str, Any]]:
    """本地兜底：用 attrs.get 的 required 标记列出还没填的必填属性。"""
    filled = {int(item.pid) for item in draft.properties if int(item.vid) > 0 or item.prop_value.strip()}
    applicable, _ = _resolve_applicable_properties(catalog, draft.properties)
    missing: list[dict[str, Any]] = []
    for item in (catalog or {}).get("properties") or []:
        if not isinstance(item, dict) or not item.get("required"):
            continue
        pid = int(item.get("pid") or 0)
        # 联动属性在当前父级取值下不适用 → 不必填
        if pid and pid not in applicable:
            continue
        if pid and pid in filled:
            continue
        missing.append(
            {
                "pid": pid,
                "name": str(item.get("name") or item.get("propertyName") or f"pid={pid}"),
            }
        )
    return missing
