#!/usr/bin/env python3
"""Extract per-theme CSS blocks from frontend stylesheets into backend theme packages."""
from __future__ import annotations

import re
import shutil
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SRC_STYLES = REPO / "web-frontend" / "src" / "shared" / "styles"
OUT_DIR = REPO / "local-runtime" / "wh_local" / "data" / "themes"

# Themes we want to move from frontend bundle to backend downloadable packages.
DOWNLOADABLE = ["violet", "dessert", "diamond", "quirky", "chinese"]


def split_top_level_blocks(css: str) -> list[tuple[str, str]]:
    """Split CSS into top-level blocks preserving braces.

    Returns list of (type, content) where type is one of:
      'rule'        - a normal selector rule
      'keyframes'   - @keyframes block
      'media'       - @media block
      'font-face'   - @font-face block
      'other-at'    - other at-rules
      'comment'     - top-level comment
      'raw'         - leftover whitespace/unknown
    """
    blocks: list[tuple[str, str]] = []
    i = 0
    n = len(css)

    def skip_ws() -> None:
        nonlocal i
        while i < n and css[i].isspace():
            i += 1

    while i < n:
        skip_ws()
        if i >= n:
            break

        start = i

        if css.startswith("/*", i):
            end = css.find("*/", i + 2)
            if end == -1:
                blocks.append(("comment", css[i:]))
                break
            i = end + 2
            blocks.append(("comment", css[start:i]))
            continue

        if css[i] == "@":
            # Find the first '{' or ';' ending the prelude
            j = i
            while j < n and css[j] not in "{;":
                if css.startswith("/*", j):
                    j = css.find("*/", j + 2) + 2
                else:
                    j += 1
            if j >= n:
                blocks.append(("raw", css[i:]))
                break
            if css[j] == ";":
                i = j + 1
                blocks.append(("other-at", css[start:i]))
                continue

            # css[j] == '{'
            depth = 1
            k = j + 1
            while k < n and depth > 0:
                if css.startswith("/*", k):
                    k = css.find("*/", k + 2) + 2
                    continue
                if css[k] == "{":
                    depth += 1
                elif css[k] == "}":
                    depth -= 1
                k += 1

            block = css[start:k]
            lowered = block.lower()
            if "@keyframes" in lowered:
                blocks.append(("keyframes", block))
            elif "@media" in lowered:
                blocks.append(("media", block))
            elif "@font-face" in lowered:
                blocks.append(("font-face", block))
            else:
                blocks.append(("other-at", block))
            i = k
            continue

        # Regular rule: read until matching '}'
        if css[i] != "{":
            j = i
            while j < n and css[j] != "{":
                if css.startswith("/*", j):
                    j = css.find("*/", j + 2) + 2
                    continue
                j += 1
            if j >= n:
                blocks.append(("raw", css[i:]))
                break
            # Now read the declaration block
            depth = 1
            k = j + 1
            while k < n and depth > 0:
                if css.startswith("/*", k):
                    k = css.find("*/", k + 2) + 2
                    continue
                if css[k] == "{":
                    depth += 1
                elif css[k] == "}":
                    depth -= 1
                k += 1
            blocks.append(("rule", css[start:k]))
            i = k
            continue

        # Should not reach here; consume one char to avoid infinite loop
        i += 1
        blocks.append(("raw", css[start:i]))

    return blocks


def blocks_for_theme(blocks: list[tuple[str, str]], theme: str) -> list[tuple[str, str]]:
    """Select blocks that mention the given theme id in a data-theme selector."""
    pattern = f'data-theme="{theme}"'
    return [(t, c) for t, c in blocks if pattern in c]


def collect_keyframe_names(css: str) -> set[str]:
    """Find animation-name values inside CSS text."""
    names: set[str] = set()
    # animation: name 1s ...
    for m in re.finditer(r"animation\s*:\s*([\w-]+)", css, re.IGNORECASE):
        names.add(m.group(1))
    # animation-name: name
    for m in re.finditer(r"animation-name\s*:\s*([\w-]+)", css, re.IGNORECASE):
        names.add(m.group(1))
    return names


def keyframes_blocks_for_theme(all_blocks: list[tuple[str, str]], theme_blocks: list[tuple[str, str]]) -> list[tuple[str, str]]:
    """Include @keyframes that are referenced by the theme's rules."""
    needed = set()
    for _, content in theme_blocks:
        needed.update(collect_keyframe_names(content))
    if not needed:
        return []
    result = []
    for t, content in all_blocks:
        if t != "keyframes":
            continue
        m = re.search(r"@keyframes\s+([\w-]+)", content, re.IGNORECASE)
        if m and m.group(1) in needed:
            result.append((t, content))
    return result


