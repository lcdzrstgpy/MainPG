"""麻将单牌 3D 渲染（Blender bpy 脚本，P0 样张）。

为什么用建模渲染而不是继续抠图贴图：
- 牌面字号/花纹是商品本体，AI 重画必然造字；本地贴图又会因为每张图的透视、
  光照不同而"贴纸感"明显，尺寸也不一致。
- 建模只要把「牌体几何 + 牌面图案」固定下来，牌面尺寸、倒角、厚度、光影都是
  确定值，换机位/换数量不会漂移。

本脚本只做一件事：给一张牌面图案（RGBA，alpha 即墨迹），渲染成一张产品感样张。
图案之外的场景（台面、手、背景）留给 AI 空景生成，不在这里做。

用法::

    blender -b --factory-startup --python tools/render_tile.py -- \
        --pattern <RGBA png> --out <png> [--length 42] [--width 30] \
        [--thickness 19] [--res 800] [--samples 64] [--transparent] \
        [--elevation 34] [--azimuth 22] [--pattern-cover 0.70]

坐标约定：牌平放，牌面朝 +Z；长边沿 Y（42mm），短边沿 X（30mm），厚度沿 Z。

牌面贴图不用「对象坐标投影」：Blender 各版本 Texture Coordinate 的 Object 输出
单位/归一化方式不一致，会把图案拉大或推到画外。这里直接给朝上的面写 UV，
图案位置、大小、朝向全部由 Python 决定，渲染结果可复现。
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import bpy

# 命令行按毫米描述，场景内部按米建模（见 main 里的说明）。
_MM = 0.001


def parse_args() -> argparse.Namespace:
    argv = sys.argv
    argv = argv[argv.index("--") + 1 :] if "--" in argv else []
    parser = argparse.ArgumentParser(description="Render mahjong tiles (single or a cluster).")
    parser.add_argument("--pattern", default="", help="单张牌面图案 RGBA PNG")
    parser.add_argument(
        "--patterns",
        default="",
        help="多张牌面图案，逗号分隔；与 --layout 配合一次渲出一撮牌",
    )
    parser.add_argument(
        "--layout",
        default="single",
        choices=("single", "row", "fan", "grid"),
        help="single=单牌, row=并排, fan=扇形摊开, grid=多行铺开",
    )
    parser.add_argument("--gap", type=float, default=2.0, help="并排间距 mm")
    parser.add_argument("--out", required=True, help="输出 PNG 路径")
    parser.add_argument("--length", type=float, default=42.0, help="牌面长边 mm")
    parser.add_argument("--width", type=float, default=30.0, help="牌面短边 mm")
    parser.add_argument("--thickness", type=float, default=19.0, help="牌体厚度 mm")
    parser.add_argument("--res", type=int, default=800)
    parser.add_argument("--samples", type=int, default=64)
    parser.add_argument("--pattern-cover", type=float, default=0.70, help="图案宽占牌面短边的比例")
    parser.add_argument("--pattern-shift", type=float, default=0.0, help="图案沿长边的偏移（占牌面长边比例）")
    parser.add_argument("--bevel", type=float, default=1.5, help="倒角宽度 mm")
    parser.add_argument(
        "--body",
        default="ivory",
        choices=("ivory", "gold"),
        help="牌体（非牌面）材质：ivory=象牙白；gold=金色亮片（美式亚克力牌常见）",
    )
    parser.add_argument(
        "--face-layer",
        type=float,
        default=0.0,
        help="牌面白层占牌厚的比例（0=不分层）。美式牌常见「白色面层 + 彩色背层」两段结构",
    )
    parser.add_argument(
        "--params",
        default="",
        help="牌型参数 JSON（尺寸/分层/配色/版式）；给了就覆盖同名命令行参数",
    )
    parser.add_argument("--elevation", type=float, default=34.0, help="相机仰角（度）")
    parser.add_argument("--azimuth", type=float, default=22.0, help="相机方位角（度）")
    parser.add_argument("--focal", type=float, default=85.0, help="相机焦距 mm")
    parser.add_argument("--margin", type=float, default=1.55, help="画面留白系数（越大牌越小）")
    parser.add_argument("--transparent", action="store_true", help="透明背景输出")
    parser.add_argument("--engine", default="eevee", choices=("eevee", "cycles"))
    return parser.parse_args(argv)


def pattern_paths(args: argparse.Namespace) -> list[str]:
    raw = str(args.patterns or "").strip()
    if raw:
        items = [item.strip() for item in raw.split(",") if item.strip()]
        if items:
            return items
    if args.pattern:
        return [str(args.pattern)]
    raise SystemExit("需要 --pattern 或 --patterns")


@dataclass(frozen=True)
class Placement:
    """一块牌在桌面上的位置（mm、度）。原点为牌体中心，牌面朝 +Z。"""

    x: float = 0.0
    y: float = 0.0
    rotation: float = 0.0


def build_layout(name: str, count: int, *, width: float, length: float, gap: float) -> list[Placement]:
    """按牌面尺寸算摆位；count 与图案数量一致。"""
    count = max(1, count)
    if name == "row" or (name == "single" and count > 1):
        step = width + max(gap, 0.5)
        total = step * (count - 1)
        return [
            Placement(
                x=-total / 2 + index * step,
                y=0.0,
                # 交替 ±2°，避免一排牌过于机械。
                rotation=2.0 if index % 2 else -2.0,
            )
            for index in range(count)
        ]
    if name == "fan":
        # 手牌式摊开：中间靠前、两侧后撤并向外转，读起来像"一撮牌"。
        spread = width * 1.02
        placements: list[Placement] = []
        for index in range(count):
            offset = index - (count - 1) / 2
            placements.append(
                Placement(
                    x=offset * spread,
                    y=-abs(offset) * (length * 0.10),
                    rotation=-offset * 7.0,
                )
            )
        return placements
    if name == "grid":
        # 方图里摆一排长牌会显得又细又小；三列多行铺开更接近产品图的构图。
        columns = max(1, math.ceil(math.sqrt(count)))
        rows = max(1, math.ceil(count / columns))
        step_x = width + max(gap, 0.5) * 3.0
        step_y = length + max(gap, 0.5) * 3.0
        placements = []
        for index in range(count):
            column = index % columns
            row = index // columns
            placements.append(
                Placement(
                    x=(column - (columns - 1) / 2.0) * step_x,
                    y=(row - (rows - 1) / 2.0) * step_y,
                    rotation=3.0 if (index + row) % 2 else -3.0,
                )
            )
        return placements
    return [Placement()]


def cluster_extent(placements: list[Placement], *, width: float, length: float) -> tuple[float, float]:
    """摆位后的整体包围盒（宽, 长），单位与入参一致。"""
    half_w, half_l = width / 2.0, length / 2.0
    span_x = 0.0
    span_y = 0.0
    for placement in placements:
        radians = math.radians(placement.rotation)
        cos, sin = abs(math.cos(radians)), abs(math.sin(radians))
        span_x = max(span_x, abs(placement.x) + half_w * cos + half_l * sin)
        span_y = max(span_y, abs(placement.y) + half_w * sin + half_l * cos)
    return span_x * 2.0, span_y * 2.0


def projected_size(
    span_x: float,
    span_y: float,
    thickness: float,
    *,
    elevation: float,
    azimuth: float,
) -> tuple[float, float]:
    """把桌面包围盒换算成「画面里的宽 / 高」，用于取景。

    不换算的话，斜着看的一排牌在画面里会比包围盒更宽，容易顶到画幅边缘。
    """

    az = math.radians(azimuth)
    el = math.radians(elevation)
    width = span_x * abs(math.cos(az)) + span_y * abs(math.sin(az))
    depth = span_y * abs(math.cos(az)) + span_x * abs(math.sin(az))
    height = depth * abs(math.sin(el)) + thickness * abs(math.cos(el))
    return width, height


def _hex_to_linear(value: str, fallback: tuple[float, float, float] = (0.9, 0.9, 0.88)):
    """把 #RRGGBB（sRGB）转成 Blender 需要的线性色。"""

    text = str(value or "").strip().lstrip("#")
    if len(text) != 6:
        return (*fallback, 1.0)

    def channel(pair: str) -> float:
        raw = int(pair, 16) / 255.0
        return raw / 12.92 if raw <= 0.04045 else ((raw + 0.055) / 1.055) ** 2.4

    return (channel(text[0:2]), channel(text[2:4]), channel(text[4:6]), 1.0)


