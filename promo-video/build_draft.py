# -*- coding: utf-8 -*-
"""界野 · 抖音数字人口播 60s —— 剪映专业版草稿生成器

用法：
    python build_draft.py                # 生成草稿（draft_content.json）
    python build_draft.py --alt          # 若剪映提示「草稿内容已损坏」，用这个再跑一次
                                         # 多写一份 draft_info.json 并把注册表指向它
    python build_draft.py --print-only   # 只打印分镜时间轴，不写盘

产出：在剪映草稿根目录下新建 <draft_name>/，包含可被剪映 11.5 打开的明文工程。
"""

import json
import os
import shutil
import sys
import time
import uuid

HERE = os.path.dirname(os.path.abspath(__file__))
PLAN_PATH = os.path.join(HERE, "plan.json")

DRAFT_ROOT = r"C:\Users\HUAWEI\AppData\Local\JianyingPro\User Data\Projects\com.lveditor.draft"
ROOT_META = os.path.join(DRAFT_ROOT, "root_meta_info.json")
COVER_SRC = os.path.join(DRAFT_ROOT, "8月9日 (3)", "draft_cover.jpg")

US = 1_000_000
APP_VERSION = "4.3.1"          # 明文草稿可用版本号；剪映 11.x 会自动升级该草稿
NEW_VERSION = "77.0.0"
APP_SOURCE = "lv"
DEVICE_ID = "dfc293bb9e0be5c3392426895aefdfdf"
MAC_ADDRESS = "ee7af6b6b0ed289224e11f6d93cc02d6"
OS_VERSION = "10.0.22621"

MAT_KEYS = [
    "audio_balances", "audio_effects", "audio_fades", "audios", "beats", "canvases",
    "chromas", "color_curves", "drafts", "effects", "green_screens", "handwrites",
    "hsl", "images", "log_color_wheels", "manual_deformations", "masks",
    "material_animations", "material_colors", "placeholders", "plugin_effects",
    "primary_color_wheels", "realtime_denoises", "sound_channel_mappings", "speeds",
    "stickers", "tail_leaders", "text_templates", "texts", "transitions",
    "video_effects", "video_trackings", "videos",
]


def uid():
    return str(uuid.uuid4()).upper()


def us(seconds):
    return int(round(seconds * US))


def load_plan():
    with open(PLAN_PATH, encoding="utf-8") as f:
        return json.load(f)


def text_material(content, font_size, color, font_path):
    """剪映文本素材（字幕 / 字卡 / 口播稿）。content 为内联富文本字符串。"""
    inner = '<size=%s><font id="" path="%s">[%s]</font></size>' % (
        round(float(font_size), 3), font_path, content)
    return {
        "add_type": 0,
        "alignment": 1,
        "background_alpha": 1.0,
        "background_color": "",
        "background_height": 0.14,
        "background_horizontal_offset": 0.0,
        "background_round_radius": 0.0,
        "background_style": 0,
        "background_vertical_offset": 0.0,
        "background_width": 0.14,
        "bold_width": 0.0,
        "border_color": "",
        "border_width": 0.08,
        "check_flag": 7,
        "content": inner,
        "fixed_height": -1.0,
        "fixed_width": -1.0,
        "font_category_id": "",
        "font_category_name": "",
        "font_id": "",
        "font_name": "",
        "font_path": font_path,
        "font_resource_id": "",
        "font_size": float(font_size),
        "font_source_platform": 0,
        "font_team_id": "",
        "font_title": "none",
        "font_url": "",
        "fonts": [],
        "force_apply_line_max_width": False,
        "global_alpha": 1.0,
        "group_id": "",
        "has_shadow": False,
        "id": uid(),
        "initial_scale": 1.0,
        "is_rich_text": False,
        "italic_degree": 0,
        "ktv_color": "",
        "language": "",
        "layer_weight": 1,
        "letter_spacing": 0.0,
        "line_spacing": 0.02,
        "name": "",
        "preset_category": "",
        "preset_category_id": "",
        "preset_has_set_alignment": False,
        "preset_id": "",
        "preset_index": 0,
        "preset_name": "",
        "recognize_type": 0,
        "relevance_segment": [],
        "shadow_alpha": 0.8,
        "shadow_angle": -45.0,
        "shadow_color": "",
        "shadow_distance": 8.0,
        "shadow_point": {"x": 1.0182337649086284, "y": -1.0182337649086284},
        "shadow_smoothing": 1.0,
        "shape_clip_x": False,
        "shape_clip_y": False,
        "style_name": "",
        "sub_type": 0,
        "text_alpha": 1.0,
        "text_color": color,
        "text_preset_resource_id": "",
        "text_size": 30,
        "text_to_audio_ids": [],
        "tts_auto_update": False,
        "type": "text",
        "typesetting": 0,
        "underline": False,
        "underline_offset": 0.22,
        "underline_width": 0.05,
        "use_effect_default_color": True,
        "words": {"end_time": [], "start_time": [], "text": []},
    }


