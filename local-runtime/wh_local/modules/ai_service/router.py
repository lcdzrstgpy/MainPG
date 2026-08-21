from __future__ import annotations

import base64
import json
from concurrent.futures import ThreadPoolExecutor
from collections.abc import Iterator
from pathlib import Path
from threading import Lock
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse

from ...session import Actor, actor_from_authorization, require_permission
from ..basic_settings.service import SystemConfigService
from .defaults import DEFAULT_MODELS
from .gateway import StationGateway, StationGatewayError
from .service import AiService, AiServiceError
from .temporary_cos import TemporaryCosStore, TemporaryReference, TemporaryReferenceError
from .web_search import search_context, search_public_web


def create_router(
    database_path: Path,
    asset_root: Path,
    *,
    legacy_pod_enabled: bool = True,
) -> APIRouter:
    router = APIRouter(prefix="/api/ai-service", tags=["ai-service"])
    service = AiService(database_path, asset_root)
    pod_executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="ai-pod")
    pod_references: dict[str, TemporaryReference] = {}
    pod_references_lock = Lock()

    def permitted(actor: Actor, permission: str) -> None:
        require_permission(actor, permission, database_path)

    @router.get("/bootstrap")
    def bootstrap(actor: Actor = Depends(actor_from_authorization)) -> dict[str, Any]:
        permitted(actor, "ai_service.read")
        return service.bootstrap(actor)

    @router.put("/settings/models")
    async def save_models(request: Request, actor: Actor = Depends(actor_from_authorization)) -> dict[str, Any]:
        permitted(actor, "ai_service.settings_manage")
        body = await _body(request)
        profiles = body.get("models")
        if not isinstance(profiles, list):
            raise HTTPException(status_code=400, detail="models is required")
        return {"models": _call(service.save_model_profiles, profiles, actor.id)}

    @router.post("/settings/models/discover")
    def discover_models(actor: Actor = Depends(actor_from_authorization)) -> dict[str, Any]:
        permitted(actor, "ai_service.settings_manage")
        try:
            gateway = _gateway(database_path)
            return {"model_ids": gateway.list_models()}
        except StationGatewayError as exc:
            raise _provider_error(exc)
        finally:
            if "gateway" in locals():
                gateway.close()

    @router.put("/settings/templates")
    async def save_templates(request: Request, actor: Actor = Depends(actor_from_authorization)) -> dict[str, Any]:
        permitted(actor, "ai_service.settings_manage")
        body = await _body(request)
        templates = body.get("templates")
        if not isinstance(templates, list):
            raise HTTPException(status_code=400, detail="templates is required")
        return {"templates": _call(service.save_templates, templates, actor.id)}

    @router.get("/conversations")
    def conversations(actor: Actor = Depends(actor_from_authorization)) -> dict[str, Any]:
        permitted(actor, "ai_service.read")
        return {"conversations": service.list_conversations(actor)}

    @router.post("/conversations")
    async def create_conversation(request: Request, actor: Actor = Depends(actor_from_authorization)) -> dict[str, str]:
        permitted(actor, "ai_service.create")
        body = await _body(request)
        return _call(service.create_conversation, actor, str(body.get("title") or "新建创作"), mode=str(body.get("mode") or "chat"))

    @router.patch("/conversations/{conversation_id}")
    async def update_conversation(conversation_id: str, request: Request, actor: Actor = Depends(actor_from_authorization)) -> dict[str, Any]:
        permitted(actor, "ai_service.create")
        body = await _body(request)
        title = body.get("title")
        is_pinned = body.get("is_pinned")
        if title is not None and not isinstance(title, str):
            raise HTTPException(status_code=400, detail="title must be a string")
        if is_pinned is not None and not isinstance(is_pinned, bool):
            raise HTTPException(status_code=400, detail="is_pinned must be a boolean")
        return _call(service.update_conversation, actor, conversation_id, title=title, is_pinned=is_pinned)

    @router.delete("/conversations/{conversation_id}")
    def delete_conversation(conversation_id: str, actor: Actor = Depends(actor_from_authorization)) -> dict[str, str]:
        permitted(actor, "ai_service.delete")
        _call(service.delete_conversation, actor, conversation_id)
        return {"conversation_id": conversation_id, "status": "deleted"}

    @router.get("/conversations/{conversation_id}/messages")
    def messages(conversation_id: str, actor: Actor = Depends(actor_from_authorization)) -> dict[str, Any]:
        permitted(actor, "ai_service.read")
        return {"messages": _call(service.list_messages, actor, conversation_id)}

    @router.post("/assets")
    async def upload_asset(request: Request, actor: Actor = Depends(actor_from_authorization)) -> dict[str, str]:
        permitted(actor, "ai_service.create")
        content_length = request.headers.get("content-length")
        if content_length and content_length.isdigit() and int(content_length) > 13 * 1024 * 1024:
            raise HTTPException(status_code=413, detail="uploaded material is too large")
        form = await request.form()
        upload = form.get("file")
        if upload is None or not hasattr(upload, "read"):
            raise HTTPException(status_code=400, detail="file is required")
        try:
            return _call(
                service.save_asset,
                actor,
                str(getattr(upload, "filename", "product-image")),
                await upload.read(),
                str(getattr(upload, "content_type", "")),
            )
        finally:
            await upload.close()

    @router.get("/assets/{asset_id}")
    def get_asset(asset_id: str, actor: Actor = Depends(actor_from_authorization)) -> FileResponse:
        permitted(actor, "ai_service.read")
        info = _call(service.asset_info, actor, asset_id)
        path = service._asset_path(info["relative_path"])
        if not path.is_file():
            raise HTTPException(status_code=404, detail="asset is unavailable")
        return FileResponse(path, media_type=info["content_type"], filename=info["filename"])

    @router.delete("/assets/{asset_id}")
    def delete_asset(asset_id: str, actor: Actor = Depends(actor_from_authorization)) -> dict[str, str]:
        permitted(actor, "ai_service.delete")
        _call(service.delete_asset, actor, asset_id)
        return {"asset_id": asset_id, "status": "deleted"}

    @router.post("/creations")
    def create_image(body: dict[str, Any], actor: Actor = Depends(actor_from_authorization)) -> dict[str, Any]:
        permitted(actor, "ai_service.create")
        conversation_id = str(body.get("conversation_id") or "")
        if not conversation_id:
            template_id = str(body.get("template_id") or "scene")
            template = next((item for item in service.templates() if item["id"] == template_id), None)
            conversation_id = service.create_conversation(actor, str(body.get("title") or "新建创作"), mode=str(template["mode"] if template else "generate"))["conversation_id"]
        asset_ids = _string_list(body.get("asset_ids"))
        prompt = str(body.get("prompt") or "").strip()
        model_id = str(body.get("model_id") or "gpt-image-2-1k")
        template_id = str(body.get("template_id") or "scene")
        payload = _call(
            service.prepare_creation,
            actor,
            template_id=template_id,
            model_id=model_id,
            user_prompt=prompt,
            asset_ids=asset_ids,
            size=str(body.get("size") or "1024x1024"),
        )
        _call(service.append_message, actor, conversation_id, role="user", content=prompt, asset_ids=asset_ids)
        creation = _call(service.create_creation, actor, conversation_id, payload)
        gateway: StationGateway | None = None
        temporary: TemporaryReference | None = None
        try:
            gateway = _gateway(database_path)
            try:
                results = gateway.generate_image(payload)
            except StationGatewayError as error:
                # If the selected station route rejects a data URL, retry once with a private temporary URL.
                if error.status_code not in {400, 415, 422} or not asset_ids or not payload.get("image"):
                    raise
                runtime = SystemConfigService(database_path).get_runtime_config()
                temporary = TemporaryCosStore(runtime.cos).publish(
                    service.asset_content(actor, asset_ids[0]),
                    service.asset_info(actor, asset_ids[0])["content_type"],
                )
                payload["image"] = temporary.url
                results = gateway.generate_image(payload)
            output_asset_ids = [_save_result(service, actor, gateway, item)["asset_id"] for item in results]
            _call(service.append_message, actor, conversation_id, role="assistant", content=f"已生成 {len(output_asset_ids)} 张商品创作图。", asset_ids=output_asset_ids)
            _call(service.finish_creation, actor, creation["creation_id"], status="succeeded", output_asset_ids=output_asset_ids)
            return {**creation, "status": "succeeded", "asset_ids": output_asset_ids, "conversation_id": conversation_id}
        except (StationGatewayError, TemporaryReferenceError, AiServiceError) as exc:
            _call(service.finish_creation, actor, creation["creation_id"], status="failed", error_message=str(exc))
            raise _provider_error(exc)
        finally:
            if temporary is not None:
                TemporaryCosStore(SystemConfigService(database_path).get_runtime_config().cos).delete(temporary)
            if gateway is not None:
                gateway.close()

    if legacy_pod_enabled:

        @router.post("/pod-creations")
        def create_pod_images(body: dict[str, Any], actor: Actor = Depends(actor_from_authorization)) -> dict[str, Any]:
            """Queue the four fixed POD groups and return without waiting for images."""
            permitted(actor, "ai_service.create")
            conversation_id = str(body.get("conversation_id") or "")
            prompt = str(body.get("prompt") or "").strip()
            asset_ids = _string_list(body.get("asset_ids"))
            if not prompt:
                raise HTTPException(status_code=400, detail="POD product brief is required")
            if not conversation_id:
                conversation_id = service.create_conversation(actor, str(body.get("title") or "POD 出图"), mode="pod")["conversation_id"]
            _call(service.append_message, actor, conversation_id, role="user", content=prompt, asset_ids=asset_ids)
            creation = _call(service.create_pod_creation, actor, conversation_id, user_prompt=prompt, asset_ids=asset_ids)
            for kind in ("scene", "feature", "size", "white"):
                pod_executor.submit(_run_pod_group, service, database_path, actor, creation["creation_id"], kind, pod_references, pod_references_lock)
            return {**creation, "groups": _call(service.pod_creation_status, actor, creation["creation_id"])["groups"]}

        @router.get("/pod-creations/{creation_id}")
        def pod_creation_status(creation_id: str, actor: Actor = Depends(actor_from_authorization)) -> dict[str, Any]:
            permitted(actor, "ai_service.read")
            return _call(service.pod_creation_status, actor, creation_id)

        @router.get("/conversations/{conversation_id}/pod-creation")
        def latest_pod_creation(conversation_id: str, actor: Actor = Depends(actor_from_authorization)) -> dict[str, Any]:
            permitted(actor, "ai_service.read")
            latest = _call(service.latest_pod_creation, actor, conversation_id)
            return _call(service.pod_creation_status, actor, latest["creation_id"]) if latest else {"creation_id": "", "groups": []}

        @router.post("/pod-creations/{creation_id}/groups/{kind}/retry")
        def retry_pod_group(creation_id: str, kind: str, actor: Actor = Depends(actor_from_authorization)) -> dict[str, Any]:
            permitted(actor, "ai_service.create")
            group = _call(service.retry_pod_group, actor, creation_id, kind)
            pod_executor.submit(_run_pod_group, service, database_path, actor, creation_id, kind, pod_references, pod_references_lock)
            return group

    @router.post("/messages/stream")
    async def stream_message(request: Request, actor: Actor = Depends(actor_from_authorization)) -> StreamingResponse:
        permitted(actor, "ai_service.create")
        body = await _body(request)
        conversation_id = str(body.get("conversation_id") or "")
        content = str(body.get("content") or "").strip()
        model_id = str(body.get("model_id") or "deepseek-v4-flash")
        if not conversation_id or not content:
            raise HTTPException(status_code=400, detail="conversation_id and content are required")
        if model_id not in {item["id"] for item in service.model_profiles() if "chat" in item["modes"]}:
            raise HTTPException(status_code=400, detail="selected model does not support chat")
        asset_ids = _string_list(body.get("asset_ids"))
        _call(service.append_message, actor, conversation_id, role="user", content=content, asset_ids=asset_ids)
        context = _call(service.build_chat_context, actor, conversation_id, """你是本地商品创作助手，使用简体中文回答商品创作、运营与本地化相关问题。

回答规则：
1. 不要寒暄、自我介绍、重复用户问题，也不要先罗列你能提供哪些帮助；直接回应当前需求。
2. 先给出结论或可执行答案，再补充必要的理由、步骤或示例。
3. 使用清晰的 Markdown 排版：短标题、编号步骤和项目符号；每个要点只表达一件事，段落保持简短。
4. 涉及方案、文案或运营建议时，优先给可直接复制或执行的内容；信息不足时，只追问一个最关键的问题。
5. 不使用空泛套话，不堆叠长段落；除非用户要求，不写冗长前言或总结。
6. 用户只发问候时，简洁引导其直接提供商品、目标或要解决的问题。""")
        if bool(body.get("web_search")):
            try:
                results = search_public_web(content)
            except RuntimeError as exc:
                raise HTTPException(status_code=502, detail=str(exc)) from exc
            source_context = search_context(results)
            if source_context:
                context.append({"role": "user", "content": source_context})
        try:
            gateway = _gateway(database_path)
        except StationGatewayError as exc:
            raise _provider_error(exc)
        return StreamingResponse(
            _stream_station_reply(service, actor, conversation_id, gateway, context, model_id),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    return router


def _gateway(database_path: Path) -> StationGateway:
    runtime = SystemConfigService(database_path).get_runtime_config()
    return StationGateway(_station_api_key(runtime))


def _station_api_key(runtime: Any) -> str:
    """Resolve the single station credential stored by the existing settings page.

    The settings page keeps text/image fields for compatibility with product
    processing, but station-88 uses one account key and dispatches by `model`.
    Prefer the text field as the canonical location and accept the image field
    only for installations that were configured before the text field existed.
    """
    return str(runtime.text_ai.api_key or runtime.image_ai.api_key or "")


def _stream_station_reply(
    service: AiService,
    actor: Actor,
    conversation_id: str,
    gateway: StationGateway,
    context: list[dict[str, Any]],
    model_id: str,
) -> Iterator[str]:
    chunks: list[str] = []
    try:
        for line in gateway.chat_stream(context, model_id):
            if line.startswith("data:"):
                _append_delta(chunks, line[5:].strip())
            yield f"{line}\n\n"
        _call(service.append_message, actor, conversation_id, role="assistant", content="".join(chunks))
    except StationGatewayError as exc:
        yield f"data: {json.dumps({'error': str(exc)}, ensure_ascii=False)}\n\n"
    finally:
        gateway.close()


def _run_pod_group(
    service: AiService,
    database_path: Path,
    actor: Actor,
    creation_id: str,
    kind: str,
    pod_references: dict[str, TemporaryReference],
    pod_references_lock: Lock,
) -> None:
    """Run one POD group independently so all four can call the station together."""
    gateway: StationGateway | None = None
    try:
        group = service.start_pod_group(actor, creation_id, kind)
        payload = group["payload"]
        gateway = _gateway(database_path)
        try:
            results = gateway.generate_image(payload)
        except StationGatewayError as error:
            asset_ids = _payload_asset_ids(service, actor, creation_id, kind)
            if error.status_code not in {400, 415, 422} or not asset_ids or not payload.get("image"):
                raise
            payload["image"] = _pod_temporary_reference_url(
                service, database_path, actor, creation_id, asset_ids[0], pod_references, pod_references_lock,
            )
            results = gateway.generate_image(payload)
        output_asset_ids = [_save_result(service, actor, gateway, result)["asset_id"] for result in results]
        service.finish_pod_group(actor, creation_id, kind, status="succeeded", output_asset_ids=output_asset_ids)
    except (StationGatewayError, TemporaryReferenceError, AiServiceError) as exc:
        try:
            service.finish_pod_group(actor, creation_id, kind, status="failed", error_message=str(exc))
        except AiServiceError:
            pass
    finally:
        if gateway is not None:
            gateway.close()
        _cleanup_pod_reference_when_settled(service, database_path, actor, creation_id, pod_references, pod_references_lock)


def _pod_temporary_reference_url(
    service: AiService,
    database_path: Path,
    actor: Actor,
    creation_id: str,
    asset_id: str,
    pod_references: dict[str, TemporaryReference],
    lock: Lock,
) -> str:
    with lock:
        existing = pod_references.get(creation_id)
        if existing is not None:
            return existing.url
        runtime = SystemConfigService(database_path).get_runtime_config()
        temporary = TemporaryCosStore(runtime.cos).publish(
            service.asset_content(actor, asset_id),
            service.asset_info(actor, asset_id)["content_type"],
        )
        pod_references[creation_id] = temporary
        return temporary.url


def _cleanup_pod_reference_when_settled(
    service: AiService,
    database_path: Path,
    actor: Actor,
    creation_id: str,
    pod_references: dict[str, TemporaryReference],
    lock: Lock,
) -> None:
    status = service.pod_creation_status(actor, creation_id)
    if any(group["status"] in {"queued", "running"} for group in status["groups"]):
        return
    with lock:
        temporary = pod_references.pop(creation_id, None)
    if temporary is not None:
        TemporaryCosStore(SystemConfigService(database_path).get_runtime_config().cos).delete(temporary)


def _payload_asset_ids(service: AiService, actor: Actor, creation_id: str, kind: str) -> list[str]:
    status = service.pod_creation_status(actor, creation_id)
    group = next((item for item in status["groups"] if item["kind"] == kind), None)
    if group is None:
        return []
    with service._connect() as conn:
        row = conn.execute("SELECT payload_json FROM ai_service_pod_groups WHERE group_id = ?", (group["group_id"],)).fetchone()
    try:
        value = json.loads(row["payload_json"]).get("_asset_ids", []) if row else []
    except (TypeError, ValueError):
        return []
    return [item for item in value if isinstance(item, str)]


def _append_delta(chunks: list[str], raw: str) -> None:
    if raw == "[DONE]":
        return
    try:
        data = json.loads(raw)
        value = data.get("choices", [{}])[0].get("delta", {}).get("content", "")
    except (ValueError, AttributeError, IndexError, TypeError):
        return
    if isinstance(value, str):
        chunks.append(value)


def _save_result(service: AiService, actor: Actor, gateway: StationGateway, item: dict[str, str]) -> dict[str, str]:
    if item.get("b64_json"):
        try:
            content = base64.b64decode(item["b64_json"], validate=True)
        except ValueError as exc:
            raise AiServiceError("station returned invalid base64 image") from exc
        content_type = _image_type(content)
    else:
        url = str(item.get("url") or "")
        content, content_type = gateway.download_image(url)
        content_type = content_type or _image_type(content)
    return service.save_asset(actor, "generated-image", content, content_type)


def _image_type(content: bytes) -> str:
    if content.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if content.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if content.startswith(b"RIFF") and content[8:12] == b"WEBP":
        return "image/webp"
    if content.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    raise AiServiceError("station result is not a supported image")


async def _body(request: Request) -> dict[str, Any]:
    try:
        body = await request.json()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="JSON body is required") from exc
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="JSON object is required")
    return body


def _string_list(value: Any) -> list[str]:
    return [item for item in value if isinstance(item, str)] if isinstance(value, list) else []


def _provider_error(exc: Exception) -> HTTPException:
    status = exc.status_code if isinstance(exc, StationGatewayError) and exc.status_code else 502
    if isinstance(exc, (AiServiceError, TemporaryReferenceError)):
        status = getattr(exc, "status_code", 503)
    return HTTPException(status_code=status, detail=str(exc))


def _call(func, *args, **kwargs):
    try:
        return func(*args, **kwargs)
    except AiServiceError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
