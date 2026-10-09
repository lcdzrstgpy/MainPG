"""Python snippets executed against the remote customer-auth SQLite database.

Each snippet receives JSON-compatible arguments in the global ``A`` and prints
one JSON object.  Keeping the database mutations on the server makes account,
session and audit-log updates atomic even though the desktop tool connects via
SSH.
"""

COMMON = r'''
import hashlib
import json
import secrets
import sqlite3
from datetime import datetime, timedelta, timezone

_CN_TZ = timezone(timedelta(hours=8))

def _now():
    return datetime.now(_CN_TZ).isoformat(timespec="seconds")

def _db():
    connection = sqlite3.connect(A[0], timeout=20)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA busy_timeout = 20000")
    connection.execute("PRAGMA foreign_keys = ON")
    return connection

def _token_hash(token):
    return hashlib.sha256(token.encode("utf-8")).hexdigest()

def _password_hash(password, salt, iterations=200000):
    return hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), bytes.fromhex(salt), iterations
    ).hex()

def _audit(conn, actor_id, actor_name, action, target_type="", target_id="",
           details=None, success=1, client_ip="", operation_id=None):
    op_id = operation_id or ("op_" + secrets.token_hex(12))
    conn.execute(
        """
        INSERT INTO admin_operation_logs (
            operation_id, actor_admin_id, actor_username, action,
            target_type, target_id, details_json, success, client_ip, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            op_id, actor_id or "", actor_name or "", action,
            target_type or "", target_id or "",
            json.dumps(details or {}, ensure_ascii=False),
            1 if success else 0, client_ip or "", _now(),
        ),
    )
    return op_id

def _actor(conn, token, allow_password_change=False):
    now = _now()
    row = conn.execute(
        """
        SELECT a.admin_id, a.username, a.status, a.must_change_password,
               s.session_id, s.expires_at
        FROM admin_sessions s
        JOIN admin_accounts a ON a.admin_id = s.admin_id
        WHERE s.token_hash = ? AND s.revoked_at = ''
          AND datetime(s.expires_at) > datetime(?)
        """,
        (_token_hash(token), now),
    ).fetchone()
    if row is None or row["status"] != "active":
        return None, "invalid_session"
    if row["must_change_password"] and not allow_password_change:
        return None, "password_change_required"
    conn.execute(
        "UPDATE admin_sessions SET last_used_at = ? WHERE session_id = ?",
        (now, row["session_id"]),
    )
    return row, ""

def _print(payload):
    print(json.dumps(payload, ensure_ascii=False))
    connection = globals().get("conn")
    if connection is not None:
        connection.close()

def _feedback_images_meta(conn, feedback_id):
    """反馈图片的轻量元信息（name/mime/size），不含 base64 本体。"""
    row = conn.execute(
        "SELECT images_json FROM customer_feedback WHERE feedback_id = ?",
        (feedback_id,),
    ).fetchone()
    if row is None or not row["images_json"]:
        return []
    try:
        items = json.loads(row["images_json"])
    except (ValueError, TypeError):
        return []
    if not isinstance(items, list):
        return []
    return [
        {"name": str(i.get("name") or "")[:80], "mime": str(i.get("mime") or ""),
         "size": int(i.get("size") or 0)}
        for i in items if isinstance(i, dict)
    ]
'''


