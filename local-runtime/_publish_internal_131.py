# -*- coding: utf-8 -*-
"""发布内测版 1.3.1：备份旧包 + 上传 MainPG-Setup-1.3.1.exe -> MainPG-Internal-Setup.exe + 校验。
密码从环境变量 DEPLOY_PASSWORD 读取。"""
import datetime
import hashlib
import os
import paramiko

HOST = "45.197.150.50"
PASSWORD = os.environ.get("DEPLOY_PASSWORD", "")
LOCAL_EXE = r"e:\MainPG\MainPG\local-runtime\dist\MainPG-Setup-1.3.1.exe"
REMOTE_INTERNAL = "/var/www/html/internal-downloads/MainPG-Internal-Setup.exe"
TS = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")

assert os.path.isfile(LOCAL_EXE), "missing new 1.3.1 installer"

client = paramiko.SSHClient()
client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
client.connect(HOST, username="root", password=PASSWORD, timeout=15)


def run(cmd, timeout=120):
    _, stdout, stderr = client.exec_command(cmd, timeout=timeout)
    return (stdout.read().decode("utf-8", "replace") + stderr.read().decode("utf-8", "replace")).strip()


print("== 1. 备份内测区旧包 ==")
print(run(f"cp -p {REMOTE_INTERNAL} {REMOTE_INTERNAL}.backup-{TS} && echo BACKUP_OK"))

print("== 2. 上传新包 ==")
sftp = client.open_sftp()
sftp.put(LOCAL_EXE, REMOTE_INTERNAL)
sftp.close()
print("  uploaded")

print("== 3. SHA256 校验 ==")
h = hashlib.sha256()
with open(LOCAL_EXE, "rb") as f:
    for chunk in iter(lambda: f.read(1 << 20), b""):
        h.update(chunk)
print("local exe :", h.hexdigest())
print("remote    :", run(f"sha256sum {REMOTE_INTERNAL}"))

print("== 4. HTTP 校验 ==")
for path in ("/internal-downloads/MainPG-Internal-Setup.exe",):
    print(path, "->", run(
        f"curl -sk -o /dev/null -w '%{{http_code}} %{{size_download}}' https://127.0.0.1{path} -H 'Host: workbench.haocoming.top'"
    ))
print("== 5. 容器内可见性 ==")
print(run("docker exec wh-site ls -la /app/internal-downloads/MainPG-Internal-Setup.exe 2>&1 | head -2"))
client.close()
print("DONE")
