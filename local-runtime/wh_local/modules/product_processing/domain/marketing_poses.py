"""精选处理的多角度姿势计划。

问题：同一商品的多张营销图如果都用同一张竖直白底参考，生图模型会照抄姿势，
一批图看起来全是同一个角度。这里给每个输出位分配不同的朝向，并明确要求模型
「重新摆放、不要照抄参考图」。

白底主图不参与轮换，固定正向直立（listing 主图的常规要求）。
"""

from __future__ import annotations

POSES: dict[str, str] = {
    "upright": "standing upright, straight-on front view",
    "tilt_right": "tilted about 30 degrees clockwise, diagonal composition",
    "diagonal": "laid diagonally from the lower-left to the upper-right",
    "horizontal": "lying horizontally across the frame",
    "three_quarter": "three-quarter angle, rotated about 35 degrees toward the camera",
    "mirror": "mirrored orientation with the handle on the left",
}

# 前 4 个位先覆盖差异最大的四种朝向；超过 4 个再轮换。
POSE_ORDER: tuple[str, ...] = (
    "upright",
    "tilt_right",
    "diagonal",
    "horizontal",
    "three_quarter",
    "mirror",
)


def pose_for(index: int, *, enabled: bool = True) -> str:
    """按输出位分配姿势；关闭多角度时统一正向。"""
    if not enabled:
        return "upright"
    return POSE_ORDER[int(index) % len(POSE_ORDER)]


def pose_rule(pose: str, *, set_count: int = 1) -> str:
    """拼进生图提示词的朝向约束。"""
    text = POSES.get(str(pose or ""), POSES["upright"])
    count = max(1, int(set_count or 1))
    if count > 1:
        return (
            f"All {count} pieces of the set must be arranged together in this orientation: "
            f"{text}. Re-stage them even if the reference images show a different pose; "
            "do not merge pieces, do not omit any."
        )
    return (
        f"The product must be staged in this orientation: {text}. "
        "Re-stage it even if the reference image shows a different pose."
    )