SERVER_SCHEMA_SCRIPT = r'''
import hashlib
import json
import secrets
import sqlite3
from datetime import datetime, timedelta, timezone

CN_TZ = timezone(timedelta(hours=8))

def now():
    return datetime.now(CN_TZ).isoformat(timespec="seconds")

conn = sqlite3.connect(A[0], timeout=20)
conn.row_factory = sqlite3.Row
conn.execute("PRAGMA busy_timeout = 20000")
conn.executescript("""
CREATE TABLE IF NOT EXISTS admin_accounts (
    admin_id TEXT PRIMARY KEY,
    username TEXT NOT NULL COLLATE NOCASE UNIQUE,
    password_hash TEXT NOT NULL,
    salt TEXT NOT NULL,
    algorithm TEXT NOT NULL DEFAULT 'pbkdf2_sha256',
    iterations INTEGER NOT NULL DEFAULT 200000,
    must_change_password INTEGER NOT NULL DEFAULT 1,
    status TEXT NOT NULL DEFAULT 'active',
    created_by TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    last_login_at TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS admin_sessions (
    session_id TEXT PRIMARY KEY,
    admin_id TEXT NOT NULL,
    token_hash TEXT NOT NULL UNIQUE,
    expires_at TEXT NOT NULL,
    revoked_at TEXT NOT NULL DEFAULT '',
    last_used_at TEXT NOT NULL,
    created_at TEXT NOT NULL,
    client_ip TEXT NOT NULL DEFAULT '',
    user_agent TEXT NOT NULL DEFAULT '',
    FOREIGN KEY (admin_id) REFERENCES admin_accounts (admin_id)
);

CREATE INDEX IF NOT EXISTS idx_admin_sessions_active
    ON admin_sessions (token_hash, expires_at, revoked_at);

CREATE TABLE IF NOT EXISTS admin_operation_logs (
    operation_id TEXT PRIMARY KEY,
    actor_admin_id TEXT NOT NULL DEFAULT '',
    actor_username TEXT NOT NULL DEFAULT '',
    action TEXT NOT NULL,
    target_type TEXT NOT NULL DEFAULT '',
    target_id TEXT NOT NULL DEFAULT '',
    details_json TEXT NOT NULL DEFAULT '{}',
    success INTEGER NOT NULL DEFAULT 1,
    client_ip TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_admin_operation_logs_created
    ON admin_operation_logs (created_at DESC);
CREATE INDEX IF NOT EXISTS idx_admin_operation_logs_actor
    ON admin_operation_logs (actor_admin_id, created_at DESC);

CREATE TABLE IF NOT EXISTS invitation_codes (
    code TEXT PRIMARY KEY,
    max_uses INTEGER NOT NULL DEFAULT 100,
    used_count INTEGER NOT NULL DEFAULT 0,
    expires_at TEXT NOT NULL DEFAULT '',
    created_by TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    created_by_admin_id TEXT NOT NULL DEFAULT '',
    creation_operation_id TEXT NOT NULL DEFAULT '',
    remark TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS invitation_code_usages (
    code TEXT NOT NULL,
    account_id TEXT NOT NULL UNIQUE,
    username TEXT NOT NULL DEFAULT '',
    email TEXT NOT NULL DEFAULT '',
    used_at TEXT NOT NULL,
    PRIMARY KEY (code, account_id),
    FOREIGN KEY (code) REFERENCES invitation_codes (code),
    FOREIGN KEY (account_id) REFERENCES auth_accounts (account_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_invitation_code_usages_code
    ON invitation_code_usages (code, used_at DESC);
""")

columns = {row[1] for row in conn.execute("PRAGMA table_info(invitation_codes)")}
if "created_by_admin_id" not in columns:
    conn.execute(
        "ALTER TABLE invitation_codes ADD COLUMN created_by_admin_id TEXT NOT NULL DEFAULT ''"
    )
if "creation_operation_id" not in columns:
    conn.execute(
        "ALTER TABLE invitation_codes ADD COLUMN creation_operation_id TEXT NOT NULL DEFAULT ''"
    )
if "remark" not in columns:
    conn.execute(
        "ALTER TABLE invitation_codes ADD COLUMN remark TEXT NOT NULL DEFAULT ''"
    )

# 仅保留关键业务操作，并按自然月自动清理三个月以前的日志。
conn.execute(
    """
    DELETE FROM admin_operation_logs
    WHERE action IN (
        'admin.login', 'admin.logout', 'admin.change_password',
        'admin.test_session_cleanup', 'user.export'
    )
       OR datetime(created_at) < datetime('now', '-3 months')
    """
)

created = []
stamp = now()
for username in A[1]:
    admin_id = "adm_" + hashlib.sha256(username.lower().encode("utf-8")).hexdigest()[:20]
    salt = secrets.token_hex(16)
    password_hash = hashlib.pbkdf2_hmac(
        "sha256", A[2].encode("utf-8"), bytes.fromhex(salt), 200000
    ).hex()
    cur = conn.execute(
        """
        INSERT OR IGNORE INTO admin_accounts (
            admin_id, username, password_hash, salt, algorithm, iterations,
            must_change_password, status, created_by, created_at, updated_at
        ) VALUES (?, ?, ?, ?, 'pbkdf2_sha256', 200000, 1, 'active',
                  'system_bootstrap', ?, ?)
        """,
        (admin_id, username, password_hash, salt, stamp, stamp),
    )
    if cur.rowcount:
        op_id = "op_" + secrets.token_hex(12)
        conn.execute(
            """
            INSERT INTO admin_operation_logs (
                operation_id, actor_admin_id, actor_username, action,
                target_type, target_id, details_json, success, client_ip, created_at
            ) VALUES (?, 'system', '系统初始化', 'admin.bootstrap_create',
                      'admin', ?, ?, 1, '', ?)
            """,
            (op_id, admin_id, json.dumps({"username": username}, ensure_ascii=False), stamp),
        )
        created.append(username)

conn.commit()
print(json.dumps({"ok": True, "created": created}, ensure_ascii=False))
conn.close()
'''


