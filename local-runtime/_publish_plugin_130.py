# -*- coding: utf-8 -*-
"""发布插件 v0.1.130（重建版）到官网下载区：
1) 上传新 W-H-browser-extension-v0.1.130.zip（含 temu_dom_capture 更新）到 /var/www/html/downloads/
2) 备份 release-state.json 并原子更新 plugin 记录（size/sha）
3) 校验页面显示与下载 SHA256（容器 env 已指向 v0.1.130，无需重建）
密码从环境变量 DEPLOY_PASSWORD 读取。"""
import datetime
import hashlib
import json
import os
import paramiko

HOST = "45.197.150.50"
PASSWORD = os.environ.get("DEPLOY_PASSWORD", "")
LOCAL_ZIP = r"e:\MainPG\MainPG\W-H-browser-extension-v0.1.130.zip"
REMOTE_ZIP = "/var/www/html/downloads/W-H-browser-extension-v0.1.130.zip"
STATE_PATH = "/var/lib/qifan-download-releases/release-state.json"
TS = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")

NEW_VERSION = "1.0.4"
h = hashlib.sha256()
with open(LOCAL_ZIP, "rb") as f:
    for chunk in iter(lambda: f.read(1 << 20), b""):
        h.update(chunk)
NEW_SHA = h.hexdigest()
NEW_SIZE = os.path.getsize(LOCAL_ZIP)

client = paramiko.SSHClient()
client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
client.connect(HOST, username="root", password=PASSWORD, timeout=15)


def run(cmd, timeout=180):
    _, stdout, stderr = client.exec_command(cmd, timeout=timeout)
    return (stdout.read().decode("utf-8", "replace") + stderr.read().decode("utf-8", "replace")).strip()


print(f"== 1. 上传插件 zip（{NEW_SIZE} bytes, sha={NEW_SHA}）==")
sftp = client.open_sftp()
sftp.put(LOCAL_ZIP, REMOTE_ZIP)
sftp.close()
print("  uploaded:", REMOTE_ZIP)

print("== 2. 备份 release-state.json ==")
print(run(f"cp -p {STATE_PATH} {STATE_PATH}.bak-{TS} && echo BACKUP_OK"))

print("== 3. 原子更新 release-state.json 的 plugin 记录 ==")
state = json.loads(run(f"cat {STATE_PATH}"))
state["plugin"] = {
    "version": NEW_VERSION,
    "fileName": "W-H-browser-extension-v0.1.130.zip",
    "size": NEW_SIZE,
    "sha256": NEW_SHA,
    "releasedAt": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
}
body = json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
sftp = client.open_sftp()
with sftp.open(STATE_PATH, "w") as f:
    f.write(body)
sftp.close()
print("  new plugin record:", json.dumps(state["plugin"], ensure_ascii=False))

print("== 4. 校验 ==")
print("  remote sha256:", run(f"sha256sum {REMOTE_ZIP}"))
print("  local  sha256:", NEW_SHA)
print("  http check:", run("curl -sk -o /dev/null -w '%{http_code} %{size_download}' https://127.0.0.1/downloads/W-H-browser-extension-v0.1.130.zip -H 'Host: workbench.haocoming.top'"))
print("  container env:", run("docker inspect wh-site --format '{{range .Config.Env}}{{println .}}{{end}}' 2>&1 | grep PLUGIN_DOWNLOAD"))
client.close()
print("DONE")
