# -*- coding: utf-8 -*-
"""查看官网正式远程更新区 /mainpg/windows/ 现状（密码认证）。"""
import os
import paramiko

HOST = "45.197.150.50"
PASSWORD = os.environ.get("DEPLOY_PASSWORD", "")

client = paramiko.SSHClient()
client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
client.connect(HOST, username="root", password=PASSWORD, timeout=15)


def run(cmd, timeout=60):
    _, stdout, stderr = client.exec_command(cmd, timeout=timeout)
    return (stdout.read().decode("utf-8", "replace") + stderr.read().decode("utf-8", "replace")).strip()


print("== /mainpg/windows 顶层 ==")
print(run("ls -la /var/www/html/mainpg/windows/ 2>&1 | head -40"))
print("== manifest.json ==")
print(run("cat /var/www/html/mainpg/windows/manifest.json 2>&1"))
print("== patch-manifest.json ==")
print(run("cat /var/www/html/mainpg/windows/patch-manifest.json 2>&1"))
print("== patch 目录 ==")
print(run("ls -la /var/www/html/mainpg/windows/patch/ 2>&1 | head -20"))
print("== internal-downloads 内测区 ==")
print(run("ls -la /var/www/html/internal-downloads/ 2>&1 | head -20"))
client.close()