SERVER_LOGIN_SCRIPT = COMMON + r'''
import hmac

conn = _db()
username, password, client_ip, user_agent = A[1], A[2], A[3], A[4]
row = conn.execute(
    """
    SELECT admin_id, username, password_hash, salt, iterations,
           must_change_password, status
    FROM admin_accounts WHERE username = ? COLLATE NOCASE
    """,
    (username,),
).fetchone()

valid = False
if row is not None:
    candidate = _password_hash(password, row["salt"], int(row["iterations"]))
    valid = hmac.compare_digest(candidate, row["password_hash"])

if row is None or not valid:
    _print({"ok": False, "error": "invalid_credentials"})
elif row["status"] != "active":
    _print({"ok": False, "error": "account_disabled"})
else:
    token = "wh_admin_" + secrets.token_urlsafe(32)
    session_id = "admin_sess_" + secrets.token_hex(16)
    stamp = _now()
    expires_at = (datetime.now(_CN_TZ) + timedelta(hours=12)).isoformat(timespec="seconds")
    conn.execute(
        """
        INSERT INTO admin_sessions (
            session_id, admin_id, token_hash, expires_at, revoked_at,
            last_used_at, created_at, client_ip, user_agent
        ) VALUES (?, ?, ?, ?, '', ?, ?, ?, ?)
        """,
        (session_id, row["admin_id"], _token_hash(token), expires_at,
         stamp, stamp, client_ip, user_agent),
    )
    conn.execute(
        "UPDATE admin_accounts SET last_login_at = ?, updated_at = ? WHERE admin_id = ?",
        (stamp, stamp, row["admin_id"]),
    )
    conn.commit()
    _print({
        "ok": True,
        "token": token,
        "expires_at": expires_at,
        "admin": {
            "admin_id": row["admin_id"],
            "username": row["username"],
            "must_change_password": bool(row["must_change_password"]),
        },
    })
'''


SERVER_VALIDATE_SESSION_SCRIPT = COMMON + r'''
conn = _db()
actor, error = _actor(conn, A[1], bool(A[2]))
conn.commit()
if actor is None:
    _print({"ok": False, "error": error})
else:
    _print({
        "ok": True,
        "admin": {
            "admin_id": actor["admin_id"],
            "username": actor["username"],
            "must_change_password": bool(actor["must_change_password"]),
        },
    })
'''


SERVER_LOGOUT_SCRIPT = COMMON + r'''
conn = _db()
actor, error = _actor(conn, A[1], True)
if actor is None:
    _print({"ok": True})
else:
    stamp = _now()
    conn.execute(
        "UPDATE admin_sessions SET revoked_at = ? WHERE session_id = ?",
        (stamp, actor["session_id"]),
    )
    conn.commit()
    _print({"ok": True})
'''


SERVER_CHANGE_ADMIN_PASSWORD_SCRIPT = COMMON + r'''
import hmac

conn = _db()
actor, error = _actor(conn, A[1], True)
if actor is None:
    _print({"ok": False, "error": error})
else:
    account = conn.execute(
        "SELECT password_hash, salt, iterations FROM admin_accounts WHERE admin_id = ?",
        (actor["admin_id"],),
    ).fetchone()
    current_ok = hmac.compare_digest(
        _password_hash(A[2], account["salt"], int(account["iterations"])),
        account["password_hash"],
    )
    if not current_ok:
        _print({"ok": False, "error": "bad_current_password"})
    else:
        salt = secrets.token_hex(16)
        stamp = _now()
        conn.execute(
            """
            UPDATE admin_accounts
            SET password_hash = ?, salt = ?, iterations = 200000,
                must_change_password = 0, updated_at = ?
            WHERE admin_id = ?
            """,
            (_password_hash(A[3], salt), salt, stamp, actor["admin_id"]),
        )
        conn.execute(
            """
            UPDATE admin_sessions SET revoked_at = ?
            WHERE admin_id = ? AND session_id <> ? AND revoked_at = ''
            """,
            (stamp, actor["admin_id"], actor["session_id"]),
        )
        conn.commit()
        _print({"ok": True, "admin": {
            "admin_id": actor["admin_id"], "username": actor["username"],
            "must_change_password": False,
        }})
'''


SERVER_LIST_USERS_SCRIPT = COMMON + r'''
conn = _db()
limit, offset = int(A[1]), int(A[2])
rows = conn.execute("""
SELECT a.account_id, a.username, a.email, a.display_name,
       COALESCE(r.role_name, a.role) AS role_name, a.workspace_id,
       a.account_status, a.login_status, a.created_at,
       COALESCE(u.code, '') AS invitation_code,
       (SELECT MAX(l.created_at) FROM auth_login_logs l
         WHERE l.account_id = a.account_id AND l.success = 1) AS last_login,
       (SELECT COALESCE(SUM(o.amount_cents), 0) FROM billing_payment_orders o
         WHERE o.account_id = a.account_id AND o.status = 'paid') AS total_recharge_cents
FROM auth_accounts a
LEFT JOIN roles r ON r.role = a.role
LEFT JOIN invitation_code_usages u ON u.account_id = a.account_id
ORDER BY a.created_at DESC
LIMIT ? OFFSET ?
""", (limit, offset)).fetchall()
total = conn.execute("SELECT COUNT(*) FROM auth_accounts").fetchone()[0]
_print({"ok": True, "users": [dict(row) for row in rows], "total": total,
       "offset": offset, "limit": limit, "has_more": (offset + len(rows)) < total})
'''


