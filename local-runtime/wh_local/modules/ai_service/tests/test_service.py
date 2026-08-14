from __future__ import annotations

from datetime import datetime, timedelta, timezone
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
    assert "商品改图执行规范" in payload["prompt"]
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
    assert all("POD 交付图总规则" in item["payload"]["prompt"] for item in payloads)
    assert all(item["payload"]["image"].startswith("data:image/png;base64,") for item in payloads)


def test_generate_creation_payload_uses_the_structured_generate_prompt(tmp_path: Path) -> None:
    service = _service(tmp_path)

    payload = service.prepare_creation(
        _actor(),
        template_id="poster",
        model_id="gpt-image-2-1k",
        user_prompt="为保温杯制作秋季礼品海报",
        size="1024x1024",
    )

    assert "文生图执行规范" in payload["prompt"]
    assert "保温杯制作秋季礼品海报" in payload["prompt"]


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


def test_expired_conversations_remove_all_owned_assets_without_touching_other_users(tmp_path: Path) -> None:
    service = _service(tmp_path)
    actor = _actor()
    other_actor = _actor("operator-2")
    expired = service.create_conversation(actor, "过期会话")
    recent = service.create_conversation(actor, "保留会话")
    other_expired = service.create_conversation(other_actor, "其他用户的过期会话")
    message_asset = service.save_asset(actor, "message.png", PNG, "image/png")
    generated_asset = service.save_asset(actor, "generated.png", PNG, "image/png")
    pod_asset = service.save_asset(actor, "pod.png", PNG, "image/png")
    other_asset = service.save_asset(other_actor, "other.png", PNG, "image/png")
    service.append_message(actor, expired["conversation_id"], role="user", content="过期附件", asset_ids=[message_asset["asset_id"]])
    creation = service.create_creation(actor, expired["conversation_id"], {"model": "gpt-image-2-1k"})
    service.finish_creation(actor, creation["creation_id"], status="succeeded", output_asset_ids=[generated_asset["asset_id"]])
    pod_creation = service.create_pod_creation(actor, expired["conversation_id"], user_prompt="过期 POD", asset_ids=[])
    service.finish_pod_group(actor, pod_creation["creation_id"], "scene", status="succeeded", output_asset_ids=[pod_asset["asset_id"]])
    service.append_message(other_actor, other_expired["conversation_id"], role="user", content="其他用户附件", asset_ids=[other_asset["asset_id"]])

    eight_days_ago = (datetime.now(timezone.utc) - timedelta(days=8)).isoformat(timespec="seconds")
    with service._connect() as conn:
        conn.execute("UPDATE ai_service_conversations SET created_at = ?, updated_at = ? WHERE conversation_id IN (?, ?)", (eight_days_ago, eight_days_ago, expired["conversation_id"], other_expired["conversation_id"]))

    assert [item["conversation_id"] for item in service.list_conversations(actor)] == [recent["conversation_id"]]
    assert all(not (service.asset_root / asset["path"]).exists() for asset in (message_asset, generated_asset, pod_asset))
    assert service.list_messages(other_actor, other_expired["conversation_id"])[0]["content"] == "其他用户附件"
    assert (service.asset_root / other_asset["path"]).exists()


def test_expiration_keeps_a_conversation_created_exactly_seven_days_ago(tmp_path: Path) -> None:
    service = _service(tmp_path)
    actor = _actor()
    conversation = service.create_conversation(actor, "七天边界")
    now = datetime(2030, 1, 8, tzinfo=timezone.utc)
    cutoff = (now - timedelta(days=7)).isoformat(timespec="seconds")
    with service._connect() as conn:
        conn.execute("UPDATE ai_service_conversations SET created_at = ?, updated_at = ? WHERE conversation_id = ?", (cutoff, cutoff, conversation["conversation_id"]))

    assert service.purge_expired_conversations(actor, now=now) == 0
    assert service.list_messages(actor, conversation["conversation_id"]) == []


def test_conversation_can_be_renamed_and_pinned_without_affecting_another_user(tmp_path: Path) -> None:
    service = _service(tmp_path)
    actor = _actor()
    other_actor = _actor("operator-2")
    first = service.create_conversation(actor, "第一个会话")
    second = service.create_conversation(actor, "第二个会话")
    other = service.create_conversation(other_actor, "其他用户会话")

    renamed = service.update_conversation(actor, first["conversation_id"], title="  自定义名称  ", is_pinned=True)

    assert renamed["title"] == "自定义名称"
    assert renamed["is_pinned"] is True
    assert [item["conversation_id"] for item in service.list_conversations(actor)] == [first["conversation_id"], second["conversation_id"]]
    assert service.update_conversation(actor, first["conversation_id"], is_pinned=False)["is_pinned"] is False
    assert service.update_conversation(other_actor, other["conversation_id"], title="其他名称")["title"] == "其他名称"


def test_conversations_keep_their_creation_mode_for_mode_switching(tmp_path: Path) -> None:
    service = _service(tmp_path)
    actor = _actor()

    chat = service.create_conversation(actor, "聊天会话", mode="chat")
    image = service.create_conversation(actor, "白底图", mode="generate")

    conversations = {item["conversation_id"]: item for item in service.list_conversations(actor)}

    assert conversations[chat["conversation_id"]]["mode"] == "chat"
    assert conversations[image["conversation_id"]]["mode"] == "generate"