def load_params(path: str) -> dict:
    """读牌型参数文件；坏文件直接报错，避免悄悄用品类默认值糊弄过去。"""

    if not str(path or "").strip():
        return {}
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"牌型参数不是对象: {path}")
    return data


def apply_params(args: argparse.Namespace) -> dict:
    """参数文件覆盖命令行：尺寸、倒角、分层、配色、图案占位。"""

    data = load_params(args.params)
    if not data:
        return {}
    size = data.get("size_mm") or []
    if len(size) == 3:
        args.width, args.length, args.thickness = (float(value) for value in size)
    if data.get("bevel_mm") is not None:
        args.bevel = float(data["bevel_mm"])
    face = data.get("face") or {}
    if face.get("cover") is not None:
        args.pattern_cover = float(face["cover"])
    layers = data.get("layers") or []
    if isinstance(layers, list) and layers:
        # 分层色带整条交给下面 resolve_bands 处理：面层/背层可能不止两层。
        args.layers = [layer for layer in layers if isinstance(layer, dict)]
    for layer in layers:
        if not isinstance(layer, dict):
            continue
        if layer.get("name") == "face":
            if layer.get("thickness_ratio") is not None:
                args.face_layer = float(layer["thickness_ratio"])
            if layer.get("color"):
                args.face_color = str(layer["color"])
        elif layer.get("name") == "body":
            if layer.get("finish"):
                args.body_finish = str(layer["finish"])
            if layer.get("color"):
                args.body_color = str(layer["color"])
    return data