SERVER_SET_USER_STATUS_SCRIPT = COMMON + r'''
conn = _db()
actor, error = _actor(conn, A[1])
if actor is None:
    _print({"ok": False, "error": error})
else:
    target = conn.execute(
        "SELECT username, account_status FROM auth_accounts WHERE account_id = ?", (A[2],)
    ).fetchone()
    if target is None:
        _print({"ok": False, "error": "not_found"})
    else:
        stamp = _now()
        conn.execute(
            "UPDATE auth_accounts SET account_status = ?, updated_at = ? WHERE account_id = ?",
            (A[3], stamp, A[2]),
        )
        if A[3] == "disabled":
            conn.execute(
                "UPDATE auth_platform_sessions SET revoked_at = ? WHERE account_id = ? AND revoked_at = ''",
                (stamp, A[2]),
            )
        op_id = _audit(
            conn, actor["admin_id"], actor["username"], "user.set_status",
            "user", A[2], {"username": target["username"], "from": target["account_status"], "to": A[3]},
            1, A[4],
        )
        conn.commit()
        _print({"ok": True, "operation_id": op_id})
'''


SERVER_RESET_USER_PASSWORD_SCRIPT = COMMON + r'''
conn = _db()
actor, error = _actor(conn, A[1])
if actor is None:
    _print({"ok": False, "error": error})
else:
    target = conn.execute(
        "SELECT username FROM auth_accounts WHERE account_id = ?", (A[2],)
    ).fetchone()
    if target is None:
        _print({"ok": False, "error": "not_found"})
    else:
        salt = secrets.token_hex(16)
        stamp = _now()
        conn.execute("""
        INSERT INTO auth_password_credentials
          (account_id, password_hash, salt, algorithm, iterations, updated_at)
        VALUES (?, ?, ?, 'pbkdf2_sha256', 200000, ?)
        ON CONFLICT(account_id) DO UPDATE SET
          password_hash = excluded.password_hash,
          salt = excluded.salt,
          algorithm = excluded.algorithm,
          iterations = excluded.iterations,
          updated_at = excluded.updated_at
        """, (A[2], _password_hash(A[3], salt), salt, stamp))
        conn.execute(
            "UPDATE auth_platform_sessions SET revoked_at = ? WHERE account_id = ? AND revoked_at = ''",
            (stamp, A[2]),
        )
        conn.execute(
            "UPDATE auth_accounts SET login_status = 'offline', updated_at = ? WHERE account_id = ?",
            (stamp, A[2]),
        )
        op_id = _audit(conn, actor["admin_id"], actor["username"], "user.reset_password",
                       "user", A[2], {"username": target["username"]}, 1, A[4])
        conn.commit()
        _print({"ok": True, "operation_id": op_id})
'''


SERVER_FORCE_LOGOUT_USER_SCRIPT = COMMON + r'''
conn = _db()
actor, error = _actor(conn, A[1])
if actor is None:
    _print({"ok": False, "error": error})
else:
    target = conn.execute(
        "SELECT username FROM auth_accounts WHERE account_id = ?", (A[2],)
    ).fetchone()
    if target is None:
        _print({"ok": False, "error": "not_found"})
    else:
        stamp = _now()
        conn.execute(
            "UPDATE auth_accounts SET login_status = 'offline', updated_at = ? WHERE account_id = ?",
            (stamp, A[2]),
        )
        conn.execute(
            "UPDATE auth_platform_sessions SET revoked_at = ? WHERE account_id = ? AND revoked_at = ''",
            (stamp, A[2]),
        )
        op_id = _audit(conn, actor["admin_id"], actor["username"], "user.force_logout",
                       "user", A[2], {"username": target["username"]}, 1, A[3])
        conn.commit()
        _print({"ok": True, "operation_id": op_id})
'''


SERVER_LIST_INVITATIONS_SCRIPT = COMMON + r'''
conn = _db()
limit, offset = int(A[1]), int(A[2])
rows = conn.execute("""
SELECT i.code, i.max_uses, i.used_count, i.expires_at, i.created_at,
       i.created_by, i.created_by_admin_id, i.creation_operation_id, i.remark,
       COALESCE(a.username, i.created_by) AS created_by_username
FROM invitation_codes i
LEFT JOIN admin_accounts a
  ON a.admin_id = CASE WHEN i.created_by_admin_id <> ''
                       THEN i.created_by_admin_id ELSE i.created_by END
ORDER BY i.created_at DESC
LIMIT ? OFFSET ?
""", (limit, offset)).fetchall()
total = conn.execute("SELECT COUNT(*) FROM invitation_codes").fetchone()[0]
usage_rows = conn.execute("""
SELECT u.code, u.account_id, u.username, u.email, u.used_at,
       a.account_status, a.login_status,
       (SELECT COALESCE(SUM(o.amount_cents), 0) FROM billing_payment_orders o
         WHERE o.account_id = u.account_id AND o.status = 'paid') AS total_recharge_cents
FROM invitation_code_usages u
LEFT JOIN auth_accounts a ON a.account_id = u.account_id
ORDER BY u.used_at DESC
""").fetchall()
accounts_by_code = {}
for usage in usage_rows:
    accounts_by_code.setdefault(usage["code"], []).append({
        "account_id": usage["account_id"],
        "username": usage["username"],
        "email": usage["email"],
        "used_at": usage["used_at"],
        "account_status": usage["account_status"] or "",
        "login_status": usage["login_status"] or "",
        "total_recharge_cents": usage["total_recharge_cents"] or 0,
    })
items = []
for row in rows:
    item = dict(row)
    accounts = accounts_by_code.get(row["code"], [])
    item["accounts"] = accounts
    item["total_recharge_cents"] = sum(
        int(account.get("total_recharge_cents") or 0) for account in accounts
    )
    items.append(item)
_print({"ok": True, "invitations": items, "total": total,
       "offset": offset, "limit": limit, "has_more": (offset + len(rows)) < total})
'''


