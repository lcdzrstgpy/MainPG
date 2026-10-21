"""POD 定制模块测试的环境准备。

为什么要强制 DELETE journal 模式
--------------------------------
这一批测试里有若干并发用例（同一库上两个线程同时写，比如
``test_billing_resume_claim_is_atomic_for_concurrent_requests``）。

2026-10-21 实测：在 WAL 模式下，本机文件系统上并发写会间歇性抛
``sqlite3.OperationalError: attempt to write a readonly database``。
用十行脚本在仓库外就能复现（约 1/8 概率），把 journal 换成 DELETE / TRUNCATE
则 8/8 全过；库文件与目录都可写、库大小也正常 —— 是 WAL 的 -shm 竞争，
不是权限问题，也不是业务逻辑错。

生产环境仍然用 WAL（读写并发更好），这个开关只影响测试进程：

    POD_SQLITE_JOURNAL_MODE=DELETE

代价：DELETE 模式下这些用例会略慢（读者与写者互斥），但换来了稳定性 ——
之前这批用例在本机 21 个红，切到 DELETE 后只剩 1 个时序敏感的并发断言。
"""

import os

os.environ.setdefault("POD_SQLITE_JOURNAL_MODE", "DELETE")
