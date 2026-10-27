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

为什么用 fixture 而不是模块级 setenv
------------------------------------
模块级 ``os.environ.setdefault`` 会在**收集阶段**就把变量写进整个进程，泄漏给
同一进程里其他目录的测试。2026-10-22 全量跑时它就踩了坑：``test_app_update``
首次 import ``wh_local.app.main`` 触发模块级 ``app = create_app()``，拿着 DELETE
去切**用户真实库**的模式，撞上残留连接直接 ``database is locked``，连带 3 个用例
报红（单跑不会）。

改成 autouse fixture + monkeypatch：每个用例开始前设、结束后自动还原，既不泄漏，
也保住了并发用例需要的 DELETE。

代价：DELETE 模式下这些用例会略慢（读者与写者互斥），但换来了稳定性 ——
之前这批用例在本机 21 个红，切到 DELETE 后只剩 1 个时序敏感的并发断言。
"""

import pytest


@pytest.fixture(autouse=True)
def _delete_journal_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("POD_SQLITE_JOURNAL_MODE", "DELETE")