SERVER_GENERATE_INVITATIONS_SCRIPT = COMMON + r'''
conn = _db()
actor, error = _actor(conn, A[1])
if actor is None:
    _print({"ok": False, "error": error})
else:
    count, max_uses, expires_at, client_ip, remark = int(A[2]), int(A[3]), A[4], A[5], str(A[6] or "").strip()
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    def chunk(size):
        return "".join(secrets.choice(alphabet) for _ in range(size))
    codes = []
    operation_id = "op_" + secrets.token_hex(12)
    stamp = _now()
    attempts = 0
    while len(codes) < count and attempts < count * 10:
        attempts += 1
        code = "MAINPG-" + chunk(4) + "-" + chunk(4)
        cur = conn.execute("""
            INSERT OR IGNORE INTO invitation_codes (
                code, max_uses, used_count, expires_at, created_by, created_at,
                created_by_admin_id, creation_operation_id, remark
            ) VALUES (?, ?, 0, ?, ?, ?, ?, ?, ?)
        """, (code, max_uses, expires_at, actor["admin_id"], stamp,
                actor["admin_id"], operation_id, remark))
        if cur.rowcount:
            codes.append(code)
    _audit(conn, actor["admin_id"], actor["username"], "invitation.generate",
           "invitation_batch", operation_id,
           {"count": len(codes), "max_uses": max_uses, "expires_at": expires_at,
            "remark": remark, "codes": codes},
           1, client_ip, operation_id)
    conn.commit()
    _print({"ok": True, "count": len(codes), "codes": codes,
            "operation_id": operation_id})
'''


SERVER_LIST_ADMINS_SCRIPT = COMMON + r'''
conn = _db()
rows = conn.execute("""
SELECT a.admin_id, a.username, a.must_change_password, a.status,
       a.created_by, creator.username AS created_by_username,
       a.created_at, a.updated_at, a.last_login_at
FROM admin_accounts a
LEFT JOIN admin_accounts creator ON creator.admin_id = a.created_by
ORDER BY a.created_at ASC, a.username COLLATE NOCASE ASC
""").fetchall()
_print({"ok": True, "admins": [dict(row) for row in rows]})
'''


SERVER_CREATE_ADMIN_SCRIPT = COMMON + r'''
conn = _db()
actor, error = _actor(conn, A[1])
if actor is None:
    _print({"ok": False, "error": error})
elif conn.execute("SELECT 1 FROM admin_accounts WHERE username = ? COLLATE NOCASE", (A[2],)).fetchone():
    _print({"ok": False, "error": "username_exists"})
else:
    admin_id = "adm_" + secrets.token_hex(10)
    salt = secrets.token_hex(16)
    stamp = _now()
    conn.execute("""
        INSERT INTO admin_accounts (
            admin_id, username, password_hash, salt, algorithm, iterations,
            must_change_password, status, created_by, created_at, updated_at
        ) VALUES (?, ?, ?, ?, 'pbkdf2_sha256', 200000, 1, 'active', ?, ?, ?)
    """, (admin_id, A[2], _password_hash(A[3], salt), salt,
            actor["admin_id"], stamp, stamp))
    op_id = _audit(conn, actor["admin_id"], actor["username"], "admin.create",
                   "admin", admin_id, {"username": A[2]}, 1, A[4])
    conn.commit()
    _print({"ok": True, "admin_id": admin_id, "operation_id": op_id})
'''


SERVER_SET_ADMIN_STATUS_SCRIPT = COMMON + r'''
conn = _db()
actor, error = _actor(conn, A[1])
if actor is None:
    _print({"ok": False, "error": error})
elif actor["admin_id"] == A[2] and A[3] == "disabled":
    _print({"ok": False, "error": "cannot_disable_self"})
else:
    target = conn.execute(
        "SELECT username, status FROM admin_accounts WHERE admin_id = ?", (A[2],)
    ).fetchone()
    if target is None:
        _print({"ok": False, "error": "not_found"})
    elif A[3] == "disabled" and conn.execute(
        "SELECT COUNT(*) FROM admin_accounts WHERE status = 'active'"
    ).fetchone()[0] <= 1:
        _print({"ok": False, "error": "last_active_admin"})
    else:
        stamp = _now()
        conn.execute(
            "UPDATE admin_accounts SET status = ?, updated_at = ? WHERE admin_id = ?",
            (A[3], stamp, A[2]),
        )
        if A[3] == "disabled":
            conn.execute(
                "UPDATE admin_sessions SET revoked_at = ? WHERE admin_id = ? AND revoked_at = ''",
                (stamp, A[2]),
            )
        op_id = _audit(conn, actor["admin_id"], actor["username"], "admin.set_status",
                       "admin", A[2], {"username": target["username"], "from": target["status"], "to": A[3]},
                       1, A[4])
        conn.commit()
        _print({"ok": True, "operation_id": op_id})
'''