def extract() -> dict[str, str]:
    themes_css = (SRC_STYLES / "themes.css").read_text(encoding="utf-8")
    personality_css = (SRC_STYLES / "theme-personality.css").read_text(encoding="utf-8")
    ink_css = (SRC_STYLES / "ink-tap.css").read_text(encoding="utf-8")

    themes_blocks = split_top_level_blocks(themes_css)
    personality_blocks = split_top_level_blocks(personality_css)
    ink_blocks = split_top_level_blocks(ink_css)

    extracted: dict[str, str] = {}
    for theme in DOWNLOADABLE:
        parts: list[tuple[str, str]] = []
        tb = blocks_for_theme(themes_blocks, theme)
        pb = blocks_for_theme(personality_blocks, theme)
        parts.extend(tb)
        parts.extend(pb)
        parts.extend(keyframes_blocks_for_theme(themes_blocks, tb))
        parts.extend(keyframes_blocks_for_theme(personality_blocks, pb))

        # Chinese theme also needs the ink-tap component styles.
        if theme == "chinese":
            parts.extend(ink_blocks)

        # Deduplicate while preserving order.
        seen: set[str] = set()
        unique: list[tuple[str, str]] = []
        for t, c in parts:
            if c in seen:
                continue
            seen.add(c)
            unique.append((t, c))

        out = "\n\n".join(c for _, c in unique)
        extracted[theme] = out

    return extracted


def strip_downloadable_blocks(blocks: list[tuple[str, str]]) -> list[tuple[str, str]]:
    """Remove blocks that belong to any downloadable theme."""
    patterns = [f'data-theme="{t}"' for t in DOWNLOADABLE]
    return [(t, c) for t, c in blocks if not any(p in c for p in patterns)]


def write_stripped_css() -> None:
    """Rewrite frontend stylesheets with downloadable theme blocks removed."""
    themes_css = (SRC_STYLES / "themes.css").read_text(encoding="utf-8")
    personality_css = (SRC_STYLES / "theme-personality.css").read_text(encoding="utf-8")

    themes_blocks = split_top_level_blocks(themes_css)
    personality_blocks = split_top_level_blocks(personality_css)

    stripped_themes = strip_downloadable_blocks(themes_blocks)
    stripped_personality = strip_downloadable_blocks(personality_blocks)

    # Backup originals
    shutil.copy2(SRC_STYLES / "themes.css", SRC_STYLES / "themes.css.bak")
    shutil.copy2(SRC_STYLES / "theme-personality.css", SRC_STYLES / "theme-personality.css.bak")

    (SRC_STYLES / "themes.css").write_text(
        "".join(c for _, c in stripped_themes), encoding="utf-8"
    )
    (SRC_STYLES / "theme-personality.css").write_text(
        "".join(c for _, c in stripped_personality), encoding="utf-8"
    )
    print("Stripped downloadable theme blocks from frontend stylesheets.")


def write_packages(extracted: dict[str, str]) -> None:
    if OUT_DIR.exists():
        shutil.rmtree(OUT_DIR)
    OUT_DIR.mkdir(parents=True)

    swatches = {
        "violet": "linear-gradient(135deg, #f5d8e9 0 38%, #e7c9f4 38% 70%, #d57eae 70%)",
        "dessert": "linear-gradient(135deg, #f3e3cf 0 34%, #b8754e 34% 67%, #bd7b82 67%)",
        "diamond": "linear-gradient(135deg, #050505, #737985 55%, #ffffff)",
        "quirky": "linear-gradient(135deg, #a3e635 0 34%, #fde047 34% 62%, #f43f5e 62% 78%, #7c3aed 78%)",
        "chinese": "linear-gradient(135deg, #eee9dc 0 36%, #52716c 36% 72%, #a74736 72%)",
    }
    labels = {
        "violet": "樱雾粉紫",
        "dessert": "焦糖",
        "diamond": "黑白钻石",
        "quirky": "怪趣贴纸",
        "chinese": "水墨青黛",
    }
    descriptions = {
        "violet": "柔和粉紫渐变，适合长时间办公",
        "dessert": "暖棕焦糖色调，温馨舒适",
        "diamond": "高对比黑钻石风格，简洁锐利",
        "quirky": "多彩贴纸风，活泼有趣",
        "chinese": "水墨青黛意境，点击屏幕有墨韵反馈",
    }

    for theme, css in extracted.items():
        pkg_dir = OUT_DIR / theme
        pkg_dir.mkdir(parents=True)
        (pkg_dir / "theme.css").write_text(css, encoding="utf-8")
        manifest = {
            "id": theme,
            "label": labels[theme],
            "description": descriptions[theme],
            "swatch": swatches[theme],
            "version": "1.0.0",
            "files": ["theme.css"],
        }
        import json
        (pkg_dir / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"Wrote {pkg_dir} ({len(css)} chars)")


if __name__ == "__main__":
    extracted = extract()
    write_packages(extracted)
    write_stripped_css()
    print(f"Done. Packages in {OUT_DIR}")
