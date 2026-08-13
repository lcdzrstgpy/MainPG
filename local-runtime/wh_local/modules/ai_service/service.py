from __future__ import annotations

import base64
import hashlib
import json
import re
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from ...session import Actor
from .defaults import DEFAULT_MODELS, DEFAULT_TEMPLATES
from .materials import extract_local_document


MAX_ASSET_BYTES = 12 * 1024 * 1024
_SAFE_FILENAME = re.compile(r"[^A-Za-z0-9._-]+")
_IMAGE_SIGNATURES = {
    b"\x89PNG\r\n\x1a\n": "image/png",
    b"\xff\xd8\xff": "image/jpeg",
    b"GIF87a": "image/gif",
    b"GIF89a": "image/gif",
}

POD_OUTPUT_SPECS: tuple[dict[str, Any], ...] = (
    {
        "kind": "scene",
        "label": "场景图",
        "count": 2,
        "prompt": "生成两张不同的跨境电商商品场景图。突出商品主体与真实使用情境，画面干净、高级、适合商品详情页；不添加水印、品牌 Logo 或不可读文字。",
    },
    {
        "kind": "feature",
        "label": "功能图",
        "count": 2,
        "prompt": "生成两张不同角度的跨境电商商品功能图。用画面清楚呈现材质、结构、使用方式或关键细节；构图干净，突出卖点，不添加水印、品牌 Logo 或不可读文字。",
    },
    {
        "kind": "size",
        "label": "尺寸图",
        "count": 1,
        "prompt": "生成一张跨境电商商品尺寸展示图。以干净白色或浅灰背景清楚展示产品整体比例；仅使用用户提供的尺寸信息，不得臆造数字；不添加水印、品牌 Logo 或不可读文字。",
    },
    {
        "kind": "white",
        "label": "白底图",
        "count": 1,
        "prompt": "生成一张跨境电商商品白底主图。纯白背景，商品居中完整，比例准确，棚拍光线均匀，保留参考图中的商品主体、颜色、材质与细节；不添加文字、道具或水印。",
    },
)


class AiServiceError(RuntimeError):
    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.status_code = status_code