SERVER_RESET_ADMIN_PASSWORD_SCRIPT = COMMON + r'''
conn = _db()
actor, error = _actor(conn, A[1])
if actor is None:
    _print({"ok": False, "error": error})
else:
    target = conn.execute(
        "SELECT username FROM admin_accounts WHERE admin_id = ?", (A[2],)
    ).fetchone()
    if target is None:
        _print({"ok": False, "error": "not_found"})
    else:
        salt = secrets.token_hex(16)
        stamp = _now()
        conn.execute("""
            UPDATE admin_accounts
            SET password_hash = ?, salt = ?, iterations = 200000,
                must_change_password = 1, updated_at = ?
            WHERE admin_id = ?
        """, (_password_hash(A[3], salt), salt, stamp, A[2]))
        conn.execute(
            "UPDATE admin_sessions SET revoked_at = ? WHERE admin_id = ? AND revoked_at = ''",
            (stamp, A[2]),
        )
        op_id = _audit(conn, actor["admin_id"], actor["username"], "admin.reset_password",
                       "admin", A[2], {"username": target["username"]}, 1, A[4])
        conn.commit()
        _print({"ok": True, "operation_id": op_id})
'''


SERVER_LIST_AUDIT_SCRIPT = COMMON + r'''
conn = _db()
limit, offset = int(A[1]), int(A[2])
rows = conn.execute("""
SELECT operation_id, actor_admin_id, actor_username, action,
       target_type, target_id, details_json, success, client_ip, created_at
FROM admin_operation_logs
ORDER BY created_at DESC, operation_id DESC
LIMIT ? OFFSET ?
""", (limit, offset)).fetchall()
total = conn.execute("SELECT COUNT(*) FROM admin_operation_logs").fetchone()[0]
_print({"ok": True, "logs": [dict(row) for row in rows], "total": total,
       "offset": offset, "limit": limit, "has_more": (offset + len(rows)) < total})
'''


SERVER_LIST_USER_ACTIVITY_SCRIPT = COMMON + r'''
conn = _db()
limit, offset = int(A[1]), int(A[2])
_WINDOW = "datetime(created_at) >= datetime('now', '-3 months')"
rows = conn.execute("""
SELECT * FROM (
  SELECT 'login' AS type, 'lg_' || id AS operation_id, account_id AS actor_admin_id,
         username AS actor_username,
         CASE WHEN success THEN 'auth.login_success' ELSE 'auth.login_fail' END AS action,
         'account' AS target_type, COALESCE(email, username, '') AS target_id,
         COALESCE(failure_reason, '') AS details_json, success, '' AS client_ip, created_at
  FROM auth_login_logs WHERE """ + _WINDOW + """
  UNION ALL
  SELECT 'security', 'sec_' || event_id, account_id, account_id, event_type,
         'account', account_id, metadata_json, success, COALESCE(ip, ''), created_at
  FROM auth_security_events WHERE """ + _WINDOW + """
  UNION ALL
  SELECT 'ai', 'gw_' || usage_id, account_id, account_id, 'ai.' || feature_key,
         feature_key, COALESCE(provider_task_id, ''), '',
         CASE WHEN status = 'succeeded' THEN 1 ELSE 0 END, '', created_at
  FROM billing_ai_gateway_requests WHERE """ + _WINDOW + """
  UNION ALL
  SELECT 'freeze', 'fz_' || freeze_id, account_id, account_id, 'billing.freeze',
         'batch', freeze_id, '',
         CASE WHEN status IN ('frozen', 'settled') THEN 1 ELSE 0 END, '', created_at
  FROM billing_batch_freezes WHERE """ + _WINDOW + """
)
ORDER BY datetime(created_at) DESC, operation_id DESC
LIMIT ? OFFSET ?
""", (limit, offset)).fetchall()
total = conn.execute("""
SELECT (SELECT COUNT(*) FROM auth_login_logs WHERE """ + _WINDOW + """)
     + (SELECT COUNT(*) FROM auth_security_events WHERE """ + _WINDOW + """)
     + (SELECT COUNT(*) FROM billing_ai_gateway_requests WHERE """ + _WINDOW + """)
     + (SELECT COUNT(*) FROM billing_batch_freezes WHERE """ + _WINDOW + """)
""").fetchone()[0]
_print({"ok": True, "logs": [dict(row) for row in rows], "total": total,
       "offset": offset, "limit": limit, "has_more": (offset + len(rows)) < total})
'''


SERVER_AUDIT_ONLY_SCRIPT = COMMON + r'''
conn = _db()
actor, error = _actor(conn, A[1])
if actor is None:
    _print({"ok": False, "error": error})
else:
    op_id = _audit(conn, actor["admin_id"], actor["username"], A[2],
                   A[3], A[4], json.loads(A[5] or "{}"), 1, A[6])
    conn.commit()
    _print({"ok": True, "operation_id": op_id})
'''