def resolve_bands(args: argparse.Namespace, thickness: float) -> list[tuple[float, float, dict]]:
    """把参数里的 layers 折成「从牌面往牌底排好的色带」。

    ``layers`` 从牌面往下写，``thickness_ratio`` 之和视为 1。返回
    ``[(z_low, z_high, layer)]``，z 用对象局部坐标（-t/2 .. +t/2），牌面朝 +Z。
    没有参数文件时退回 ``--face-layer`` 的二分法，行为与旧版一致。
    """

    layers = getattr(args, "layers", None)
    if not isinstance(layers, list) or not layers:
        face_ratio = max(0.0, min(0.95, float(getattr(args, "face_layer", 0.0) or 0.0)))
        layers = [
            {"name": "face", "thickness_ratio": face_ratio},
            {"name": "body", "thickness_ratio": 1.0 - face_ratio},
        ]
    ratios = [max(0.0, float(layer.get("thickness_ratio") or 0.0)) for layer in layers]
    total = sum(ratios)
    if total <= 1e-6:
        layers, ratios, total = [{"name": "body", "thickness_ratio": 1.0}], [1.0], 1.0
    bands: list[list] = []
    top = thickness / 2.0
    for layer, ratio in zip(layers, ratios):
        height = thickness * ratio / total
        if height <= 1e-9:
            continue
        bands.append([top - height, top, layer])
        top -= height
    if bands:
        # 浮点累加会留一点缝，最后一段直接贴到牌底。
        bands[-1][0] = -thickness / 2.0
    return [(low, high, layer) for low, high, layer in bands]


def resolve_finish(args: argparse.Namespace) -> None:
    """--body 是简写；参数文件里的 finish 优先。"""

    finish = getattr(args, "body_finish", "") or ""
    if finish:
        args.body_finish = finish
    else:
        args.body_finish = "glitter" if args.body == "gold" else "gloss"
    if not getattr(args, "body_color", ""):
        args.body_color = "#C4A946" if args.body_finish == "glitter" else "#EBE5D4"
    if not getattr(args, "face_color", ""):
        args.face_color = "#F1EFE8"


def clear_scene() -> None:
    bpy.ops.wm.read_factory_settings(use_empty=True)


def pattern_rect(
    image: bpy.types.Image,
    *,
    length: float,
    width: float,
    cover: float,
) -> tuple[float, float]:
    """图案在牌面上的实际宽高（mm）：宽度按短边比例，高度按图案自身宽高比。"""
    pat_w, pat_h = image.size
    aspect = (pat_w / pat_h) if pat_h else 0.75
    cover = max(0.05, min(1.0, float(cover)))
    rect_w = width * cover
    rect_h = rect_w / max(aspect, 1e-6)
    # cover=1 表示「图案本身就是整张牌面」，此时不能内缩，否则四周会多出一圈白边。
    max_h = length * (0.995 if cover >= 0.999 else 0.88)
    if rect_h > max_h:
        rect_h = max_h
        rect_w = rect_h * aspect
    return rect_w, rect_h


def make_tile(length: float, width: float, thickness: float, bevel: float = 1.5):
    """圆角盒牌体：立方体 + bevel，尺寸单位为 mm。"""
    bpy.ops.mesh.primitive_cube_add(size=1.0, location=(0.0, 0.0, thickness / 2.0))
    obj = bpy.context.active_object
    obj.name = "MahjongTile"
    obj.scale = (width, length, thickness)
    bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)

    modifier = obj.modifiers.new("Bevel", "BEVEL")
    modifier.width = bevel
    modifier.segments = 5
    modifier.limit_method = "ANGLE"
    modifier.angle_limit = math.radians(30.0)
    modifier.harden_normals = True
    # 先烘焙倒角，后面要按真实面写 UV（否则修改器会重排 loop 顺序）。
    bpy.ops.object.modifier_apply(modifier=modifier.name)

    bpy.ops.object.shade_smooth()
    try:
        bpy.ops.object.shade_auto_smooth(angle=math.radians(40.0))
    except (AttributeError, RuntimeError, TypeError):
        mesh = obj.data
        if hasattr(mesh, "use_auto_smooth"):
            mesh.use_auto_smooth = True
            mesh.auto_smooth_angle = math.radians(40.0)
    return obj


