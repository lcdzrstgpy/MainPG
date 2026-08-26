# -*- coding: utf-8 -*-
"""发布 1.3.2（已 EV 签名版）到正式远程更新区 + 同步内测区：
1) 用签名后 exe 的实际 SHA 重新生成签名 release-manifest.json（Ed25519）
2) 上传 MainPG-Setup-1.3.2.exe + manifest.json 到 /mainpg/windows/
3) 更新内测区 MainPG-Internal-Setup.exe（备份）
4) SHA256 + HTTP 全链路校验
密码从环境变量 DEPLOY_PASSWORD 读取。"""
import datetime
import hashlib
import os
import paramiko

from pathlib import Path

import sys
sys.path.insert(0, r"e:\MainPG\MainPG\local-runtime")
from wh_local.runtime.release_manifest import (  # noqa: E402
    canonical_manifest_bytes,
    create_signed_manifest,
    load_private_key,
)

HOST = "45.197.150.50"
PASSWORD = os.environ.get("DEPLOY_PASSWORD", "")
KEY = Path(r"C:\secure\mainpg-release-ed25519.pem")
MAINPG_DIR = "/var/www/html/mainpg/windows"
LOCAL_EXE = Path(r"e:\MainPG\MainPG\local-runtime\dist\MainPG-Setup-1.3.2.exe")
LOCAL_MANIFEST = Path(r"e:\MainPG\MainPG\local-runtime\dist\release-manifest.json")
REMOTE_EXE = f"{MAINPG_DIR}/MainPG-Setup-1.3.2.exe"
REMOTE_MANIFEST = f"{MAINPG_DIR}/manifest.json"
REMOTE_INTERNAL = "/var/www/html/internal-downloads/MainPG-Internal-Setup.exe"
TS = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")

VERSION = "1.3.2"
INSTALLER_URL = f"https://workbench.haocoming.top/mainpg/windows/MainPG-Setup-{VERSION}.exe"
RELEASE_NOTES = "merge: syq-lirun-0825 利润活动商品工作流 + POD定制/引用图直连参考+双画布单位/店铺采集/价格验证/主题 + 失败诊断增强 + 系统版本管理面板"

assert LOCAL_EXE.is_file(), "missing 1.3.2 installer"

# 1. 生成签名 manifest（用签名后 exe 的实际 SHA）
sha = hashlib.sha256()
with LOCAL_EXE.open("rb") as f:
    for chunk in iter(lambda: f.read(1 << 20), b""):
        sha.update(chunk)
EXE_SHA = sha.hexdigest()
manifest = create_signed_manifest(
    version=VERSION,
    mandatory=False,
    installer_url=INSTALLER_URL,
    sha256=EXE_SHA,
    release_notes=RELEASE_NOTES,
    published_at=datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    private_key=load_private_key(KEY),
)
LOCAL_MANIFEST.write_bytes(canonical_manifest_bytes(manifest) + b"\n")
print("== 1. signed manifest ==")
print("  exe sha256:", EXE_SHA)
print("  version  :", manifest["version"])
print("  manifest :", LOCAL_MANIFEST)

client = paramiko.SSHClient()
client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
client.connect(HOST, username="root", password=PASSWORD, timeout=15)


def run(cmd, timeout=120):
    _, stdout, stderr = client.exec_command(cmd, timeout=timeout)
    return (stdout.read().decode("utf-8", "replace") + stderr.read().decode("utf-8", "replace")).strip()


print("== 2. 建目录 + 备份现有 manifest ==")
print(run(f"mkdir -p {MAINPG_DIR} && echo DIR_OK"))
print(run(f"cp -p {REMOTE_MANIFEST} {REMOTE_MANIFEST}.bak-{TS} && echo BACKUP_OK"))

print("== 3. 上传正式区 exe + manifest ==")
sftp = client.open_sftp()
sftp.put(str(LOCAL_EXE), REMOTE_EXE)
sftp.put(str(LOCAL_MANIFEST), REMOTE_MANIFEST)
sftp.close()
print("  uploaded exe + manifest")

print("== 4. 更新内测区 exe ==")
print(run(f"cp -p {REMOTE_INTERNAL} {REMOTE_INTERNAL}.backup-{TS} && echo BACKUP_OK"))
sftp = client.open_sftp()
sftp.put(str(LOCAL_EXE), REMOTE_INTERNAL)
sftp.close()
print("  uploaded internal installer")

print("== 5. SHA256 校验 ==")
print("local exe :", EXE_SHA)
print("remote exe:", run(f"sha256sum {REMOTE_EXE} {REMOTE_INTERNAL}"))

print("== 6. HTTP 校验 ==")
for path, host in (
    ("/mainpg/windows/MainPG-Setup-1.3.2.exe", "workbench.haocoming.top"),
    ("/mainpg/windows/manifest.json", "workbench.haocoming.top"),
    ("/internal-downloads/MainPG-Internal-Setup.exe", "workbench.haocoming.top"),
):
    print(path, "->", run(f"curl -sk -o /dev/null -w '%{{http_code}} %{{size_download}}' https://127.0.0.1{path} -H 'Host: {host}'"))

print("== 7. 远端 manifest 回读 ==")
print(run(f"cat {REMOTE_MANIFEST}"))
client.close()
print("DONE")
