from __future__ import annotations

import base64
import json
from collections.abc import Iterator
from pathlib import Path
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


def create_router(database_path: Path, asset_root: Path) -> APIRouter:
    router = APIRouter(prefix="/api/ai-service", tags=["ai-service"])
    service = AiService(database_path, asset_root)

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
        return _call(service.create_conversation, actor, str(body.get("title") or "新建创作"))

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
            conversation_id = service.create_conversation(actor, str(body.get("title") or "新建创作"))["conversation_id"]
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
        context = _call(service.build_chat_context, actor, conversation_id, "你是本地商品创作助手，回答应简洁、可执行。")
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