def assign_face_uvs(
    obj,
    *,
    rect_w: float,
    rect_h: float,
    shift: float,
    uv_name: str = "TileFace",
) -> None:
    """给朝上的平面写 UV：图案正好铺满 rect_w × rect_h（居中，可沿长边偏移）。

    其余面（倒角、侧壁、背面）统一写到一个远在 [0,1] 之外的点，
    着色器里用「UV 是否在 [0,1] 内」判断墨迹范围，比依赖贴图扩展模式可靠。
    """
    mesh = obj.data
    uv_layer = mesh.uv_layers.new(name=uv_name)
    offset_y = shift
    for polygon in mesh.polygons:
        is_face = polygon.normal.z > 0.99
        for loop_index in polygon.loop_indices:
            if is_face:
                co = mesh.vertices[mesh.loops[loop_index].vertex_index].co
                uv_layer.data[loop_index].uv = (
                    co.x / rect_w + 0.5,
                    (co.y - offset_y) / rect_h + 0.5,
                )
            else:
                uv_layer.data[loop_index].uv = (4.0, 4.0)


def _inside_mask(nodes, links, value_socket, bounds: tuple[float, float]):
    """value 落在 bounds 区间内返回 1，否则返回 0。"""

    def compare(operation: str, threshold: float):
        node = nodes.new("ShaderNodeMath")
        node.operation = operation
        node.inputs[1].default_value = threshold
        links.new(value_socket, node.inputs[0])
        return node

    above = compare("GREATER_THAN", bounds[0])
    below = compare("LESS_THAN", bounds[1])
    both = nodes.new("ShaderNodeMath")
    both.operation = "MULTIPLY"
    links.new(above.outputs["Value"], both.inputs[0])
    links.new(below.outputs["Value"], both.inputs[1])
    return both


def make_tile_material(
    pattern_path: Path,
    *,
    base_color: str = "#F1EFE8",
    uv_name: str = "TileFace",
) -> bpy.types.Material:
    """象牙色牌体 + 牌面刻纹：墨迹处压暗、下沉，粗糙度变哑。"""
    material = bpy.data.materials.new("IvoryTile")
    material.use_nodes = True
    nodes = material.node_tree.nodes
    links = material.node_tree.links
    nodes.clear()

    output = nodes.new("ShaderNodeOutputMaterial")
    output.location = (900, 0)
    principled = nodes.new("ShaderNodeBsdfPrincipled")
    principled.location = (620, 0)
    links.new(principled.outputs["BSDF"], output.inputs["Surface"])

    # 牌面底色由牌型参数决定（=牌面层的颜色），不能在这里写死。
    ivory = _hex_to_linear(base_color, (0.925, 0.897, 0.828))
    principled.inputs["Base Color"].default_value = ivory
    principled.inputs["Metallic"].default_value = 0.0
    if "Specular IOR Level" in principled.inputs:
        principled.inputs["Specular IOR Level"].default_value = 0.52
    if "Subsurface Weight" in principled.inputs:
        principled.inputs["Subsurface Weight"].default_value = 0.05
        principled.inputs["Subsurface Radius"].default_value = (2.0, 1.6, 1.2)

    uv_map = nodes.new("ShaderNodeUVMap")
    uv_map.uv_map = uv_name
    uv_map.location = (-900, 0)
    separate = nodes.new("ShaderNodeSeparateXYZ")
    separate.location = (-700, 0)
    links.new(uv_map.outputs["UV"], separate.inputs["Vector"])

    # 只有 UV 落在 [0,1] 内的面才有墨迹（其余面被 assign_face_uvs 写到 (4,4)）。
    inside_u = _inside_mask(nodes, links, separate.outputs["X"], (-0.0005, 1.0005))
    inside_u.location = (-520, 140)
    inside_v = _inside_mask(nodes, links, separate.outputs["Y"], (-0.0005, 1.0005))
    inside_v.location = (-520, -40)

    inside = nodes.new("ShaderNodeMath")
    inside.operation = "MULTIPLY"
    inside.name = "FaceInsideMask"
    inside.location = (-120, 40)
    links.new(inside_u.outputs["Value"], inside.inputs[0])
    links.new(inside_v.outputs["Value"], inside.inputs[1])

    image = bpy.data.images.load(str(pattern_path))
    image.alpha_mode = "STRAIGHT"
    tex = nodes.new("ShaderNodeTexImage")
    tex.image = image
    tex.extension = "CLIP"
    tex.interpolation = "Linear"
    tex.location = (-120, 260)
    links.new(uv_map.outputs["UV"], tex.inputs["Vector"])

    ink = nodes.new("ShaderNodeMath")
    ink.operation = "MULTIPLY"
    ink.name = "InkAlpha"
    ink.location = (120, 200)
    links.new(tex.outputs["Alpha"], ink.inputs[0])
    links.new(inside.outputs["Value"], ink.inputs[1])

    mix = nodes.new("ShaderNodeMix")
    mix.data_type = "RGBA"
    mix.location = (360, 120)
    mix.inputs["A"].default_value = ivory
    links.new(ink.outputs["Value"], mix.inputs["Factor"])
    links.new(tex.outputs["Color"], mix.inputs["B"])
    links.new(mix.outputs["Result"], principled.inputs["Base Color"])

    # 刻纹：墨迹处高度压到 0，做出 0.15mm 的凹槽，花纹才有实体感。
    invert = nodes.new("ShaderNodeMath")
    invert.operation = "SUBTRACT"
    invert.inputs[0].default_value = 1.0
    invert.location = (120, -180)
    links.new(ink.outputs["Value"], invert.inputs[1])
    bump_pattern = nodes.new("ShaderNodeBump")
    bump_pattern.location = (360, -180)
    bump_pattern.inputs["Strength"].default_value = 0.8
    # 0.12mm：只做「印刷/上色」层级的轻微起伏。给到 0.28mm 会变成浮雕感，
    # 而这类花卉定制牌的图案是平面印刷，刻太深就和原图不是一个工艺了。
    bump_pattern.inputs["Distance"].default_value = 0.00012
    links.new(invert.outputs["Value"], bump_pattern.inputs["Height"])

    # 牌体细颗粒：避免高光死板，也让刻纹边缘不至于像矢量图。
    noise = nodes.new("ShaderNodeTexNoise")
    noise.location = (120, -420)
    noise.inputs["Scale"].default_value = 9.0
    noise.inputs["Detail"].default_value = 6.0
    links.new(uv_map.outputs["UV"], noise.inputs["Vector"])
    bump_grain = nodes.new("ShaderNodeBump")
    bump_grain.location = (540, -360)
    bump_grain.inputs["Strength"].default_value = 0.05
    bump_grain.inputs["Distance"].default_value = 0.00002
    links.new(noise.outputs["Fac"], bump_grain.inputs["Height"])
    links.new(bump_pattern.outputs["Normal"], bump_grain.inputs["Normal"])
    links.new(bump_grain.outputs["Normal"], principled.inputs["Normal"])

    # 抛光亚克力/象牙：整体光滑，刻纹内部更哑，高光会沿花纹断开。
    rough = nodes.new("ShaderNodeMath")
    rough.operation = "MULTIPLY_ADD"
    rough.location = (360, -560)
    rough.inputs[1].default_value = 0.05
    rough.inputs[2].default_value = 0.20
    links.new(noise.outputs["Fac"], rough.inputs[0])
    ink_rough = nodes.new("ShaderNodeMath")
    ink_rough.operation = "MULTIPLY_ADD"
    ink_rough.location = (540, -560)
    ink_rough.inputs[1].default_value = 0.20
    links.new(ink.outputs["Value"], ink_rough.inputs[0])
    links.new(rough.outputs["Value"], ink_rough.inputs[2])
    links.new(ink_rough.outputs["Value"], principled.inputs["Roughness"])
    return material


