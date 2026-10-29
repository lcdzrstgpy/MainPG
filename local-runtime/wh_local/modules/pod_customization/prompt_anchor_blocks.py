"""全定制列式生成与复刻派生面板共用的「锚定式生成」提示词段落。

这里的英文文本逐字从 ``column_prompts`` 搬出，两条链路共用同一份，避免各自漂移：
改动这里会同时影响全定制的派生轮与复刻的派生面板，必须成对回归。
"""

from __future__ import annotations

OUTPUT_FORMAT_LOCK = (
    "OUTPUT FORMAT LOCK (mandatory in rounds 2/3/4 as well) — every call returns ONE square 2x2 contact "
    "sheet with exactly four equal panels and one clean centred divider, same format as round 1. Never "
    "return a single full-frame image, a 1x4 or 4x1 strip, unequal panels, a collage with borders, or "
    "any number of panels other than four. \"This call only needs the detail image\" never means "
    "\"return one image\": the call still returns four detail panels, one per style."
)

ANCHOR_BINDING = (
    "ANCHOR BINDING — the four attached reference images ARE the accepted round-1 result. They are the "
    "FOUR different styles of this contact sheet, bound by position, not by content:\n"
    "- reference image 1 = the product shown in the TOP-LEFT quadrant\n"
    "- reference image 2 = TOP-RIGHT\n"
    "- reference image 3 = BOTTOM-LEFT\n"
    "- reference image 4 = BOTTOM-RIGHT\n"
    "Each quadrant uses ONLY its own reference image. Never regenerate the product or the artwork from "
    "scratch, and never copy, blend or swap motifs, colours or layouts between quadrants."
)

RIGID_ARTWORK_LOCK = (
    "RIGID ARTWORK LOCK — the printed artwork is a rigid sheet glued to the product. Changing the "
    "camera rotates the whole sheet with the product; it is never permission to re-layout, re-centre, "
    "rotate, rescale or re-space the artwork. The hero motif's position on the product face, its size "
    "relative to the face, its orientation and its distance from seams/edges must be identical to the "
    "anchor. A detail panel is a zoom into the SAME layout, never a new version of the artwork."
)

ANCHOR_CONTINUITY = (
    "ANCHOR CONTINUITY — keep the anchor's IDENTITY, never its camera:\n"
    "- MUST stay identical: product identity, structure, material, base colour, printed artwork, scene, "
    "set surface, light direction, colour temperature and contrast.\n"
    "- MUST change with this round's direction: camera angle, shot distance, crop, framing and depth of "
    "field. A macro round is expected to have shallow focus even when the anchor is deep-focus.\n"
    "- Never reproduce the reference camera: a panel that is the same shot as its reference image "
    "(same distance, same crop, same full-product view) is a FAILED result.\n"
    "- If the previous round's scene cannot be reproduced from the anchor, keep the anchor's scene — "
    "never invent a new set."
)

CAMERA_MUST_DIFFER = (
    "CAMERA MUST DIFFER — this round's camera must be visibly different from the reference images' "
    "camera and from every other round: different shot distance and different framing. Reproducing "
    "the reference framing (same distance, same crop, same full-product view) is a FAILED result."
)


__all__ = [
    "ANCHOR_BINDING",
    "ANCHOR_CONTINUITY",
    "CAMERA_MUST_DIFFER",
    "OUTPUT_FORMAT_LOCK",
    "RIGID_ARTWORK_LOCK",
]