SERVER_PURGE_AUDIT_SCRIPT = COMMON + r'''
conn = _db()
# 操作日志的 created_at 由 _now() 写入，格式单一（+08:00 的 ISO 文本），
# 所以用同格式的边界做裸列比较，可以直接命中 idx_admin_operation_logs_created
# 做范围删除；原来写成 datetime(created_at) < datetime(...) 会让索引失效。
_boundary = conn.execute(
    "SELECT strftime('%Y-%m-%dT%H:%M:%S', 'now', '-3 months', '+8 hours') || '+08:00'"
).fetchone()[0]
_cur = conn.execute(
    "DELETE FROM admin_operation_logs WHERE created_at < ?", (_boundary,)
)
conn.commit()
_print({"ok": True, "deleted": _cur.rowcount})
'''


SERVER_DASHBOARD_SCRIPT = COMMON + r'''
conn = _db()
include_revenue = bool(A[1])

def _one(sql):
    return dict(conn.execute(sql).fetchone())

scale = _one(
    "SELECT COALESCE(MAX(point_unit_scale), 10) AS s FROM billing_pricing_rules"
)["s"] or 10

online_users = _one(
    "SELECT COUNT(*) AS c FROM auth_accounts WHERE login_status = 'online'"
)["c"]
total_users = _one("SELECT COUNT(*) AS c FROM auth_accounts")["c"]
total_invites = _one("SELECT COUNT(*) AS c FROM invitation_codes")["c"]

today = _one("""
SELECT COALESCE(SUM(amount_cents), 0) AS amount_cents,
       COALESCE(SUM(points), 0) AS points
FROM billing_payment_orders
WHERE status = 'paid'
  AND date(paid_at, '+8 hours') = date('now', '+8 hours')
""")

today_consumed = _one("""
SELECT COALESCE(SUM(points_delta), 0) AS points
FROM billing_point_ledger
WHERE direction = 'debit'
  AND date(created_at, '+8 hours') = date('now', '+8 hours')
""")

total_revenue_cents = 0
if include_revenue:
    total_revenue_cents = _one(
        "SELECT COALESCE(SUM(amount_cents), 0) AS c FROM billing_payment_orders WHERE status = 'paid'"
    )["c"]

# 30 天趋势：原来是「每天 × 每列」各跑一个相关子查询（flow 3 列 + growth 2 列
# 合计约 180 次表扫描），改成每张表只扫一次做 GROUP BY，缺的天数按骨架补零。
_DAY_RANGE = "BETWEEN date('now','+8 hours','-29 days') AND date('now','+8 hours')"

_days = [row["d"] for row in conn.execute("""
WITH RECURSIVE days(d) AS (
  SELECT date('now', '+8 hours', '-29 days')
  UNION ALL
  SELECT date(d, '+1 day') FROM days WHERE d < date('now', '+8 hours')
)
SELECT d FROM days
""").fetchall()]

_revenue = {row["day"]: int(row["cents"] or 0) for row in conn.execute("""
SELECT date(p.paid_at, '+8 hours') AS day, SUM(p.amount_cents) AS cents
FROM billing_payment_orders p
WHERE p.status = 'paid'
  AND date(p.paid_at, '+8 hours') """ + _DAY_RANGE + """
GROUP BY day
""").fetchall()}

_points = {}
for row in conn.execute("""
SELECT date(l.created_at, '+8 hours') AS day, l.direction, SUM(l.points_delta) AS points
FROM billing_point_ledger l
WHERE date(l.created_at, '+8 hours') """ + _DAY_RANGE + """
GROUP BY day, l.direction
""").fetchall():
    _points[(row["day"], row["direction"])] = int(row["points"] or 0)

flow = [{
    "day": day,
    "revenue_cents": _revenue.get(day, 0),
    "issued_points": _points.get((day, "credit"), 0),
    "consumed_points": _points.get((day, "debit"), 0),
} for day in _days]

_new_users = {row["day"]: int(row["c"] or 0) for row in conn.execute("""
SELECT date(a.created_at, '+8 hours') AS day, COUNT(*) AS c
FROM auth_accounts a
WHERE date(a.created_at, '+8 hours') """ + _DAY_RANGE + """
GROUP BY day
""").fetchall()}

_active_users = {row["day"]: int(row["c"] or 0) for row in conn.execute("""
SELECT day, COUNT(DISTINCT account_id) AS c FROM (
  SELECT date(created_at, '+8 hours') AS day, account_id FROM auth_login_logs
  WHERE success = 1 AND date(created_at, '+8 hours') """ + _DAY_RANGE + """
  UNION
  SELECT date(created_at, '+8 hours') AS day, account_id FROM billing_point_ledger
  WHERE date(created_at, '+8 hours') """ + _DAY_RANGE + """
) GROUP BY day
""").fetchall()}

growth = [{
    "day": day,
    "new_users": _new_users.get(day, 0),
    "active_users": _active_users.get(day, 0),
} for day in _days]

_stats = {
    "online_users": online_users,
    "total_users": total_users,
    "total_invites": total_invites,
    "today_revenue_cents": today["amount_cents"],
    "today_issued_points": int(today["points"] // scale),
    "today_consumed_points": int(today_consumed["points"] // scale),
}
if include_revenue:
    _stats["total_revenue_cents"] = total_revenue_cents

_print({
    "ok": True,
    "stats": _stats,
    "flow": [
        {"day": row["day"], "revenue_cents": row["revenue_cents"],
         "issued_points": int(row["issued_points"] // scale),
         "consumed_points": int(row["consumed_points"] // scale)}
        for row in flow
    ],
    "growth": growth,
})
'''