def text_segment(material_id, start_us, dur_us, transform_y, render_index):
    """文本 segment：source_timerange 为 null，靠 clip.transform 定位。"""
    return {
        "cartoon": False,
        "clip": {
            "alpha": 1.0,
            "flip": {"horizontal": False, "vertical": False},
            "rotation": 0.0,
            "scale": {"x": 1.0, "y": 1.0},
            "transform": {"x": 0.0, "y": transform_y},
        },
        "common_keyframes": [],
        "enable_adjust": False,
        "enable_color_curves": True,
        "enable_color_wheels": True,
        "enable_lut": False,
        "enable_smart_color_adjust": False,
        "extra_material_refs": [],
        "group_id": "",
        "hdr_settings": None,
        "id": uid(),
        "intensifies_audio": False,
        "is_placeholder": False,
        "is_tone_modify": False,
        "keyframe_refs": [],
        "last_nonzero_volume": 1.0,
        "material_id": material_id,
        "render_index": render_index,
        "reverse": False,
        "source_timerange": None,
        "speed": 1.0,
        "target_timerange": {"duration": dur_us, "start": start_us},
        "template_id": "",
        "template_scene": "default",
        "track_attribute": 0,
        "track_render_index": 0,
        "uniform_scale": {"on": True, "value": 1.0},
        "visible": True,
        "volume": 1.0,
    }


def build(plan):
    meta = plan["meta"]
    lay = meta["layout"]
    font_path = meta["font_path"]
    shots = plan["shots"]

    total = round(sum(float(s["seconds"]) for s in shots), 3)
    target = float(meta["total_seconds"])
    if abs(total - target) > 0.05:
        print("[WARN] plan.json 各镜时长合计 %.1fs，与 total_seconds %.1fs 不一致，按合计值生成"
              % (total, target))

    materials = {k: [] for k in MAT_KEYS}
    anim_id = uid()
    materials["material_animations"].append(
        {"animations": [], "id": anim_id, "type": "sticker_animation"})

    card_track = {"attribute": 0, "flag": 0, "id": uid(), "segments": [], "type": "text"}
    voice_track = {"attribute": 0, "flag": 0, "id": uid(), "segments": [], "type": "text"}

    render_index = 14008
    t = 0.0
    for s in shots:
        dur = us(s["seconds"])
        start = us(t)

        card = text_material(s["card"], lay["card_font_size"], lay["card_color"], font_path)
        materials["texts"].append(card)
        seg = text_segment(card["id"], start, dur, lay["card_transform_y"], render_index)
        seg["extra_material_refs"] = [anim_id]
        card_track["segments"].append(seg)
        render_index += 1

        voice = text_material(s["voice"], lay["voice_hint_font_size"],
                              lay["voice_hint_color"], font_path)
        materials["texts"].append(voice)
        seg = text_segment(voice["id"], start, dur, lay["voice_hint_transform_y"], render_index)
        seg["extra_material_refs"] = [anim_id]
        voice_track["segments"].append(seg)
        render_index += 1

        t += s["seconds"]

    draft_id = uid()
    timeline_id = uid()
    now_us = int(time.time() * US)

    platform = {
        "app_id": 3704, "app_source": APP_SOURCE, "app_version": APP_VERSION,
        "device_id": DEVICE_ID, "hard_disk_id": "", "mac_address": MAC_ADDRESS,
        "os": "windows", "os_version": OS_VERSION,
    }

    content = {
        "canvas_config": {
            "height": meta["canvas"]["height"],
            "ratio": meta["canvas"]["ratio"],
            "width": meta["canvas"]["width"],
        },
        "color_space": 0,
        "config": {
            "adjust_max_index": 1, "attachment_info": [], "combination_max_index": 1,
            "export_range": None, "extract_audio_last_index": 1,
            "lyrics_recognition_id": "", "lyrics_sync": True, "lyrics_taskinfo": [],
            "maintrack_adsorb": True, "material_save_mode": 0,
            "original_sound_last_index": 1, "record_audio_last_index": 1,
            "sticker_max_index": 1, "subtitle_recognition_id": "", "subtitle_sync": True,
            "subtitle_taskinfo": [], "system_font_list": [], "video_mute": False,
            "zoom_info_params": None,
        },
        "cover": None,
        "create_time": 0,
        "duration": us(total),
        "extra_info": None,
        "fps": float(meta["fps"]),
        "free_render_index_mode_on": False,
        "group_container": None,
        "id": timeline_id,
        "keyframe_graph_list": [],
        "keyframes": {"adjusts": [], "audios": [], "effects": [], "filters": [],
                      "handwrites": [], "stickers": [], "texts": [], "videos": []},
        "last_modified_platform": dict(platform),
        "materials": materials,
        "mutable_config": None,
        "name": "",
        "new_version": NEW_VERSION,
        "platform": dict(platform),
        "relationships": [],
        "render_index_track_mode_on": False,
        "retouch_cover": None,
        "source": "default",
        "static_cover_image_path": "",
        "tracks": [card_track, voice_track],
        "update_time": 0,
        "version": 360000,
    }

    meta_info = {
        "draft_cloud_capcut_purchase_info": "",
        "draft_cloud_last_action_download": False,
        "draft_cloud_materials": [],
        "draft_cloud_purchase_info": "",
        "draft_cloud_template_id": "",
        "draft_cloud_tutorial_info": "",
        "draft_cloud_videocut_purchase_info": "",
        "draft_cover": "draft_cover.jpg",
        "draft_deeplink_url": "",
        "draft_enterprise_info": {"draft_enterprise_extra": "", "draft_enterprise_id": "",
                                  "draft_enterprise_name": ""},
        "draft_fold_path": "",
        "draft_id": draft_id,
        "draft_is_article_video_draft": False,
        "draft_is_from_deeplink": "false",
        "draft_materials": [],
        "draft_materials_copied_info": [],
        "draft_name": meta["draft_name"],
        "draft_new_version": "",
        "draft_removable_storage_device": "",
        "draft_root_path": DRAFT_ROOT,
        "draft_segment_extra_info": [],
        "draft_timeline_materials_size_": 0,
        "tm_draft_cloud_completed": "",
        "tm_draft_cloud_modified": 0,
        "tm_draft_create": now_us,
        "tm_draft_modified": now_us,
        "tm_duration": us(total),
    }

    return content, meta_info, draft_id, us(total), platform


