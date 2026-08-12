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


def test_pod_payloads_are_fixed_to_1k_and_cover_the_four_output_groups(tmp_path: Path) -> None:
    service = _service(tmp_path)
    actor = _actor()
    asset = service.save_asset(actor, "design.png", PNG, "image/png")

    payloads = service.prepare_pod_creations(
        actor,
        user_prompt="复古猫咪插画，面向美国礼品市场",
        asset_ids=[asset["asset_id"]],
    )

    assert [item["kind"] for item in payloads] == ["scene", "feature", "size", "white"]
    assert {item["payload"]["model"] for item in payloads} == {"gpt-image-2-1k"}
    assert [item["payload"]["n"] for item in payloads] == [2, 2, 1, 1]
    assert all("复古猫咪插画" in item["payload"]["prompt"] for item in payloads)
    assert all(item["payload"]["image"].startswith("data:image/png;base64,") for item in payloads)


def test_pod_creation_persists_four_independent_groups_and_retries_only_failed_one(tmp_path: Path) -> None:
    service = _service(tmp_path)
    actor = _actor()
    conversation = service.create_conversation(actor, "POD 杯垫")
    creation = service.create_pod_creation(
        actor,
        conversation["conversation_id"],
        user_prompt="复古杯垫，办公室场景",
        asset_ids=[],
    )

    status = service.pod_creation_status(actor, creation["creation_id"])
    assert [group["kind"] for group in status["groups"]] == ["scene", "feature", "size", "white"]
    assert {group["status"] for group in status["groups"]} == {"queued"}

    service.start_pod_group(actor, creation["creation_id"], "scene")
    service.finish_pod_group(actor, creation["creation_id"], "scene", status="failed", error_message="station timeout")
    retried = service.retry_pod_group(actor, creation["creation_id"], "scene")

    assert retried["kind"] == "scene"
    assert service.pod_creation_status(actor, creation["creation_id"])["groups"][0]["status"] == "queued"


def test_only_one_active_pod_creation_is_allowed_per_conversation_and_restart_interrupts_groups(tmp_path: Path) -> None:
    service = _service(tmp_path)
    actor = _actor()
    conversation = service.create_conversation(actor, "POD 杯垫")
    creation = service.create_pod_creation(actor, conversation["conversation_id"], user_prompt="杯垫", asset_ids=[])

    try:
        service.create_pod_creation(actor, conversation["conversation_id"], user_prompt="重复提交", asset_ids=[])
    except AiServiceError as error:
        assert error.status_code == 409
    else:
        raise AssertionError("an active POD creation must block duplicate submission")

    service.start_pod_group(actor, creation["creation_id"], "scene")
    assert service.mark_interrupted_pod_groups() == 4
    states = {group["status"] for group in service.pod_creation_status(actor, creation["creation_id"])["groups"]}
    assert states == {"interrupted"}
    assert service.latest_pod_creation(actor, conversation["conversation_id"])["creation_id"] == creation["creation_id"]
