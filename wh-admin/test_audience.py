# -*- coding: utf-8 -*-
"""公告分类投放 · 端到端验证（全程用合成库，绝不碰生产数据）。

覆盖：
1. 无 token → 只拿全员公告
2. 有效 token + 伪造的 ?account_id= → 按 token 身份，伪造无效
3. 按套餐分类：basic / experience 各自命中
4. 按邀请码分类：注册时记录的邀请码命中；无记录的账号命中不了（历史未记录语义）
5. 无钱包行账号 → 归入 experience（设计约定）
6. 匿名 + 带分类规则 → 看不到（匿名不等于体验版）
7. 图片接口对不符合分类的 → 404（不泄露"这条存在"）
8. 指定名单 与 分类规则 取交集
9. 邀请码选项接口未鉴权 → 401（顺带确认路由已注册）
"""
import importlib.util
import json
import os
import sqlite3
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
WORK = Path(tempfile.mkdtemp(prefix="announce-test-"))
CN = timezone(timedelta(hours=8))
NOW = datetime.now(CN).isoformat(timespec="seconds")
FUTURE = (datetime.now(CN) + timedelta(days=30)).isoformat(timespec="seconds")

# ---- 1. 合成 customer-auth 库 ----
AUTH_DB = WORK / "customer-auth.sqlite3"
con = sqlite3.connect(AUTH_DB)
con.executescript(
    """
    CREATE TABLE auth_accounts (
        account_id TEXT PRIMARY KEY,
        username TEXT NOT NULL,
        email TEXT NOT NULL DEFAULT '',
        role TEXT NOT NULL DEFAULT 'operator',
        workspace_id TEXT NOT NULL DEFAULT 'default',
        account_status TEXT NOT NULL DEFAULT 'active',
        login_status TEXT NOT NULL DEFAULT 'offline',
        created_at TEXT NOT NULL DEFAULT (datetime('now'))
    );
    CREATE TABLE billing_wallets (
        account_id TEXT PRIMARY KEY,
        plan_type TEXT NOT NULL DEFAULT 'experience',
        plan_expire_at TEXT NOT NULL DEFAULT ''
    );
    CREATE TABLE auth_platform_sessions (
        session_id TEXT PRIMARY KEY,
        account_id TEXT NOT NULL,
        token_hash TEXT NOT NULL UNIQUE,
        expires_at TEXT NOT NULL,
        revoked_at TEXT NOT NULL DEFAULT '',
        last_used_at TEXT NOT NULL DEFAULT ''
    );
    CREATE TABLE invitation_code_usages (
        code TEXT NOT NULL,
        account_id TEXT NOT NULL UNIQUE,
        username TEXT NOT NULL DEFAULT '',
        email TEXT NOT NULL DEFAULT '',
        used_at TEXT NOT NULL,
        PRIMARY KEY (code, account_id)
    );
    """
)
import hashlib


