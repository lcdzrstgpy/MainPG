"""combo_kit 领域错误与常量。"""
from __future__ import annotations


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
# 轮播图 2 / 轮播图 3 / 白底尺寸图 / 细节图 为并发生图；详情图为本地拼接合成。
IMAGE_ROLES: tuple[dict[str, str], ...] = (
    {"role": "main", "label": "套装主图"},
    {"role": "carousel_2", "label": "轮播图 2"},
    {"role": "carousel_3", "label": "轮播图 3"},
    {"role": "white_bg", "label": "白底尺寸图"},
    {"role": "detail_shot", "label": "细节图"},
    {"role": "detail_page", "label": "详情图"},
)

# 套装主图角色：主体解析阶段生成后直接复用，不再二次调用生图 API。
FUSION_MAIN_ROLE = "main"

# 允许用户在 Prompt 配置页自定义的辅助提示词角色（3 项）。
# 主图、细节图、详情图不开放用户自定义提示词：主图用融合模板，细节/详情用老模块模板/拼接。
EDITABLE_PROMPT_ROLES: tuple[str, ...] = ("carousel_2", "carousel_3", "white_bg")

# 需要调用生图 API 的角色（不含主图/详情图）。
GENERATED_API_ROLES: tuple[str, ...] = ("carousel_2", "carousel_3", "white_bg", "detail_shot")

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