def make_body_material(finish: str, color: str) -> bpy.types.Material:
    """牌体（倒角、侧壁、底面）材质。

    finish=glitter 对应美式亚克力牌的金色亮片：金属底 + 细密颗粒凹凸，
    远看是金色块面，近看有闪片感，不能做成镜面金属。
    finish=gloss 是普通抛光牌体（象牙/纯色）。
    """

    material = bpy.data.materials.new(f"TileBody_{finish}")
    material.use_nodes = True
    nodes = material.node_tree.nodes
    links = material.node_tree.links
    principled = nodes.get("Principled BSDF")
    base = _hex_to_linear(color)
    if finish != "glitter":
        principled.inputs["Base Color"].default_value = base
        principled.inputs["Roughness"].default_value = 0.30
        if "Specular IOR Level" in principled.inputs:
            principled.inputs["Specular IOR Level"].default_value = 0.52
        return material

    tex_coord = nodes.new("ShaderNodeTexCoord")
    tex_coord.location = (-800, 0)
    # 亮片是细密颗粒：坐标本身是米制，scale 必须很大才出得来"闪片"而不是"砂纸"。
    glitter = nodes.new("ShaderNodeTexNoise")
    glitter.location = (-600, 0)
    glitter.inputs["Scale"].default_value = 3200.0
    glitter.inputs["Detail"].default_value = 2.0
    glitter.inputs["Roughness"].default_value = 0.6
    links.new(tex_coord.outputs["Object"], glitter.inputs["Vector"])

    ramp = nodes.new("ShaderNodeValToRGB")
    ramp.location = (-380, 0)
    # 亮片只让明度小幅起伏：幅度太大整体会被拉暗，渲出来和取样色完全对不上
    # （实测把 #81B4E9 的浅蓝牌体渲成近黑蓝）。
    dark = tuple(min(1.0, channel * 0.78) for channel in base[:3]) + (1.0,)
    light = tuple(min(1.0, channel * 1.18) for channel in base[:3]) + (1.0,)
    ramp.color_ramp.elements[0].position = 0.42
    ramp.color_ramp.elements[0].color = dark
    ramp.color_ramp.elements[1].position = 0.62
    ramp.color_ramp.elements[1].color = light
    links.new(glitter.outputs["Fac"], ramp.inputs["Fac"])
    links.new(ramp.outputs["Color"], principled.inputs["Base Color"])

    # 彩色亚克力牌体是「透亮塑料 + 内嵌亮片」，不是金属：金属度给高会整块发黑。
    principled.inputs["Metallic"].default_value = 0.18
    principled.inputs["Roughness"].default_value = 0.32
    if "Coat Weight" in principled.inputs:
        principled.inputs["Coat Weight"].default_value = 0.35
        principled.inputs["Coat Roughness"].default_value = 0.10
    if "Specular IOR Level" in principled.inputs:
        principled.inputs["Specular IOR Level"].default_value = 0.60

    bump = nodes.new("ShaderNodeBump")
    bump.location = (200, -160)
    bump.inputs["Strength"].default_value = 0.18
    bump.inputs["Distance"].default_value = 0.00003
    links.new(glitter.outputs["Fac"], bump.inputs["Height"])
    links.new(bump.outputs["Normal"], principled.inputs["Normal"])
    return material