def write_draft(content, meta_info, draft_id, duration_us, platform, alt=False):
    name = meta_info["draft_name"]
    folder = os.path.join(DRAFT_ROOT, name)
    os.makedirs(folder, exist_ok=True)
    for sub in ("Resources", "common_attachment", "matting"):
        os.makedirs(os.path.join(folder, sub), exist_ok=True)

    json_file = "draft_info.json" if alt else "draft_content.json"
    blob = json.dumps(content, ensure_ascii=False, separators=(",", ":"))
    for fn in ("draft_content.json", "draft_content.json.bak"):
        with open(os.path.join(folder, fn), "w", encoding="utf-8") as f:
            f.write(blob)
    if alt:
        with open(os.path.join(folder, "draft_info.json"), "w", encoding="utf-8") as f:
            f.write(blob)

    meta_info["draft_fold_path"] = folder.replace("\\", "/")
    with open(os.path.join(folder, "draft_meta_info.json"), "w", encoding="utf-8") as f:
        json.dump(meta_info, f, ensure_ascii=False, separators=(",", ":"))

    with open(os.path.join(folder, "draft_settings"), "w", encoding="utf-8") as f:
        f.write("[General]\ndraft_create_time=%d\ndraft_last_edit_time=%d\n"
                "real_edit_seconds=0\nreal_edit_keys=0\n"
                % (int(time.time()), int(time.time())))

    with open(os.path.join(folder, "draft_agency_config.json"), "w", encoding="utf-8") as f:
        json.dump({"marterials": None, "use_converter": False, "video_resolution": 720},
                  f, ensure_ascii=False, separators=(",", ":"))

    with open(os.path.join(folder, "attachment_pc_common.json"), "w", encoding="utf-8") as f:
        json.dump({"commercial_music_category_ids": [], "items": [], "pc_feature_flag": 0,
                   "task_id": "", "template_item_infos": [], "unlock_template_ids": []},
                  f, ensure_ascii=False, separators=(",", ":"))

    with open(os.path.join(folder, "draft_virtual_store.json"), "w", encoding="utf-8") as f:
        json.dump({"draft_materials": [], "draft_virtual_store": [
            {"type": 0, "value": [{"creation_time": 0, "display_name": "", "filter_type": 0,
                                   "id": "", "import_time": 0, "import_time_us": 0,
                                   "sort_sub_type": 0, "sort_type": 0}]},
            {"type": 1, "value": []},
            {"type": 2, "value": []},
        ]}, f, ensure_ascii=False, separators=(",", ":"))

    with open(os.path.join(folder, "key_value.json"), "w", encoding="utf-8") as f:
        f.write("{}")

    cover = os.path.join(folder, "draft_cover.jpg")
    if os.path.exists(COVER_SRC) and not os.path.exists(cover):
        shutil.copyfile(COVER_SRC, cover)

    register(draft_id, name, folder, json_file, duration_us, platform)
    return folder, json_file


