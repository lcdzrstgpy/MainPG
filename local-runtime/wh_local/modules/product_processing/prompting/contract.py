"""唯一一份核心契约，八条，短。"""

from __future__ import annotations

CONTRACT_ZH = """硬契约（优先级最高，任何其它段落不得覆盖）：
1. 商品 100% 忠于参考图：形状、比例、颜色、材质、数量和印刷内容一致；每格可见件数必须与参考图相同，不得增删。
2. 物理支撑：能自立才立；软质必须自然垂落/盘绕/平铺；液体必须在容器内；任何商品不得悬空。
3. 尺寸：严格按逐格计划给的占比；小件必须与比例参照同框，且不得大于参照物。
4. 构图：商品完整入画，不压边、不压分界线；四格是四个不同画面，第①②格不得同机位。
5. 材质与光：真实反射/折射/接触阴影，跟随该格光向；不得塑料感、不得过曝。
6. 零添加文字：画面中不得出现任何字幕、标题、标签、水印、箭头、标尺或界面元素；只有参考图里真实印在商品上的文字才允许出现。
7. 人物：只有逐格计划明确写了手/躯干/腿的格子才可以出现；其余格子必须无人；永不露脸；头饰类只展示产品。
8. 道具只做配角：最多 3 件，放在商品后方/两侧并轻微虚化；不得遮挡、压盖或抢焦点；道具表面不得有任何文字、logo 或印花；道具必须按真实方式使用（例如杯垫一次只用一只，杯子不得压在一摞杯垫上）。
9. 只输出 2048×2048 的四宫格，分界线连续、笔直，不渲染坐标或编号。"""

CONTRACT_EN = """HARD CONTRACT (highest priority; no other section may override):
1. Product fidelity: shape, proportion, colour, material, quantity and printed content must match the reference 100%; the visible number of pieces must match in every panel - never add or remove.
2. Physical support: stand only if it can stand; soft goods must drape, coil or lie flat; liquids stay in a container; nothing floats.
3. Scale: follow the panel plan fill exactly; a small item must share the frame with its scale anchor and never look larger than it.
4. Composition: complete product, no edge or divider contact; four panels are four different photographs and Panels 1-2 must not share a camera position.
5. Material and light: real reflection, refraction and contact shadow, following that panel's light; no plastic look, no blown highlights.
6. Zero added text: no caption, title, label, watermark, arrow, ruler or UI anywhere; the only words allowed are those physically printed on the product in the reference.
7. People: a hand, torso or legs may appear ONLY in a panel whose plan line asks for it; every other panel stays product-only; never a face; headwear is shown as a product only.
8. Props are supporting only: at most three, behind or beside the product and softly out of focus; never occlude, cover or out-shout it; props must be plain with no text, logo or print, and must be used the way they are really used (for example one coaster under a mug - never a mug on top of a stack).
9. Output exactly one 2048x2048 2x2 grid with continuous straight dividers and no coordinates or panel numbers."""


def contract(language: str) -> str:
    return CONTRACT_ZH if str(language).lower().startswith("zh") else CONTRACT_EN