def make_face_side_material(color: str = "#F1EFE8") -> bpy.types.Material:
    """牌面层的侧壁：比背层亮，用来和金色亮片背层形成可见分界。"""

    material = bpy.data.materials.new("TileFaceLayer")
    material.use_nodes = True
    principled = material.node_tree.nodes.get("Principled BSDF")
    principled.inputs["Base Color"].default_value = _hex_to_linear(color, (0.945, 0.940, 0.925))
    principled.inputs["Roughness"].default_value = 0.22
    if "Specular IOR Level" in principled.inputs:
        principled.inputs["Specular IOR Level"].default_value = 0.55
    return material


def _aim_at(obj, target) -> None:
    from mathutils import Vector

    direction = Vector(target) - obj.location
    obj.rotation_euler = direction.to_track_quat("-Z", "Y").to_euler()


def make_studio(extent: float, transparent: bool) -> None:
    scene = bpy.context.scene
    world = bpy.data.worlds.new("StudioWorld")
    world.use_nodes = True
    background = world.node_tree.nodes["Background"]
    background.inputs["Color"].default_value = (0.62, 0.62, 0.62, 1.0)
    background.inputs["Strength"].default_value = 0.32
    scene.world = world
    if transparent:
        return

    # 台面：中性灰，只负责接住接触阴影，不抢主体。
    bpy.ops.mesh.primitive_plane_add(size=extent * 24.0, location=(0.0, 0.0, 0.0))
    floor = bpy.context.active_object
    floor.name = "StudioFloor"
    floor_material = bpy.data.materials.new("StudioFloor")
    floor_material.use_nodes = True
    floor_bsdf = floor_material.node_tree.nodes.get("Principled BSDF")
    floor_bsdf.inputs["Base Color"].default_value = (0.22, 0.215, 0.21, 1.0)
    floor_bsdf.inputs["Roughness"].default_value = 0.55
    floor.data.materials.append(floor_material)

    # 背板：竖向渐变，避免背景是一块死灰。
    bpy.ops.mesh.primitive_plane_add(
        size=extent * 60.0,
        location=(0.0, extent * 5.0, extent * 3.0),
        rotation=(math.radians(90.0), 0.0, 0.0),
    )
    backdrop = bpy.context.active_object
    backdrop.name = "StudioBackdrop"
    backdrop_material = bpy.data.materials.new("StudioBackdrop")
    backdrop_material.use_nodes = True
    bnodes = backdrop_material.node_tree.nodes
    blinks = backdrop_material.node_tree.links
    bsdf = bnodes.get("Principled BSDF")
    bsdf.inputs["Roughness"].default_value = 0.95
    tex = bnodes.new("ShaderNodeTexCoord")
    tex.location = (-800, 0)
    sep = bnodes.new("ShaderNodeSeparateXYZ")
    sep.location = (-600, 0)
    blinks.new(tex.outputs["Generated"], sep.inputs["Vector"])
    ramp = bnodes.new("ShaderNodeValToRGB")
    ramp.location = (-380, 0)
    ramp.color_ramp.elements[0].position = 0.10
    ramp.color_ramp.elements[0].color = (0.30, 0.295, 0.285, 1.0)
    ramp.color_ramp.elements[1].position = 0.85
    ramp.color_ramp.elements[1].color = (0.03, 0.03, 0.03, 1.0)
    blinks.new(sep.outputs["Z"], ramp.inputs["Fac"])
    blinks.new(ramp.outputs["Color"], bsdf.inputs["Base Color"])
    backdrop.data.materials.append(backdrop_material)


