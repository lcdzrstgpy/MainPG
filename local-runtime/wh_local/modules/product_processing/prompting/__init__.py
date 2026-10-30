"""V2 提示词构建：结构化档案 + 场景卡 + 逐格计划 + 单一契约。"""

from .brief import ProductBrief, infer_brief
from .builder import build_grid_prompt
from .lint import lint_prompt

__all__ = ["ProductBrief", "infer_brief", "build_grid_prompt", "lint_prompt"]