def th(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


con.executemany(
    "INSERT INTO auth_accounts(account_id, username) VALUES (?,?)",
    [
        ("acc_basic", "basic_user"),
        ("acc_exp", "exp_user"),
        ("acc_nowallet", "nowallet_user"),
    ],
)
con.executemany(
    "INSERT INTO billing_wallets(account_id, plan_type) VALUES (?,?)",
    [("acc_basic", "basic"), ("acc_exp", "experience")],  # acc_nowallet 故意不插
)
con.executemany(
    "INSERT INTO invitation_code_usages(code, account_id, username, email, used_at) VALUES (?,?,?,?,?)",
    [
        ("CODE-A", "acc_basic", "basic_user", "", NOW),
        ("CODE-B", "acc_nowallet", "nowallet_user", "", NOW),
        # acc_exp 故意不插：模拟「邀请码追踪上线前注册」的历史账号
    ],
)
con.executemany(
    "INSERT INTO auth_platform_sessions(session_id, account_id, token_hash, expires_at, revoked_at, last_used_at) VALUES (?,?,?,?,?,?)",
    [
        ("s1", "acc_basic", th("tok_basic"), FUTURE, "", NOW),
        ("s2", "acc_exp", th("tok_exp"), FUTURE, "", NOW),
        ("s3", "acc_nowallet", th("tok_nowallet"), FUTURE, "", NOW),
        ("s4", "acc_basic", th("tok_revoked"), FUTURE, "2026-01-01T00:00:00+08:00", NOW),
    ],
)
con.commit()
con.close()

# ---- 2. 合成公告库 ----
ANN_DB = WORK / "announcements.sqlite3"
con = sqlite3.connect(ANN_DB)
con.executescript(
    """
    CREATE TABLE announcements (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        title TEXT NOT NULL,
        content TEXT NOT NULL DEFAULT '',
        published_at TEXT,
        active INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        target_account_ids TEXT NOT NULL DEFAULT '',
        image_rev INTEGER NOT NULL DEFAULT 0,
        images TEXT NOT NULL DEFAULT '[]',
        audience_rules TEXT NOT NULL DEFAULT ''
    );
    """
)
rows = [
    # (title, targets, rules)
    ("全员公告", "", ""),
    ("定向给 basic_user", json.dumps(["acc_basic"]), ""),
    ("分类·仅基础版", "", json.dumps({"plan_types": ["basic"]})),
    ("分类·仅体验版", "", json.dumps({"plan_types": ["experience"]})),
    ("分类·仅邀请码A", "", json.dumps({"invite_codes": ["CODE-A"]})),
    ("分类·仅邀请码B", "", json.dumps({"invite_codes": ["CODE-B"]})),
    ("交集·基础版且邀请码A", "", json.dumps({"plan_types": ["basic"], "invite_codes": ["CODE-A"]})),
    ("交集·体验版且邀请码B", "", json.dumps({"plan_types": ["experience"], "invite_codes": ["CODE-B"]})),
    ("名单∩分类·basic_user 且基础版", json.dumps(["acc_basic"]), json.dumps({"plan_types": ["basic"]})),
    ("名单∩分类·basic_user 但要求体验版", json.dumps(["acc_basic"]), json.dumps({"plan_types": ["experience"]})),
]
for title, targets, rules in rows:
    con.execute(
        "INSERT INTO announcements(title, content, published_at, active, created_at, updated_at, target_account_ids, audience_rules, images) "
        "VALUES(?,?,?,?,?,?,?,?,?)",
        (title, "正文", NOW, 1, NOW, NOW, targets, rules, json.dumps([{"name": "a.png", "mime": "image/png", "size": 1, "data": "AA=="}])),
    )
con.commit()
IDS = {t: i for i, t in enumerate([r[0] for r in rows], start=1)}
con.close()

# ---- 3. 以 LOCAL_MODE 装载 app ----
os.environ["WH_ADMIN_LOCAL_MODE"] = "1"
os.environ["ANNOUNCE_DB_PATH"] = str(ANN_DB)
cfg = HERE / "config.json"
backup_cfg = None
if cfg.exists():
    backup_cfg = cfg.read_bytes()
cfg.write_text(json.dumps({"database_path": str(AUTH_DB)}), encoding="utf-8")

sys.path.insert(0, str(HERE))
spec = importlib.util.spec_from_file_location("whadmin_app", HERE / "app.py")
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

from fastapi.testclient import TestClient  # noqa: E402

client = TestClient(mod.app)

PASS = []
FAIL = []


def check(name: str, cond: bool, detail: str = ""):
    (PASS if cond else FAIL).append((name, detail))
    print(("  PASS  " if cond else "  FAIL  ") + name + (f"   {detail}" if detail and not cond else ""))


def titles(token=None, account_id=None):
    params = {}
    if account_id is not None:
        params["account_id"] = account_id
    headers = {"x-auth-token": token} if token else {}
    r = client.get("/api/announcements/public", params=params, headers=headers)
    assert r.status_code == 200, r.text
    return [a["title"] for a in r.json()["announcements"]]


print("\n== 1. 无 token → 只拿全员 ==")
got = titles()
check("匿名只拿到全员公告", got == ["全员公告"], f"got={got}")

print("\n== 2. 伪造 account_id 无效 ==")
got = titles(account_id="acc_basic")
check("不带 token 时伪造 account_id 无效", got == ["全员公告"], f"got={got}")
got = titles(token="tok_exp", account_id="acc_basic")
check("带 token 时忽略 query 的 account_id（按 token 身份）", "定向给 basic_user" not in got, f"got={got}")

print("\n== 3. 按套餐分类 ==")
got_b = titles(token="tok_basic")
got_e = titles(token="tok_exp")
check("基础版用户看到「仅基础版」", "分类·仅基础版" in got_b, f"got={got_b}")
check("基础版用户看不到「仅体验版」", "分类·仅体验版" not in got_b, f"got={got_b}")
check("体验版用户看到「仅体验版」", "分类·仅体验版" in got_e, f"got={got_e}")
check("体验版用户看不到「仅基础版」", "分类·仅基础版" not in got_e, f"got={got_e}")

print("\n== 4. 按邀请码分类 ==")
got_n = titles(token="tok_nowallet")
check("CODE-A 用户看到「仅邀请码A」", "分类·仅邀请码A" in got_b, f"got={got_b}")
check("CODE-A 用户看不到「仅邀请码B」", "分类·仅邀请码B" not in got_b, f"got={got_b}")
check("CODE-B 用户看到「仅邀请码B」", "分类·仅邀请码B" in got_n, f"got={got_n}")
check("CODE-B 用户看不到「仅邀请码A」", "分类·仅邀请码A" not in got_n, f"got={got_n}")
check("无邀请码记录的账号（历史未记录）两个都看不到", "分类·仅邀请码A" not in got_e and "分类·仅邀请码B" not in got_e, f"got={got_e}")

print("\n== 5. 无钱包账号归入体验版 ==")
check("无钱包账号看到「仅体验版」", "分类·仅体验版" in got_n, f"got={got_n}")
check("无钱包账号看不到「仅基础版」", "分类·仅基础版" not in got_n, f"got={got_n}")

print("\n== 6. 匿名不等于体验版 ==")
got_anon = titles()  # 重新取匿名结果，别复用上面的旧变量
check("匿名看不到「仅体验版」", "分类·仅体验版" not in got_anon, f"got={got_anon}")
check("匿名看不到「仅基础版」", "分类·仅基础版" not in got_anon, f"got={got_anon}")
check("匿名看不到邀请码公告", "分类·仅邀请码A" not in got_anon and "分类·仅邀请码B" not in got_anon, f"got={got_anon}")

print("\n== 7. 交集语义 ==")
check("基础版+CODE-A 满足「基础版∧邀请码A」", "交集·基础版且邀请码A" in got_b, f"got={got_b}")
check("CODE-B 用户不满足「基础版∧邀请码A」（他是体验版）", "交集·基础版且邀请码A" not in got_n, f"got={got_n}")
check("无邀请码账号不满足「基础版∧邀请码A」", "交集·基础版且邀请码A" not in got_e, f"got={got_e}")
check("体验版+CODE-B 满足「体验版∧邀请码B」", "交集·体验版且邀请码B" in got_n, f"got={got_n}")
check("基础版用户不满足「体验版∧邀请码B」", "交集·体验版且邀请码B" not in got_b, f"got={got_b}")

print("\n== 8. 名单 ∩ 分类 ==")
check("名单∩分类：同为 basic 时可见", "名单∩分类·basic_user 但要求体验版" not in got_b and "名单∩分类·basic_user 且基础版" in got_b, f"got={got_b}")
check("名单里有他但分类不符 → 不可见", "名单∩分类·basic_user 但要求体验版" not in got_b, f"got={got_b}")
got_e2 = titles(token="tok_exp")
check("不在名单里的用户看不到名单∩分类公告", "名单∩分类·basic_user 且基础版" not in got_e2, f"got={got_e2}")

print("\n== 9. 撤销的会话视同匿名 ==")
got_r = titles(token="tok_revoked")
check("被撤销的 token 只拿全员", got_r == ["全员公告"], f"got={got_r}")

print("\n== 10. 图片接口的分类校验（不泄露存在性）==")
r = client.get(f"/api/announcements/{IDS['分类·仅基础版']}/images", headers={"x-auth-token": "tok_exp"})
check("不符合分类 → 404", r.status_code == 404, f"status={r.status_code}")
r = client.get(f"/api/announcements/{IDS['分类·仅基础版']}/images", headers={"x-auth-token": "tok_basic"})
check("符合分类 → 200", r.status_code == 200, f"status={r.status_code}")
r = client.get(f"/api/announcements/{IDS['分类·仅基础版']}/images")
check("匿名取分类公告图片 → 404", r.status_code == 404, f"status={r.status_code}")
r = client.get(f"/api/announcements/{IDS['分类·仅邀请码A']}/images", headers={"x-auth-token": "tok_nowallet"})
check("邀请码不符 → 404", r.status_code == 404, f"status={r.status_code}")

print("\n== 11. 邀请码选项接口（管理端）==")
r = client.get("/api/invitations/options")
check("未鉴权 → 401（路由已注册）", r.status_code == 401, f"status={r.status_code}")

print("\n== 12. 公开端不泄露受众信息 ==")
r = client.get("/api/announcements/public", headers={"x-auth-token": "tok_basic"})
payload = r.json()["announcements"][0]
check("公开端不带 target_account_ids", "target_account_ids" not in payload, f"keys={list(payload)}")
check("公开端不带 audience_rules", "audience_rules" not in payload, f"keys={list(payload)}")

print("\n== 13. 规则规范化（保存路径）==")
norm = mod._normalize_audience_rules({"audience_rules": {"plan_types": ["Basic"], "invite_codes": [" CODE-A ", "CODE-A", ""]}})
check("套餐大小写归一 + 邀请码去重去空", norm == {"plan_types": ["basic"], "invite_codes": ["CODE-A"]}, f"got={norm}")
dumped = mod._dump_audience_rules(norm)
check("非空规则落库为非空 JSON 串", "invite_codes" in dumped and dumped.startswith("{"), f"got={dumped}")
check("空规则落库为空串", mod._dump_audience_rules({"plan_types": [], "invite_codes": []}) == "", "")
loaded = mod._load_audience_rules('{"workspace_codes": ["STA-A"], "invite_codes": ["CODE-A"]}')
check("老键（workspace_codes）被忽略、invite_codes 正常读", loaded == {"invite_codes": ["CODE-A"]}, f"got={loaded}")

print("\n" + "=" * 60)
print(f"通过 {len(PASS)} 项 / 失败 {len(FAIL)} 项")
for n, d in FAIL:
    print(f"  ✗ {n}   {d}")

# 还原 config
if backup_cfg is not None:
    cfg.write_bytes(backup_cfg)
else:
    cfg.unlink(missing_ok=True)

sys.exit(1 if FAIL else 0)