def add_lights(extent: float) -> None:
    """灯光按米制布置。

    主光用太阳光（强度与距离无关，最稳），另加一块大面积柔光板负责
    「能在牌面和倒角上看到形状」的高光——只有太阳光的话，平面牌面会被照成
    一片死白，读不出亚克力的光泽。
    """
    sun_specs = (
        # (name, 方向（从目标指向灯）, strength W/m², angle 度, color)
        ("Key", (-0.55, -0.75, 1.0), 2.0, 12.0, (1.0, 0.96, 0.90)),
        ("Fill", (0.9, -0.6, 0.35), 0.5, 30.0, (0.90, 0.94, 1.0)),
    )
    for name, direction, strength, angle, color in sun_specs:
        data = bpy.data.lights.new(name, type="SUN")
        data.energy = strength
        data.angle = math.radians(angle)
        data.color = color
        obj = bpy.data.objects.new(name, data)
        obj.location = tuple(component * extent for component in direction)
        bpy.context.scene.collection.objects.link(obj)
        _aim_at(obj, (0.0, 0.0, 0.0))

    # 左前上方的大柔光板：它的倒影就是牌面上那条长高光。
    # 米制场景里 1W 已经很亮（10cm 距离下的辐照度是 几十 W/m²），别按"棚灯"的直觉给值。
    softbox = bpy.data.lights.new("Softbox", type="AREA")
    softbox.shape = "RECTANGLE"
    softbox.size = 3.0 * extent
    softbox.size_y = 4.0 * extent
    softbox.energy = 1.0
    softbox.color = (1.0, 0.98, 0.95)
    box_obj = bpy.data.objects.new("Softbox", softbox)
    box_obj.location = (-4.2 * extent, -3.6 * extent, 4.6 * extent)
    bpy.context.scene.collection.objects.link(box_obj)
    _aim_at(box_obj, (0.0, 0.0, 0.0))

    # 侧后细长面光：在倒角/侧壁上拉一条高光，读出亚克力的厚度。
    strip = bpy.data.lights.new("Edge", type="AREA")
    strip.shape = "RECTANGLE"
    strip.size = 0.5 * extent
    strip.size_y = 3.0 * extent
    strip.energy = 0.08
    strip.color = (1.0, 0.99, 0.97)
    strip_obj = bpy.data.objects.new("Edge", strip)
    strip_obj.location = (2.6 * extent, 1.4 * extent, 1.8 * extent)
    bpy.context.scene.collection.objects.link(strip_obj)
    _aim_at(strip_obj, (0.0, 0.0, extent * 0.08))


def add_camera(
    extent: float,
    height: float,
    elevation: float,
    azimuth: float,
    focal: float,
    margin: float,
) -> None:
    data = bpy.data.cameras.new("Camera")
    data.lens = focal
    data.sensor_width = 36.0
    # 场景是米制、被摄体只有几十毫米，默认 clip_start=0.1m 会把牌裁掉。
    data.clip_start = 0.005
    data.clip_end = 10.0
    camera = bpy.data.objects.new("Camera", data)
    bpy.context.scene.collection.objects.link(camera)
    bpy.context.scene.camera = camera

    # 让整撮牌在画面里占约 65%，留出后期排版的安全边距。
    subject = max(extent, 0.030) * margin
    distance = subject * data.lens / data.sensor_width
    elevation_rad = math.radians(elevation)
    azimuth_rad = math.radians(azimuth)
    target_z = height * 0.5
    camera.location = (
        distance * math.cos(elevation_rad) * math.sin(azimuth_rad),
        -distance * math.cos(elevation_rad) * math.cos(azimuth_rad),
        distance * math.sin(elevation_rad) + target_z,
    )
    _aim_at(camera, (0.0, 0.0, target_z))


def configure_render(args: argparse.Namespace) -> None:
    scene = bpy.context.scene
    scene.render.resolution_x = args.res
    scene.render.resolution_y = args.res
    scene.render.resolution_percentage = 100
    scene.render.image_settings.file_format = "PNG"
    scene.render.image_settings.color_mode = "RGBA" if args.transparent else "RGB"
    scene.render.film_transparent = bool(args.transparent)
    scene.render.filepath = str(Path(args.out))
    scene.render.image_settings.compression = 15

    if args.engine == "cycles":
        scene.render.engine = "CYCLES"
        scene.cycles.samples = args.samples
        scene.cycles.use_denoising = True
        scene.cycles.device = "CPU"
    else:
        for engine in ("BLENDER_EEVEE_NEXT", "BLENDER_EEVEE"):
            try:
                scene.render.engine = engine
                break
            except TypeError:
                continue
        eevee = scene.eevee
        if hasattr(eevee, "taa_render_samples"):
            eevee.taa_render_samples = args.samples
        for attr, value in (("use_raytracing", True), ("use_shadows", True)):
            if hasattr(eevee, attr):
                try:
                    setattr(eevee, attr, value)
                except (AttributeError, TypeError):
                    pass

    scene.view_settings.view_transform = "Standard"
    scene.view_settings.look = "None"
    scene.view_settings.exposure = -0.12
    scene.unit_settings.system = "METRIC"
    scene.unit_settings.length_unit = "MILLIMETERS"
    scene.unit_settings.scale_length = 1.0