def register(draft_id, name, folder, json_file, duration_us, platform):
    with open(ROOT_META, encoding="utf-8") as f:
        store = json.load(f)
    entries = store["all_draft_store"]
    entries[:] = [e for e in entries if e.get("draft_name") != name]
    now_us = int(time.time() * US)
    folder_fwd = folder.replace("\\", "/")
    entries.append({
        "cloud_draft_cover": False,
        "cloud_draft_sync": False,
        "draft_cloud_last_action_download": False,
        "draft_cloud_purchase_info": "",
        "draft_cloud_template_id": "",
        "draft_cloud_tutorial_info": "",
        "draft_cloud_videocut_purchase_info": "",
        "draft_cover": folder_fwd + "/draft_cover.jpg",
        "draft_fold_path": folder_fwd,
        "draft_id": draft_id,
        "draft_is_ai_shorts": False,
        "draft_is_cloud_temp_draft": False,
        "draft_is_infinite_canvas_draft": False,
        "draft_is_invisible": False,
        "draft_is_pippit_draft": False,
        "draft_is_web_article_video": False,
        "draft_json_file": folder_fwd + "/" + json_file,
        "draft_name": name,
        "draft_new_version": "",
        "draft_root_path": DRAFT_ROOT,
        "draft_timeline_materials_size": 0,
        "draft_type": "",
        "draft_web_article_video_enter_from": "",
        "pippit_avatar_url": "",
        "pippit_extra_info": "",
        "pippit_id": "",
        "pippit_user_name": "",
        "streaming_edit_draft_ready": True,
        "tm_draft_cloud_completed": "",
        "tm_draft_cloud_entry_id": -1,
        "tm_draft_cloud_modified": 0,
        "tm_draft_cloud_parent_entry_id": -1,
        "tm_draft_cloud_space_id": -1,
        "tm_draft_cloud_user_id": -1,
        "tm_draft_create": now_us,
        "tm_draft_modified": now_us,
        "tm_draft_removed": 0,
        "tm_duration": duration_us,
    })
    with open(ROOT_META, "w", encoding="utf-8") as f:
        json.dump(store, f, ensure_ascii=False, separators=(",", ":"))


def print_timeline(plan):
    shots = plan["shots"]
    t = 0.0
    print("%-3s %-6s %-6s %-22s %s" % ("#", "起", "止", "上方字卡", "口播"))
    for s in shots:
        start, end = t, t + s["seconds"]
        card = s["card"].replace("\n", " / ")
        print("%-3d %-6.1f %-6.1f %-22s %s" % (s["no"], start, end, card, s["voice"]))
        t = end
    print("合计 %.1f 秒，共 %d 镜" % (t, len(shots)))


def main():
    plan = load_plan()
    print_timeline(plan)
    if "--print-only" in sys.argv:
        return
    content, meta_info, draft_id, duration_us, platform = build(plan)
    folder, json_file = write_draft(content, meta_info, draft_id, duration_us, platform,
                                    alt="--alt" in sys.argv)
    print("")
    print("草稿目录 : %s" % folder)
    print("工程文件 : %s" % json_file)
    print("草稿 ID  : %s" % draft_id)
    print("画布     : %dx%d  时长 %.1fs  文本素材 %d 条"
          % (content["canvas_config"]["width"], content["canvas_config"]["height"],
             duration_us / US, len(content["materials"]["texts"])))
    print("注册表   : %s （all_draft_store 已更新）" % ROOT_META)


if __name__ == "__main__":
    main()
