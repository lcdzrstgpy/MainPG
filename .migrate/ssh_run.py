#!/usr/bin/env python3
"""Quick remote helper: run a command on a server via paramiko (password auth)."""
import sys

import paramiko

HOST, USER, PASSWORD = sys.argv[1], sys.argv[2], sys.argv[3]
CMD = sys.argv[4]

client = paramiko.SSHClient()
client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
client.connect(HOST, username=USER, password=PASSWORD, port=22, timeout=20)
_, stdout, stderr = client.exec_command(CMD, timeout=300, get_pty=True)
out = stdout.read().decode("utf-8", "replace")
err = stderr.read().decode("utf-8", "replace")
print(out, end="")
if err.strip():
    print("--STDERR--", file=sys.stderr)
    print(err, end="", file=sys.stderr)
client.close()