def main() -> None:
    args = parse_args()
    started = time.time()
    # 牌型参数文件优先：尺寸、倒角、分层、配色、图案占位都从它来。
    apply_params(args)
    resolve_finish(args)
    clear_scene()

    # 命令行单位是毫米，场景内部统一换成米：Blender 的灯光功率是按米制算的，
    # 直接拿毫米当场景单位，牌会变成 42 米见方，灯光全部失效（只剩环境光）。
    length_mm, width_mm, thickness_mm = args.length, args.width, args.thickness
    length = length_mm * _MM
    width = width_mm * _MM
    thickness = thickness_mm * _MM
    bevel = args.bevel * _MM

    patterns = pattern_paths(args)
    placements = build_layout(
        args.layout, len(patterns), width=width_mm, length=length_mm, gap=args.gap
    )
    materials: dict[str, bpy.types.Material] = {}
    # 分层：参数里给几层就建几条色带（面层 + 一层或多层背层/夹层条纹）。
    bands = resolve_bands(args, thickness)
    band_materials: dict[tuple[str, str, str], bpy.types.Material] = {}
    band_side: list[bpy.types.Material] = []
    for _, _, layer in bands:
        name = str(layer.get("name") or "body")
        color = str(layer.get("color") or (args.face_color if name == "face" else args.body_color))
        finish = str(layer.get("finish") or ("gloss" if name == "face" else args.body_finish))
        key = (name, color, finish)
        material = band_materials.get(key)
        if material is None:
            material = (
                make_face_side_material(color) if name == "face" else make_body_material(finish, color)
            )
            band_materials[key] = material
        band_side.append(material)

    for index, (pattern, placement) in enumerate(zip(patterns, placements)):
        image = bpy.data.images.load(str(Path(pattern)))
        rect_w, rect_h = pattern_rect(
            image, length=length, width=width, cover=args.pattern_cover
        )
        tile = make_tile(length, width, thickness, bevel=bevel)
        tile.name = f"MahjongTile{index}"
        assign_face_uvs(tile, rect_w=rect_w, rect_h=rect_h, shift=args.pattern_shift * _MM)
        material = materials.get(pattern)
        if material is None:
            material = make_tile_material(Path(pattern), base_color=args.face_color)
            materials[pattern] = material
        # 槽 0 = 牌面（底色 + 图案），槽 1..N = 从牌面到牌底的各层侧壁。
        tile.data.materials.append(material)
        for side in band_side:
            tile.data.materials.append(side)
        bottom_slot = len(band_side)
        for polygon in tile.data.polygons:
            if polygon.normal.z > 0.99:
                polygon.material_index = 0
                continue
            if polygon.normal.z < -0.99:
                polygon.material_index = bottom_slot
                continue
            z = polygon.center.z
            slot = bottom_slot
            for position, (low, high, _) in enumerate(bands):
                if low - 1e-9 <= z <= high + 1e-9:
                    slot = position + 1
                    break
            polygon.material_index = slot
        tile.location = (placement.x * _MM, placement.y * _MM, thickness / 2.0)
        tile.rotation_euler = (0.0, 0.0, math.radians(placement.rotation))

    span_x, span_y = cluster_extent(placements, width=width_mm, length=length_mm)
    frame_w, frame_h = projected_size(
        span_x,
        span_y,
        thickness_mm,
        elevation=args.elevation,
        azimuth=args.azimuth,
    )
    extent = max(frame_w, frame_h, length_mm) * _MM

    make_studio(extent, args.transparent)
    add_lights(extent)
    add_camera(extent, thickness, args.elevation, args.azimuth, args.focal, args.margin)
    configure_render(args)

    bpy.context.view_layer.update()
    render_started = time.time()
    bpy.ops.render.render(write_still=True)
    render_elapsed = time.time() - render_started
    print(
        f"[render_tile] engine={bpy.context.scene.render.engine} out={args.out} "
        f"layout={args.layout} tiles={len(patterns)} span={span_x:.0f}x{span_y:.0f}mm "
        f"render={render_elapsed:.2f}s total={time.time() - started:.2f}s"
    )


if __name__ == "__main__":
    main()
