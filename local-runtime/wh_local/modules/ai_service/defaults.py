from __future__ import annotations

from typing import Any


DEFAULT_MODELS: tuple[dict[str, Any], ...] = (
    {
        "id": "deepseek-v4-flash",
        "name": "DeepSeek V4 Flash",
        "modes": ["chat"],
        "reference_transport": "none",
        "sizes": [],
        "default_count": 1,
    },
    {
        "id": "deepseek-v4-pro",
        "name": "DeepSeek V4 Pro",
        "modes": ["chat"],
        "reference_transport": "none",
        "sizes": [],
        "default_count": 1,
    },
    {
        "id": "gpt-5.6-terra",
        "name": "GPT-5.6 Terra",
        "modes": ["chat"],
        "reference_transport": "none",
        "sizes": [],
        "default_count": 1,
    },
    {
        "id": "gpt-image-2-1k",
        "name": "商品创作 Pro",
        "modes": ["generate", "edit"],
        "reference_transport": "data_url",
        "sizes": ["1024x1024", "1024x1536", "1536x1024"],
        "default_count": 1,
    },
    {
        "id": "gpt-image-2-2k",
        "name": "GPT Image 2 · 2K",
        "modes": ["generate", "edit"],
        "reference_transport": "data_url",
        "sizes": ["1024x1024", "1024x1536", "1536x1024"],
        "default_count": 1,
    },
    {
        "id": "gpt-image-2-4k",
        "name": "GPT Image 2 · 4K",
        "modes": ["generate", "edit"],
        "reference_transport": "data_url",
        "sizes": ["1024x1024", "1024x1536", "1536x1024"],
        "default_count": 1,
    },
)


DEFAULT_TEMPLATES: tuple[dict[str, Any], ...] = (
    {
        "id": "white-background",
        "label": "商品白底图",
        "description": "统一主图、干净电商质感",
        "mode": "edit",
        "default_count": 1,
        "prompt": "保留商品主体、角度与材质，将背景替换为纯白电商主图背景，柔和均匀棚拍光线，无文字、无多余道具。",
    },
    {
        "id": "scene",
        "label": "场景图",
        "description": "为商品生成可售卖的使用场景",
        "mode": "edit",
        "default_count": 4,
        "prompt": "保持商品主体完整、比例准确，生成自然高级的家居使用场景，画面突出商品主体，柔和日光，干净克制，无文字。",
    },
    {
        "id": "background",
        "label": "一键换背景",
        "description": "不改变商品，快速替换背景",
        "mode": "edit",
        "default_count": 1,
        "prompt": "严格保留上传商品图中的主体、颜色、图案和结构，只替换背景为浅米色渐变影棚，添加自然柔和投影，不添加文字或新商品。",
    },
    {
        "id": "poster",
        "label": "卖点海报",
        "description": "根据商品生成活动视觉方向",
        "mode": "generate",
        "default_count": 4,
        "prompt": "生成一张跨境电商商品卖点海报：干净留白构图，突出产品品质与使用价值，现代高级配色，预留文字区域，不直接生成可读文字。",
    },
)
