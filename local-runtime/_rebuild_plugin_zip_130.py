# -*- coding: utf-8 -*-
"""重建插件 zip v0.1.130 为顶层文件夹结构（Chrome/Edge 拖拽加载兼容）。
zip 内：W-H-browser-extension-v0.1.130/ 目录 + 全部插件文件。"""
import hashlib
import os
import zipfile

BASE = r"e:\MainPG\MainPG\W-H-browser-extension-v0.1.130"
ZIP = r"e:\MainPG\MainPG\W-H-browser-extension-v0.1.130.zip"
FOLDER = "W-H-browser-extension-v0.1.130"

NAMES = [
    "background.js",
    "content.js",
    "manifest.json",
    "network_probe_utils.js",
    "onebound_page_capture.js",
    "page_probe.js",
    "popup.html",
    "popup.js",
    "temu_dom_capture.js",
    "tenant_context.js",
    "安装说明.txt",
]


def sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


# 重新生成 SHA256SUMS.txt（含自引用，顺序与内容文件一致）
lines = []
for name in NAMES:
    p = os.path.join(BASE, name)
    lines.append(f"{sha256(p)}  {name}")
body = ("\r\n".join(lines) + "\r\n").encode("utf-8")
self_hash = hashlib.sha256(body).hexdigest()
lines.append(f"{self_hash}  SHA256SUMS.txt")
content = "\r\n".join(lines) + "\r\n"
with open(os.path.join(BASE, "SHA256SUMS.txt"), "w", encoding="utf-8", newline="") as f:
    f.write(content)

# 重建 zip：全部文件置于顶层文件夹下
if os.path.exists(ZIP):
    os.remove(ZIP)
with zipfile.ZipFile(ZIP, "w", zipfile.ZIP_DEFLATED) as zf:
    for name in NAMES + ["SHA256SUMS.txt"]:
        p = os.path.join(BASE, name)
        zf.write(p, f"{FOLDER}/{name}")
print("zip written:", ZIP, os.path.getsize(ZIP), "bytes")

# 验证结构
with zipfile.ZipFile(ZIP) as zf:
    names = zf.namelist()
print("entries:", names)
print("zip sha256:", sha256(ZIP))