SERVER_VERIFY_ADMIN_PASSWORD_SCRIPT = COMMON + r'''
import hmac

conn = _db()
actor, error = _actor(conn, A[1])
if actor is None:
    _print({"ok": False, "error": error})
else:
    row = conn.execute(
        "SELECT password_hash, salt, iterations FROM admin_accounts WHERE admin_id = ?",
        (actor["admin_id"],),
    ).fetchone()
    candidate = _password_hash(A[2], row["salt"], int(row["iterations"]))
    if hmac.compare_digest(candidate, row["password_hash"]):
        _print({"ok": True})
    else:
        _print({"ok": False, "error": "invalid_password"})
'''


SERVER_LIST_FEEDBACK_SCRIPT = COMMON + r'''
conn = _db()
limit, offset = int(A[1]), int(A[2])
# 列表不带 images_json / total_image_bytes 大字段（base64 动辄数 MB），
# 只带每张图的轻量元信息；图片本体走详情接口按需取，避免打开页面卡顿。
rows = conn.execute("""
SELECT feedback_id, account_id, username, category, content, contact,
       image_count, status, admin_note,
       admin_id, status_updated_at, app_version, platform, client_ip, created_at
FROM customer_feedback
ORDER BY created_at DESC, feedback_id DESC
LIMIT ? OFFSET ?
""", (limit, offset)).fetchall()
total = conn.execute("SELECT COUNT(*) FROM customer_feedback").fetchone()[0]
out = []
for row in rows:
    item = dict(row)
    item["images_meta"] = _feedback_images_meta(conn, row["feedback_id"])
    out.append(item)
_print({"ok": True, "feedback": out, "total": total,
       "offset": offset, "limit": limit, "has_more": (offset + len(rows)) < total})
'''


SERVER_GET_FEEDBACK_IMAGES_SCRIPT = COMMON + r'''
conn = _db()
actor, error = _actor(conn, A[1])
if actor is None:
    _print({"ok": False, "error": error})
else:
    row = conn.execute(
        "SELECT feedback_id, images_json FROM customer_feedback WHERE feedback_id = ?",
        (A[2],),
    ).fetchone()
    if row is None:
        _print({"ok": False, "error": "feedback_not_found"})
    else:
        _print({"ok": True, "feedback_id": row["feedback_id"],
                "images_json": row["images_json"]})
'''


SERVER_DELETE_FEEDBACK_SCRIPT = COMMON + r'''
conn = _db()
actor, error = _actor(conn, A[1])
if actor is None:
    _print({"ok": False, "error": error})
else:
    row = conn.execute(
        "SELECT feedback_id, account_id, username FROM customer_feedback WHERE feedback_id = ?",
        (A[2],),
    ).fetchone()
    if row is None:
        _print({"ok": False, "error": "feedback_not_found"})
    else:
        conn.execute("DELETE FROM customer_feedback WHERE feedback_id = ?", (A[2],))
        _audit(
            conn, actor["admin_id"], actor["username"], "feedback.delete",
            "feedback", A[2],
            {"account_id": row["account_id"], "username": row["username"]},
            success=1, client_ip=A[3],
        )
        conn.commit()
        _print({"ok": True, "deleted": A[2]})
'''


SERVER_UPDATE_FEEDBACK_SCRIPT = COMMON + r'''
conn = _db()
actor, error = _actor(conn, A[1])
if actor is None:
    _print({"ok": False, "error": error})
else:
    status = str(A[3])
    if status not in ("new", "processing", "resolved"):
        _print({"ok": False, "error": "invalid_status"})
    else:
        note = str(A[4])[:2000]
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        row = conn.execute(
            "SELECT feedback_id FROM customer_feedback WHERE feedback_id = ?", (A[2],)
        ).fetchone()
        if row is None:
            _print({"ok": False, "error": "feedback_not_found"})
        else:
            conn.execute(
                "UPDATE customer_feedback SET status = ?, admin_note = ?, "
                "admin_id = ?, status_updated_at = ? WHERE feedback_id = ?",
                (status, note, actor["admin_id"], now, A[2]),
            )
            conn.commit()
            _print({"ok": True, "status": status, "admin_note": note,
                    "status_updated_at": now})
'''


SERVER_COUNT_NEW_FEEDBACK_SCRIPT = COMMON + r'''
conn = _db()
count = conn.execute(
    "SELECT COUNT(*) FROM customer_feedback WHERE status = 'new'"
).fetchone()[0]
_print({"ok": True, "new_count": count})
'''
