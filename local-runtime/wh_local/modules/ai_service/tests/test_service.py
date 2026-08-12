from __future__ import annotations

from pathlib import Path

from wh_local.session import Actor

from wh_local.modules.ai_service.service import AiService, AiServiceError


PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01"
    b"\x00\x00\x00\x01\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0c"
    b"IDAT\x08\xd7c\xf8\xcf\xc0\x00\x00\x03\x01\x01\x00\x18\xdd\x8d\xb1"
    b"\x00\x00\x00\x00IEND\xaeB`\x82"
)


def _actor(user_id: str = "operator-1") -> Actor:
    return Actor(id=user_id, username=user_id, role="operator", workspace_id="workspace-a")


def _service(tmp_path: Path) -> AiService:
    return AiService(tmp_path / "workbench.sqlite3", tmp_path / "assets")


def test_uploaded_asset_is_stored_locally_and_only_owner_can_read_it(tmp_path: Path) -> None:
    service = _service(tmp_path)
    asset = service.save_asset(_actor(), "cup.png", PNG, "image/png")

    assert asset["filename"] == "cup.png"
    assert not Path(asset["path"]).is_absolute()
    assert service.asset_content(_actor(), asset["asset_id"]) == PNG

    try:
        service.asset_content(_actor("operator-2"), asset["asset_id"])
    except AiServiceError as error:
        assert error.status_code == 404
    else:
        raise AssertionError("another user must not read a private local asset")


def test_context_keeps_system_subject_and_latest_eight_messages(tmp_path: Path) -> None:
    service = _service(tmp_path)
    actor = _actor()
    conversation = service.create_conversation(actor, "水杯场景图")
    asset = service.save_asset(actor, "cup.png", PNG, "image/png")

    for index in range(10):
        service.append_message(
            actor,
            conversation["conversation_id"],
            role="user" if index % 2 == 0 else "assistant",
            content=f"message-{index}",
            asset_ids=[asset["asset_id"]] if index == 0 else [],
        )

    context = service.build_chat_context(actor, conversation["conversation_id"], "system prompt")

    assert context[0] == {"role": "system", "content": "system prompt"}
    assert context[1]["role"] == "user"
    assert isinstance(context[1]["content"], list)
    assert context[1]["content"][1]["image_url"]["url"].startswith("data:image/png;base64,")
    assert [item["content"] for item in context[2:]] == [f"message-{index}" for index in range(2, 10)]


def test_bootstrap_only_exposes_whitelisted_models_for_each_mode(tmp_path: Path) -> None:
    service = _service(tmp_path)
    data = service.bootstrap(_actor())

    assert {model["id"] for model in data["models"]} == {
        "deepseek-v4-flash", "deepseek-v4-pro", "gpt-5.6-terra",
        "gpt-image-2-1k", "gpt-image-2-2k", "gpt-image-2-4k",
    }
    assert {template["id"] for template in data["templates"]} == {
        "white-background", "scene", "background", "poster"
    }


def test_edit_creation_payload_uses_template_and_local_reference_image(tmp_path: Path) -> None:
    service = _service(tmp_path)
    actor = _actor()
    asset = service.save_asset(actor, "cup.png", PNG, "image/png")

    payload = service.prepare_creation(
        actor,
        template_id="background",
        model_id="gpt-image-2-1k",
        user_prompt="替换为浅蓝色渐变背景",
        asset_ids=[asset["asset_id"]],
        size="1024x1024",
    )

    assert payload["model"] == "gpt-image-2-1k"
    assert payload["n"] == 1
    assert "严格保留上传商品图" in payload["prompt"]
    assert "浅蓝色渐变背景" in payload["prompt"]
    assert payload["image"].startswith("data:image/png;base64,")
