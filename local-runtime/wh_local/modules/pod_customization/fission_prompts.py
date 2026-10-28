"""裂变共用的默认四格构图常量。

出方案改由 AI 一次产出（``fission_runtime``），本模块只保留「用户没配裂变构图模板时」
的兜底机位；提示词编译已下线。
"""

from __future__ import annotations

from typing import Any


FISSION_DEFAULT_TEMPLATE_ID = "fission_default_v1"
FISSION_DEFAULT_TEMPLATE_VERSION = "1"


def default_fission_composition() -> dict[str, Any]:
    return {
        "template_id": FISSION_DEFAULT_TEMPLATE_ID,
        "template_version": FISSION_DEFAULT_TEMPLATE_VERSION,
        "panels": [
            {"slot_id": "panel_1", "role": "primary_image", "shot": "wide lifestyle"},
            {"slot_id": "panel_2", "role": "pattern_detail", "shot": "macro detail"},
            {"slot_id": "panel_3", "role": "structure_or_usage", "shot": "three-quarter"},
            {"slot_id": "panel_4", "role": "material_image", "shot": "clean neutral"},
        ],
    }


__all__ = [
    "FISSION_DEFAULT_TEMPLATE_ID",
    "FISSION_DEFAULT_TEMPLATE_VERSION",
    "default_fission_composition",
]
