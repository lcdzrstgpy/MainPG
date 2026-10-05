"""combo_kit 领域错误与常量。"""
from __future__ import annotations

from typing import Any


class ComboKitError(RuntimeError):
    """基类，路由层映射到 HTTP 状态码。"""

    status_code = 400


class ComboKitNotFound(ComboKitError):
    status_code = 404


class ComboKitConflict(ComboKitError):
    status_code = 409


class ComboKitValidationError(ComboKitError):
    status_code = 422


# 一套套装固定生成的成品图（直接出图，无四宫格裁切）。
# 顺序即前台展示顺序。第 1 张「套装主图」由主体解析阶段的融合主图复用，不重复生成；
# 使用场景图 1/2 / 白底尺寸图 / 细节图 为并发生图；详情图为本地拼接合成。
# 注：carousel_2 / carousel_3 为历史 key（沿用不改，避免已存套装的提示词与图片失配），
# 其业务语义是「使用场景图 1 / 使用场景图 2」。
IMAGE_ROLES: tuple[dict[str, str], ...] = (
    {"role": "main", "label": "套装主图"},
    {"role": "carousel_2", "label": "使用场景图 1"},
    {"role": "carousel_3", "label": "使用场景图 2"},
    {"role": "white_bg", "label": "白底尺寸图"},
    {"role": "detail_shot", "label": "细节图"},
    {"role": "detail_page", "label": "详情图"},
)

# 需要「使用场景图」基础模板的角色（放开 no human / 中性背景限制，允许真实场景与手部）。
SCENE_ROLES: tuple[str, ...] = ("carousel_2", "carousel_3")

# 套装主图角色：主体解析阶段生成后直接复用，不再二次调用生图 API。
FUSION_MAIN_ROLE = "main"

# 允许用户在 Prompt 配置页自定义的辅助提示词角色（4 项）。
# 主图、详情图不开放用户自定义提示词：主图用融合模板，详情图用本地拼接。
# 细节图开放但只做「模板 + 用户补充」，用户提示词不得覆盖固定模板。
EDITABLE_PROMPT_ROLES: tuple[str, ...] = ("carousel_2", "carousel_3", "white_bg", "detail_shot")

# 需要调用生图 API 的角色（不含主图/详情图）。
GENERATED_API_ROLES: tuple[str, ...] = ("carousel_2", "carousel_3", "white_bg", "detail_shot")

# 生成选型：决定「套装主图」的产生方式与提示词方向，由用户在①套装信息里二选一。
# - bundle：多件不同商品组合成一套 —— 逐图主体词 + 蒙版 → 融合主图 → 派生各成品图（原有流程）。
# - multiview：同一商品的多张视角图（含容器内部视角、包装展开图）—— 不做融合，
#   直接以全部视角图为参考生成商品主图，再派生各成品图。
GENERATION_MODES: tuple[dict[str, str], ...] = (
    {
        "mode": "bundle",
        "label": "套装组合",
        "description": "2~6 件不同商品组成一个套装：逐图填主体词并框选主体，先把成员融合成一张主图，再派生成品图。",
    },
    {
        "mode": "multiview",
        "label": "单品多视角",
        "description": "同一商品的多个视角（正面/侧面/内部/包装展开图）：无需融合，直接以全部视角图为参考生成商品主图。",
    },
)

GENERATION_MODE_KEYS: tuple[str, ...] = tuple(str(item["mode"]) for item in GENERATION_MODES)
DEFAULT_GENERATION_MODE = "bundle"


def normalize_generation_mode(value: Any) -> str:
    """校验并归一生成选型；未识别或空缺时回退默认选型（bundle）。"""
    mode = str(value or "").strip()
    return mode if mode in GENERATION_MODE_KEYS else DEFAULT_GENERATION_MODE


# 图片上传数量边界（前端 + 后端双重校验）。
MIN_IMAGES = 2
MAX_IMAGES = 6

# 积分扣费标准（业务写死，文本/生图完全隔离）。
TEXT_POINTS = 20   # 一套文本（标题+详情描述+五点）统一扣费
IMAGE_POINTS = 100  # 一整套 6 张成品图统一扣费

# 业务状态机：顺序绝对不可修改。
STATUS_FLOW = (
    "draft",             # 已录入套装信息
    "subject_ready",     # 主体解析完成（串行：主体词+蒙版 → AI 解析）
    "text_ready",        # 文本生成完成
    "images_ready",      # 6 张成品图生成完成
    "preview_pending",   # 已进入预检
    "completed",         # 预检通过
    "failed",
)