class AiService:
    """Owns local AI conversations and private image assets.

    Remote provider calls live in the router/gateway boundary. This service never
    receives or exposes a provider API key.
    """

    def __init__(self, database_path: Path, asset_root: Path) -> None:
        self.database_path = Path(database_path)
        self.asset_root = Path(asset_root)
        self.asset_root.mkdir(parents=True, exist_ok=True)
        self._ensure_schema()

    def bootstrap(self, actor: Actor) -> dict[str, Any]:
        return {
            "models": self.model_profiles(),
            "templates": self.templates(),
            "conversations": self.list_conversations(actor),
            "storage": {"mode": "local", "reference_transport": "data_url_with_temporary_cos_fallback"},
        }

    def model_profiles(self) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """SELECT model_id, display_name, modes_json, reference_transport, sizes_json, default_count
                   FROM ai_service_model_profiles WHERE enabled = 1 ORDER BY model_id"""
            ).fetchall()
        if not rows:
            return [dict(item) for item in DEFAULT_MODELS]
        return [{
            "id": row["model_id"], "name": row["display_name"], "modes": _json_string_list(row["modes_json"]),
            "reference_transport": row["reference_transport"], "sizes": _json_string_list(row["sizes_json"]),
            "default_count": row["default_count"],
        } for row in rows]

    def templates(self) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """SELECT template_id, label, description, mode, default_count, prompt, version
                   FROM ai_service_templates WHERE enabled = 1 ORDER BY template_id"""
            ).fetchall()
        if not rows:
            return [dict(item) for item in DEFAULT_TEMPLATES]
        return [{
            "id": row["template_id"], "label": row["label"], "description": row["description"],
            "mode": row["mode"], "default_count": row["default_count"], "prompt": row["prompt"], "version": row["version"],
        } for row in rows]

    def save_model_profiles(self, profiles: list[dict[str, Any]], actor_id: str) -> list[dict[str, Any]]:
        if not profiles:
            raise AiServiceError("at least one model profile is required")
        with self._connect() as conn:
            conn.execute("UPDATE ai_service_model_profiles SET enabled = 0")
            for profile in profiles:
                model_id = str(profile.get("id") or "").strip()
                modes = _allowed_modes(profile.get("modes"))
                sizes = _json_string_list(profile.get("sizes"))
                if not model_id or not modes:
                    raise AiServiceError("model id and at least one mode are required")
                conn.execute(
                    """INSERT INTO ai_service_model_profiles
                       (model_id, display_name, modes_json, reference_transport, sizes_json, default_count, enabled, updated_by, updated_at)
                       VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?)
                       ON CONFLICT(model_id) DO UPDATE SET display_name=excluded.display_name, modes_json=excluded.modes_json,
                       reference_transport=excluded.reference_transport, sizes_json=excluded.sizes_json, default_count=excluded.default_count,
                       enabled=1, updated_by=excluded.updated_by, updated_at=excluded.updated_at""",
                    (model_id, str(profile.get("name") or model_id)[:80], json.dumps(modes),
                     str(profile.get("reference_transport") or "none"), json.dumps(sizes),
                     max(1, min(int(profile.get("default_count") or 1), 4)), actor_id, _now()),
                )
        return self.model_profiles()

    def save_templates(self, templates: list[dict[str, Any]], actor_id: str) -> list[dict[str, Any]]:
        if not templates:
            raise AiServiceError("at least one template is required")
        with self._connect() as conn:
            conn.execute("UPDATE ai_service_templates SET enabled = 0")
            for template in templates:
                template_id = str(template.get("id") or "").strip()
                mode = str(template.get("mode") or "")
                prompt = str(template.get("prompt") or "").strip()
                if not template_id or mode not in {"generate", "edit"} or not prompt:
                    raise AiServiceError("template id, mode, and prompt are required")
                conn.execute(
                    """INSERT INTO ai_service_templates
                       (template_id, label, description, mode, default_count, prompt, version, enabled, updated_by, updated_at)
                       VALUES (?, ?, ?, ?, ?, ?, 1, 1, ?, ?)
                       ON CONFLICT(template_id) DO UPDATE SET label=excluded.label, description=excluded.description,
                       mode=excluded.mode, default_count=excluded.default_count, prompt=excluded.prompt,
                       version=ai_service_templates.version + 1, enabled=1, updated_by=excluded.updated_by, updated_at=excluded.updated_at""",
                    (template_id, str(template.get("label") or template_id)[:80], str(template.get("description") or "")[:180], mode,
                     max(1, min(int(template.get("default_count") or 1), 4)), prompt, actor_id, _now()),
                )
        return self.templates()

    def create_conversation(self, actor: Actor, title: str = "新建创作") -> dict[str, str]:
        conversation_id = uuid.uuid4().hex
        now = _now()
        with self._connect() as conn:
            conn.execute(
                """INSERT INTO ai_service_conversations
                   (conversation_id, workspace_id, owner_user_id, title, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (conversation_id, actor.workspace_id, actor.id, _clean_title(title), now, now),
            )
        return {"conversation_id": conversation_id, "title": _clean_title(title), "created_at": now}

    def list_conversations(self, actor: Actor, limit: int = 50) -> list[dict[str, Any]]:
        self.purge_expired_conversations(actor)
        with self._connect() as conn:
            rows = conn.execute(
                """SELECT conversation_id, title, updated_at
                   FROM ai_service_conversations
                   WHERE workspace_id = ? AND owner_user_id = ?
                   ORDER BY updated_at DESC LIMIT ?""",
                (actor.workspace_id, actor.id, max(1, min(limit, 100))),
            ).fetchall()
        return [dict(row) for row in rows]

    def purge_expired_conversations(self, actor: Actor, now: datetime | None = None) -> int:
        cutoff = (now or datetime.now(timezone.utc)) - timedelta(days=7)
        with self._connect() as conn:
            rows = conn.execute(
                """SELECT conversation_id FROM ai_service_conversations
                   WHERE workspace_id = ? AND owner_user_id = ? AND created_at < ?""",
                (actor.workspace_id, actor.id, cutoff.isoformat(timespec="seconds")),
            ).fetchall()
        for row in rows:
            self.delete_conversation(actor, row["conversation_id"])
        return len(rows)

    def delete_conversation(self, actor: Actor, conversation_id: str) -> None:
        self._conversation(actor, conversation_id)
        with self._connect() as conn:
            message_rows = conn.execute(
                """SELECT asset_ids_json FROM ai_service_messages
                   WHERE conversation_id = ? AND workspace_id = ? AND owner_user_id = ?""",
                (conversation_id, actor.workspace_id, actor.id),
            ).fetchall()
            creation_rows = conn.execute(
                """SELECT creation_id, output_asset_ids_json FROM ai_service_creations
                   WHERE conversation_id = ? AND workspace_id = ? AND owner_user_id = ?""",
                (conversation_id, actor.workspace_id, actor.id),
            ).fetchall()
            pod_rows = conn.execute(
                """SELECT groups.output_asset_ids_json FROM ai_service_pod_groups AS groups
                   JOIN ai_service_creations AS creations ON creations.creation_id = groups.creation_id
                   WHERE creations.conversation_id = ? AND creations.workspace_id = ? AND creations.owner_user_id = ?""",
                (conversation_id, actor.workspace_id, actor.id),
            ).fetchall()
            asset_ids = {
                asset_id
                for row in [*message_rows, *creation_rows, *pod_rows]
                for asset_id in _asset_ids(row["asset_ids_json"] if "asset_ids_json" in row.keys() else row["output_asset_ids_json"])
            }
            assets = conn.execute(
                """SELECT relative_path FROM ai_service_assets
                   WHERE workspace_id = ? AND owner_user_id = ? AND asset_id IN ({})""".format(
                    ",".join("?" for _ in asset_ids) or "''"
                ),
                (actor.workspace_id, actor.id, *asset_ids),
            ).fetchall()
            conn.execute("DELETE FROM ai_service_messages WHERE conversation_id = ?", (conversation_id,))
            conn.execute(
                """DELETE FROM ai_service_pod_groups WHERE creation_id IN (
                       SELECT creation_id FROM ai_service_creations WHERE conversation_id = ? AND workspace_id = ? AND owner_user_id = ?
                   )""",
                (conversation_id, actor.workspace_id, actor.id),
            )
            conn.execute("DELETE FROM ai_service_creations WHERE conversation_id = ?", (conversation_id,))
            conn.execute("DELETE FROM ai_service_conversations WHERE conversation_id = ?", (conversation_id,))
            if asset_ids:
                conn.execute("DELETE FROM ai_service_assets WHERE asset_id IN ({})".format(",".join("?" for _ in asset_ids)), tuple(asset_ids))
        for asset in assets:
            self._asset_path(asset["relative_path"]).unlink(missing_ok=True)

    def list_messages(self, actor: Actor, conversation_id: str) -> list[dict[str, Any]]:
        self._conversation(actor, conversation_id)
        with self._connect() as conn:
            rows = conn.execute(
                """SELECT message_id, role, content, asset_ids_json, created_at
                   FROM ai_service_messages
                   WHERE conversation_id = ? AND workspace_id = ? AND owner_user_id = ?
                   ORDER BY created_at, rowid""",
                (conversation_id, actor.workspace_id, actor.id),
            ).fetchall()
        return [{**dict(row), "asset_ids": _asset_ids(row["asset_ids_json"])} for row in rows]

    def save_asset(self, actor: Actor, filename: str, content: bytes, content_type: str) -> dict[str, str]:
        if _detected_image_type(content):
            media_type = _validated_image_type(content, content_type)
            extracted_text = ""
        else:
            try:
                media_type, extracted_text = extract_local_document(filename, content)
            except ValueError as exc:
                raise AiServiceError(str(exc)) from exc
        asset_id = uuid.uuid4().hex
        safe_name = _safe_filename(filename, media_type)
        relative_path = Path(actor.workspace_id) / actor.id / f"{asset_id}_{safe_name}"
        target = self.asset_root / relative_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
        now = _now()
        try:
            with self._connect() as conn:
                conn.execute(
                    """INSERT INTO ai_service_assets
                       (asset_id, workspace_id, owner_user_id, filename, relative_path, content_type, byte_size, sha256, extracted_text, created_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (asset_id, actor.workspace_id, actor.id, safe_name, relative_path.as_posix(), media_type, len(content), hashlib.sha256(content).hexdigest(), extracted_text, now),
                )
        except Exception:
            target.unlink(missing_ok=True)
            raise
        return {"asset_id": asset_id, "filename": safe_name, "path": relative_path.as_posix(), "content_type": media_type, "kind": "image" if _is_image_type(media_type) else "document"}

    def asset_content(self, actor: Actor, asset_id: str) -> bytes:
        asset = self._asset(actor, asset_id)
        target = self._asset_path(asset["relative_path"])
        try:
            return target.read_bytes()
        except OSError as exc:
            raise AiServiceError("local asset is unavailable", 404) from exc

    def asset_info(self, actor: Actor, asset_id: str) -> dict[str, Any]:
        return dict(self._asset(actor, asset_id))

    def delete_asset(self, actor: Actor, asset_id: str) -> None:
        asset = self._asset(actor, asset_id)
        with self._connect() as conn:
            conn.execute("DELETE FROM ai_service_assets WHERE asset_id = ?", (asset_id,))
        self._asset_path(asset["relative_path"]).unlink(missing_ok=True)

    def append_message(
        self,
        actor: Actor,
        conversation_id: str,
        *,
        role: str,
        content: str,
        asset_ids: list[str] | None = None,
    ) -> dict[str, str]:
        if role not in {"user", "assistant", "system"}:
            raise AiServiceError("message role is invalid")
        self._conversation(actor, conversation_id)
        asset_ids = asset_ids or []
        for asset_id in asset_ids:
            self._asset(actor, asset_id)
        message_id = uuid.uuid4().hex
        now = _now()
        with self._connect() as conn:
            conn.execute(
                """INSERT INTO ai_service_messages
                   (message_id, conversation_id, workspace_id, owner_user_id, role, content, asset_ids_json, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (message_id, conversation_id, actor.workspace_id, actor.id, role, content.strip(), json.dumps(asset_ids), now),
            )
            conn.execute("UPDATE ai_service_conversations SET updated_at = ? WHERE conversation_id = ?", (now, conversation_id))
        return {"message_id": message_id, "created_at": now}

    def build_chat_context(self, actor: Actor, conversation_id: str, system_prompt: str) -> list[dict[str, Any]]:
        self._conversation(actor, conversation_id)
        with self._connect() as conn:
            rows = conn.execute(
                """SELECT role, content, asset_ids_json
                   FROM ai_service_messages
                   WHERE conversation_id = ? AND workspace_id = ? AND owner_user_id = ?
                   ORDER BY created_at, rowid""",
                (conversation_id, actor.workspace_id, actor.id),
            ).fetchall()
        all_messages = [dict(row) for row in rows]
        messages = all_messages[-8:]
        context: list[dict[str, Any]] = [{"role": "system", "content": system_prompt.strip()}]
        subject_asset = next((
            asset_id
            for message in reversed(all_messages)
            if message["role"] == "user"
            for asset_id in reversed(_asset_ids(message["asset_ids_json"]))
            if _is_image_type(self._asset(actor, asset_id)["content_type"])
        ), None)
        if subject_asset:
            context.append({"role": "user", "content": [
                {"type": "text", "text": "当前商品主体参考图"},
                {"type": "image_url", "image_url": {"url": self.asset_data_url(actor, subject_asset)}},
            ]})
        for message in messages:
            text_parts = [message["content"]]
            image_parts: list[dict[str, Any]] = []
            for asset_id in _asset_ids(message["asset_ids_json"]) if message["role"] == "user" else []:
                asset = self._asset(actor, asset_id)
                if _is_image_type(asset["content_type"]):
                    image_parts.append({"type": "image_url", "image_url": {"url": self.asset_data_url(actor, asset_id)}})
                elif asset["extracted_text"]:
                    text_parts.append(f"\n\n本地资料《{asset['filename']}》：\n{asset['extracted_text']}")
            text = "".join(text_parts).strip()
            context.append({"role": message["role"], "content": [{"type": "text", "text": text}, *image_parts] if image_parts else text})
        return context

    def asset_data_url(self, actor: Actor, asset_id: str) -> str:
        asset = self._asset(actor, asset_id)
        encoded = base64.b64encode(self.asset_content(actor, asset_id)).decode("ascii")
        return f"data:{asset['content_type']};base64,{encoded}"

    def prepare_creation(
        self,
        actor: Actor,
        *,
        template_id: str,
        model_id: str,
        user_prompt: str,
        asset_ids: list[str] | None = None,
        size: str = "1024x1024",
    ) -> dict[str, Any]:
        """Build the station image payload from a controlled model/template profile.

        The public API never accepts a reference-image URL. It receives local asset
        ids and this method chooses the transport declared by the model profile.
        """
        template = next((item for item in self.templates() if item["id"] == template_id), None)
        model = next((item for item in self.model_profiles() if item["id"] == model_id), None)
        if template is None or model is None or template["mode"] not in model["modes"]:
            raise AiServiceError("template and model are not compatible")
        if size not in model["sizes"]:
            raise AiServiceError("selected image size is not supported")
        asset_ids = asset_ids or []
        image_asset_id = next((asset_id for asset_id in asset_ids if _is_image_type(self._asset(actor, asset_id)["content_type"])), None)
        if template["mode"] == "edit" and not image_asset_id:
            raise AiServiceError("an uploaded product image is required for this template")
        reference_image = ""
        if image_asset_id:
            if model["reference_transport"] != "data_url":
                raise AiServiceError("temporary reference transport is not configured", 503)
            reference_image = self.asset_data_url(actor, image_asset_id)
        prompt = "\n\n".join(part for part in (str(template["prompt"]), user_prompt.strip()) if part)
        return {
            "model": model_id,
            "prompt": prompt,
            "n": int(template["default_count"]),
            "return_url": True,
            "size": size,
            **({"image": reference_image} if reference_image else {}),
        }

    def prepare_pod_creations(
        self,
        actor: Actor,
        *,
        user_prompt: str,
        asset_ids: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        """Prepare the four fixed POD deliverable groups.

        POD is intentionally not an open model picker: suppliers need a consistent
        1K image package, while the operator may provide an image, text, or both.
        """
        asset_ids = asset_ids or []
        image_asset_id = next((asset_id for asset_id in asset_ids if _is_image_type(self._asset(actor, asset_id)["content_type"])), None)
        reference_image = self.asset_data_url(actor, image_asset_id) if image_asset_id else ""
        shared_prompt = (
            "你正在为外贸 POD 商品制作供应商交付图。严格保持同一商品主体、设计图案、颜色和材质的一致性。\n"
            f"用户输入：{user_prompt.strip()}"
        )
        return [
            {
                "kind": spec["kind"],
                "label": spec["label"],
                "payload": {
                    "model": "gpt-image-2-1k",
                    "prompt": f"{shared_prompt}\n\n交付类型：{spec['prompt']}",
                    "n": spec["count"],
                    "return_url": True,
                    "size": "1024x1024",
                    **({"image": reference_image} if reference_image else {}),
                },
            }
            for spec in POD_OUTPUT_SPECS
        ]

    def create_pod_creation(
        self,
        actor: Actor,
        conversation_id: str,
        *,
        user_prompt: str,
        asset_ids: list[str],
    ) -> dict[str, Any]:
        self._conversation(actor, conversation_id)
        if self.active_pod_creation(actor, conversation_id):
            raise AiServiceError("this conversation already has a POD creation in progress", 409)
        payloads = self.prepare_pod_creations(actor, user_prompt=user_prompt, asset_ids=asset_ids)
        creation_id = uuid.uuid4().hex
        now = _now()
        with self._connect() as conn:
            conn.execute(
                """INSERT INTO ai_service_creations
                   (creation_id, conversation_id, workspace_id, owner_user_id, model_id, request_json, status, created_at, updated_at)
                   VALUES (?, ?, ?, ?, 'gpt-image-2-1k', ?, 'running', ?, ?)""",
                (creation_id, conversation_id, actor.workspace_id, actor.id, _redacted_json({"pod_outputs": payloads}), now, now),
            )
            for item in payloads:
                conn.execute(
                    """INSERT INTO ai_service_pod_groups
                       (group_id, creation_id, workspace_id, owner_user_id, kind, label, payload_json, status, created_at, updated_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, 'queued', ?, ?)""",
                    (
                        uuid.uuid4().hex,
                        creation_id,
                        actor.workspace_id,
                        actor.id,
                        item["kind"],
                        item["label"],
                        json.dumps({
                            **{key: value for key, value in item["payload"].items() if key != "image"},
                            "_asset_ids": asset_ids,
                        }, ensure_ascii=False),
                        now,
                        now,
                    ),
                )
        return {"creation_id": creation_id, "conversation_id": conversation_id, "status": "running"}

    def active_pod_creation(self, actor: Actor, conversation_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                """SELECT creation_id, status FROM ai_service_creations
                   WHERE conversation_id = ? AND workspace_id = ? AND owner_user_id = ?
                     AND request_json LIKE '%pod_outputs%' AND status = 'running'
                   ORDER BY created_at DESC LIMIT 1""",
                (conversation_id, actor.workspace_id, actor.id),
            ).fetchone()
        return dict(row) if row else None

    def latest_pod_creation(self, actor: Actor, conversation_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                """SELECT creation_id FROM ai_service_creations
                   WHERE conversation_id = ? AND workspace_id = ? AND owner_user_id = ?
                     AND request_json LIKE '%pod_outputs%'
                   ORDER BY created_at DESC LIMIT 1""",
                (conversation_id, actor.workspace_id, actor.id),
            ).fetchone()
        return dict(row) if row else None

    def pod_creation_status(self, actor: Actor, creation_id: str) -> dict[str, Any]:
        with self._connect() as conn:
            creation = conn.execute(
                """SELECT creation_id, conversation_id, status, created_at, updated_at
                   FROM ai_service_creations WHERE creation_id = ? AND workspace_id = ? AND owner_user_id = ?""",
                (creation_id, actor.workspace_id, actor.id),
            ).fetchone()
            if creation is None:
                raise AiServiceError("POD creation not found", 404)
            rows = conn.execute(
                """SELECT group_id, kind, label, status, output_asset_ids_json, error_message, started_at, finished_at, created_at, updated_at
                   FROM ai_service_pod_groups WHERE creation_id = ? AND workspace_id = ? AND owner_user_id = ?
                   ORDER BY CASE kind WHEN 'scene' THEN 1 WHEN 'feature' THEN 2 WHEN 'size' THEN 3 ELSE 4 END""",
                (creation_id, actor.workspace_id, actor.id),
            ).fetchall()
        groups = [{**dict(row), "asset_ids": _asset_ids(row["output_asset_ids_json"])} for row in rows]
        return {**dict(creation), "groups": groups}

    def start_pod_group(self, actor: Actor, creation_id: str, kind: str) -> dict[str, Any]:
        now = _now()
        with self._connect() as conn:
            row = conn.execute(
                """UPDATE ai_service_pod_groups SET status = 'running', started_at = ?, updated_at = ?, error_message = ''
                   WHERE creation_id = ? AND workspace_id = ? AND owner_user_id = ? AND kind = ? AND status = 'queued'
                   RETURNING group_id, kind, label, payload_json""",
                (now, now, creation_id, actor.workspace_id, actor.id, kind),
            ).fetchone()
        if row is None:
            raise AiServiceError("POD group is not queued", 409)
        payload = json.loads(row["payload_json"])
        asset_ids = _asset_ids(json.dumps(payload.pop("_asset_ids", [])))
        image_asset_id = next((asset_id for asset_id in asset_ids if _is_image_type(self._asset(actor, asset_id)["content_type"])), None)
        if image_asset_id:
            payload["image"] = self.asset_data_url(actor, image_asset_id)
        return {**dict(row), "payload": payload}

    def finish_pod_group(
        self,
        actor: Actor,
        creation_id: str,
        kind: str,
        *,
        status: str,
        output_asset_ids: list[str] | None = None,
        error_message: str = "",
    ) -> None:
        if status not in {"succeeded", "failed", "interrupted"}:
            raise AiServiceError("POD group status is invalid")
        now = _now()
        with self._connect() as conn:
            result = conn.execute(
                """UPDATE ai_service_pod_groups SET status = ?, output_asset_ids_json = ?, error_message = ?, finished_at = ?, updated_at = ?
                   WHERE creation_id = ? AND workspace_id = ? AND owner_user_id = ? AND kind = ?""",
                (status, json.dumps(output_asset_ids or []), error_message[:300], now, now, creation_id, actor.workspace_id, actor.id, kind),
            )
        if result.rowcount != 1:
            raise AiServiceError("POD group not found", 404)
        self._finish_pod_creation_when_settled(actor, creation_id)

    def retry_pod_group(self, actor: Actor, creation_id: str, kind: str) -> dict[str, Any]:
        now = _now()
        with self._connect() as conn:
            result = conn.execute(
                """UPDATE ai_service_pod_groups SET status = 'queued', output_asset_ids_json = '[]', error_message = '', started_at = '', finished_at = '', updated_at = ?
                   WHERE creation_id = ? AND workspace_id = ? AND owner_user_id = ? AND kind = ? AND status IN ('failed', 'interrupted')""",
                (now, creation_id, actor.workspace_id, actor.id, kind),
            )
            if result.rowcount != 1:
                raise AiServiceError("only failed or interrupted POD groups can be retried", 409)
            conn.execute("UPDATE ai_service_creations SET status = 'running', updated_at = ? WHERE creation_id = ?", (now, creation_id))
        return {"creation_id": creation_id, "kind": kind, "status": "queued"}

    def mark_interrupted_pod_groups(self) -> int:
        now = _now()
        with self._connect() as conn:
            result = conn.execute(
                """UPDATE ai_service_pod_groups SET status = 'interrupted', error_message = '本机服务已重启，可重试此组', finished_at = ?, updated_at = ?
                   WHERE status IN ('queued', 'running')""",
                (now, now),
            )
            conn.execute("UPDATE ai_service_creations SET status = 'failed', updated_at = ? WHERE status = 'running' AND request_json LIKE '%pod_outputs%'", (now,))
        return result.rowcount

    def _finish_pod_creation_when_settled(self, actor: Actor, creation_id: str) -> None:
        with self._connect() as conn:
            rows = conn.execute(
                """SELECT status, output_asset_ids_json FROM ai_service_pod_groups
                   WHERE creation_id = ? AND workspace_id = ? AND owner_user_id = ?""",
                (creation_id, actor.workspace_id, actor.id),
            ).fetchall()
            if not rows or any(row["status"] in {"queued", "running"} for row in rows):
                return
            output_asset_ids = [asset_id for row in rows for asset_id in _asset_ids(row["output_asset_ids_json"])]
            overall = "succeeded" if all(row["status"] == "succeeded" for row in rows) else "failed"
            conn.execute(
                """UPDATE ai_service_creations SET status = ?, output_asset_ids_json = ?, updated_at = ? WHERE creation_id = ?""",
                (overall, json.dumps(output_asset_ids), _now(), creation_id),
            )

    def create_creation(self, actor: Actor, conversation_id: str, payload: dict[str, Any]) -> dict[str, str]:
        self._conversation(actor, conversation_id)
        creation_id = uuid.uuid4().hex
        now = _now()
        with self._connect() as conn:
            conn.execute(
                """INSERT INTO ai_service_creations
                   (creation_id, conversation_id, workspace_id, owner_user_id, model_id, request_json, status, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, 'running', ?, ?)""",
                (creation_id, conversation_id, actor.workspace_id, actor.id, str(payload["model"]), _redacted_json(payload), now, now),
            )
        return {"creation_id": creation_id, "status": "running"}

    def finish_creation(
        self,
        actor: Actor,
        creation_id: str,
        *,
        status: str,
        output_asset_ids: list[str] | None = None,
        error_message: str = "",
    ) -> None:
        if status not in {"succeeded", "failed", "cancelled"}:
            raise AiServiceError("creation status is invalid")
        with self._connect() as conn:
            result = conn.execute(
                """UPDATE ai_service_creations
                   SET status = ?, output_asset_ids_json = ?, error_message = ?, updated_at = ?
                   WHERE creation_id = ? AND workspace_id = ? AND owner_user_id = ?""",
                (status, json.dumps(output_asset_ids or []), error_message[:300], _now(), creation_id, actor.workspace_id, actor.id),
            )
        if result.rowcount != 1:
            raise AiServiceError("creation not found", 404)

    def _conversation(self, actor: Actor, conversation_id: str) -> sqlite3.Row:
        with self._connect() as conn:
            row = conn.execute(
                """SELECT * FROM ai_service_conversations
                   WHERE conversation_id = ? AND workspace_id = ? AND owner_user_id = ?""",
                (conversation_id, actor.workspace_id, actor.id),
            ).fetchone()
        if row is None:
            raise AiServiceError("conversation not found", 404)
        return row

    def _asset(self, actor: Actor, asset_id: str) -> sqlite3.Row:
        with self._connect() as conn:
            row = conn.execute(
                """SELECT * FROM ai_service_assets
                   WHERE asset_id = ? AND workspace_id = ? AND owner_user_id = ?""",
                (asset_id, actor.workspace_id, actor.id),
            ).fetchone()
        if row is None:
            raise AiServiceError("asset not found", 404)
        return row

    def _asset_path(self, relative_path: str) -> Path:
        target = (self.asset_root / relative_path).resolve()
        if self.asset_root.resolve() not in target.parents:
            raise AiServiceError("asset path is invalid", 400)
        return target

    def _connect(self) -> sqlite3.Connection:
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.database_path, timeout=30)
        conn.row_factory = sqlite3.Row
        return conn

    def _ensure_schema(self) -> None:
        with self._connect() as conn:
            conn.executescript(SCHEMA_SQL)
            columns = {row["name"] for row in conn.execute("PRAGMA table_info(ai_service_assets)")}
            if "extracted_text" not in columns:
                conn.execute("ALTER TABLE ai_service_assets ADD COLUMN extracted_text TEXT NOT NULL DEFAULT ''")


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS ai_service_model_profiles (
    model_id TEXT PRIMARY KEY,
    display_name TEXT NOT NULL,
    modes_json TEXT NOT NULL,
    reference_transport TEXT NOT NULL DEFAULT 'none',
    sizes_json TEXT NOT NULL DEFAULT '[]',
    default_count INTEGER NOT NULL DEFAULT 1,
    enabled INTEGER NOT NULL DEFAULT 1,
    updated_by TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS ai_service_templates (
    template_id TEXT PRIMARY KEY,
    label TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    mode TEXT NOT NULL,
    default_count INTEGER NOT NULL DEFAULT 1,
    prompt TEXT NOT NULL,
    version INTEGER NOT NULL DEFAULT 1,
    enabled INTEGER NOT NULL DEFAULT 1,
    updated_by TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS ai_service_conversations (
    conversation_id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    owner_user_id TEXT NOT NULL,
    title TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_ai_service_conversations_owner
    ON ai_service_conversations (workspace_id, owner_user_id, updated_at DESC);
CREATE TABLE IF NOT EXISTS ai_service_assets (
    asset_id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    owner_user_id TEXT NOT NULL,
    filename TEXT NOT NULL,
    relative_path TEXT NOT NULL,
    content_type TEXT NOT NULL,
    byte_size INTEGER NOT NULL,
    sha256 TEXT NOT NULL,
    extracted_text TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_ai_service_assets_owner
    ON ai_service_assets (workspace_id, owner_user_id, created_at DESC);
CREATE TABLE IF NOT EXISTS ai_service_messages (
    message_id TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL,
    workspace_id TEXT NOT NULL,
    owner_user_id TEXT NOT NULL,
    role TEXT NOT NULL,
    content TEXT NOT NULL DEFAULT '',
    asset_ids_json TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_ai_service_messages_conversation
    ON ai_service_messages (conversation_id, workspace_id, owner_user_id, created_at);
CREATE TABLE IF NOT EXISTS ai_service_creations (
    creation_id TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL,
    workspace_id TEXT NOT NULL,
    owner_user_id TEXT NOT NULL,
    model_id TEXT NOT NULL,
    request_json TEXT NOT NULL,
    output_asset_ids_json TEXT NOT NULL DEFAULT '[]',
    status TEXT NOT NULL,
    error_message TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_ai_service_creations_owner
    ON ai_service_creations (workspace_id, owner_user_id, created_at DESC);
CREATE TABLE IF NOT EXISTS ai_service_pod_groups (
    group_id TEXT PRIMARY KEY,
    creation_id TEXT NOT NULL,
    workspace_id TEXT NOT NULL,
    owner_user_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    label TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    output_asset_ids_json TEXT NOT NULL DEFAULT '[]',
    status TEXT NOT NULL,
    error_message TEXT NOT NULL DEFAULT '',
    started_at TEXT NOT NULL DEFAULT '',
    finished_at TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(creation_id, kind)
);
CREATE INDEX IF NOT EXISTS idx_ai_service_pod_groups_owner
    ON ai_service_pod_groups (creation_id, workspace_id, owner_user_id, status);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _clean_title(value: str) -> str:
    return (value or "新建创作").strip()[:80] or "新建创作"


def _safe_filename(value: str, content_type: str) -> str:
    suffix = {
        "image/png": ".png", "image/jpeg": ".jpg", "image/gif": ".gif", "image/webp": ".webp",
        "text/plain": ".txt", "text/csv": ".csv",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": ".xlsx",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
    }[content_type]
    base = _SAFE_FILENAME.sub("-", Path(value or "product-image").stem).strip(".-") or "product-image"
    return f"{base[:80]}{suffix}"


def _validated_image_type(content: bytes, declared_type: str) -> str:
    if not content or len(content) > MAX_ASSET_BYTES:
        raise AiServiceError("image must be between 1 byte and 12 MB")
    detected = _detected_image_type(content)
    if content.startswith(b"RIFF") and content[8:12] == b"WEBP":
        detected = "image/webp"
    if detected is None or (declared_type and declared_type != detected):
        raise AiServiceError("only valid PNG, JPEG, GIF, or WEBP image files are accepted")
    return detected


def _detected_image_type(content: bytes) -> str | None:
    detected = next((mime for signature, mime in _IMAGE_SIGNATURES.items() if content.startswith(signature)), None)
    if content.startswith(b"RIFF") and content[8:12] == b"WEBP":
        detected = "image/webp"
    return detected


def _is_image_type(content_type: str) -> bool:
    return content_type.startswith("image/")


def _asset_ids(value: str) -> list[str]:
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError):
        return []
    return [item for item in parsed if isinstance(item, str)] if isinstance(parsed, list) else []


def _json_string_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [item for item in value if isinstance(item, str)]
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError):
        return []
    return [item for item in parsed if isinstance(item, str)] if isinstance(parsed, list) else []


def _allowed_modes(value: Any) -> list[str]:
    return [item for item in _json_string_list(value) if item in {"chat", "generate", "edit"}]


def _redacted_json(payload: dict[str, Any]) -> str:
    def redact(value: Any) -> Any:
        if isinstance(value, dict):
            return {key: "local-asset-reference" if key == "image" else redact(item) for key, item in value.items()}
        if isinstance(value, list):
            return [redact(item) for item in value]
        return value

    return json.dumps(redact(payload), ensure_ascii=False, sort_keys=True)
