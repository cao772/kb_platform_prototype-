"""Local accounts, scoped sessions and the WeChat website login flow."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import time
from http.cookies import SimpleCookie
from urllib.parse import urlencode, urlsplit
from urllib.request import urlopen

ROLES = {
    "reader": ("账号用户", {"read", "ask"}),
    "product": ("产品线", {"read", "ask", "edit"}),
    "manager": ("合规经理", {"read", "ask", "edit", "collect", "manage", "contacts"}),
    "expert": ("审核专家", {"read", "ask", "edit", "review"}),
    "admin": ("管理员", {"read", "ask", "edit", "collect", "manage", "contacts", "review", "admin"}),
}
SCHEMA = """
CREATE TABLE IF NOT EXISTS ws_users (
 id INTEGER PRIMARY KEY, username TEXT NOT NULL UNIQUE COLLATE NOCASE, role TEXT NOT NULL DEFAULT 'reader',
 password TEXT NOT NULL DEFAULT '', recovery TEXT NOT NULL DEFAULT '', wechat TEXT UNIQUE,
 enabled INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS ws_tokens (
 digest TEXT PRIMARY KEY, purpose TEXT NOT NULL, user_id INTEGER, expires INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS ws_tokens_expiry ON ws_tokens(expires);
CREATE TABLE IF NOT EXISTS ws_audit (
 id INTEGER PRIMARY KEY, actor INTEGER, action TEXT NOT NULL, subject TEXT NOT NULL,
 at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
"""
COOKIE = "kb_session"


class Denied(Exception):
    def __init__(self, message, status=403):
        super().__init__(message)
        self.status = status


def digest(text):
    return hashlib.sha256(text.encode()).hexdigest()


def encode_password(password, salt=None):
    salt = salt or secrets.token_hex(16)
    value = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), 240000).hex()
    return salt + "." + value


def valid_password(value):
    if not isinstance(value, str) or not 10 <= len(value) <= 180:
        raise ValueError("请输入 10 至 180 个字符的密码。")
    return encode_password(value)


def user_view(row):
    if not row:
        return {"id": None, "username": "访客", "role": "guest", "permissions": ["read"]}
    return {
        "id": row["id"],
        "username": row["username"],
        "role": row["role"],
        "permissions": sorted(ROLES[row["role"]][1]),
    }


def require(actor, capability):
    if capability not in actor["permissions"]:
        if not actor["id"]:
            raise Denied("请先在业务工作台登录。", 401)
        raise Denied("此操作需要相应的业务角色。")


def cookie_value(headers, key):
    jar = SimpleCookie()
    try:
        jar.load(headers.get("Cookie", ""))
    except Exception:
        return ""
    return jar[key].value if key in jar else ""


class Accounts:
    def __init__(self, store):
        self.store = store
        self.db = store.conn
        with store.lock:
            self.db.executescript(SCHEMA)

    def audit(self, actor, action, subject=""):
        self.db.execute(
            "INSERT INTO ws_audit(actor,action,subject) VALUES(?,?,?)",
            (actor.get("id"), action, str(subject)[:350]),
        )

    def token(self, purpose, user_id, lifetime):
        now = int(time.time())
        self.db.execute("DELETE FROM ws_tokens WHERE expires<=?", (now,))
        secret = secrets.token_urlsafe(32)
        self.db.execute(
            "INSERT INTO ws_tokens VALUES(?,?,?,?)", (digest(secret), purpose, user_id, now + lifetime)
        )
        return secret

    def take(self, purpose, token):
        sig = digest(token)
        row = self.db.execute(
            "SELECT * FROM ws_tokens WHERE purpose=? AND digest=? AND expires>?",
            (purpose, sig, int(time.time())),
        ).fetchone()
        if not row:
            raise Denied("登录凭证已失效，请重新开始。", 401)
        self.db.execute("DELETE FROM ws_tokens WHERE digest=?", (sig,))
        return row["user_id"]

    def actor(self, token):
        with self.store.lock:
            return (
                user_view(
                    self.db.execute(
                        """SELECT u.* FROM ws_tokens t JOIN ws_users u ON u.id=t.user_id
                WHERE t.digest=? AND t.purpose='session' AND t.expires>? AND u.enabled=1""",
                        (digest(token), int(time.time())),
                    ).fetchone()
                )
                if token
                else user_view(None)
            )

    def authenticate(self, action, payload, current_token=""):
        name = str(payload.get("username") or "").strip()
        password = payload.get("password", "")
        with self.store.lock, self.db:
            if action == "logout":
                self.db.execute("DELETE FROM ws_tokens WHERE digest=?", (digest(current_token),))
                return {}, ""
            if action == "ticket":
                uid = self.take("ticket", str(payload.get("ticket", "")))
                row = self.db.execute("SELECT * FROM ws_users WHERE id=? AND enabled=1", (uid,)).fetchone()
                if not row:
                    raise Denied("账号已停用。")
                return {"user": user_view(row)}, self.token("session", uid, 8 * 3600)
            if not 2 <= len(name) <= 90 or name.startswith("@"):
                raise ValueError("请输入 2 至 90 个字符的账号名。")
            row = self.db.execute("SELECT * FROM ws_users WHERE username=?", (name,)).fetchone()
            recovery = None
            if action == "register":
                if row:
                    raise ValueError("账号名已使用。")
                recovery = secrets.token_urlsafe(20)
                uid = self.db.execute(
                    "INSERT INTO ws_users(username,password,recovery) VALUES(?,?,?)",
                    (name, valid_password(password), digest(recovery)),
                ).lastrowid
            elif action == "recover":
                if (
                    not row
                    or not row["enabled"]
                    or not hmac.compare_digest(row["recovery"], digest(str(payload.get("recovery") or "")))
                ):
                    raise Denied("账号或恢复码无效。", 401)
                uid = row["id"]
                recovery = secrets.token_urlsafe(20)
                self.db.execute(
                    "UPDATE ws_users SET password=?,recovery=? WHERE id=?",
                    (valid_password(password), digest(recovery), uid),
                )
                self.db.execute("DELETE FROM ws_tokens WHERE user_id=?", (uid,))
            elif action == "login":
                saved = row["password"] if row and row["password"] else "00" * 16 + "." + "00" * 32
                if (
                    not isinstance(password, str)
                    or len(password) > 180
                    or not hmac.compare_digest(encode_password(password, saved.split(".")[0]), saved)
                    or not row
                    or not row["enabled"]
                ):
                    raise Denied("账号或密码不正确，或账号已停用。", 401)
                uid = row["id"]
            else:
                raise ValueError("未知账号操作。")
            actor = user_view(self.db.execute("SELECT * FROM ws_users WHERE id=?", (uid,)).fetchone())
            self.audit(actor, {"register": "注册账号", "recover": "找回密码", "login": "登录"}[action])
            response = {"user": actor}
            if recovery:
                response["recovery"] = recovery
            return response, self.token("session", uid, 8 * 3600)

    def local_ticket(self):
        # Only invoked by a local OS user via scripts/open_platform_admin.py.
        with self.store.lock, self.db:
            self.db.execute("INSERT OR IGNORE INTO ws_users(username,role) VALUES('@localhost','admin')")
            uid = self.db.execute("SELECT id FROM ws_users WHERE username='@localhost'").fetchone()[0]
            return self.token("ticket", uid, 90)

    def set_role(self, actor, uid, role, enabled):
        require(actor, "admin")
        if role not in ROLES or not isinstance(enabled, bool):
            raise ValueError("用户角色或启用状态无效。")
        with self.store.lock, self.db:
            row = self.db.execute("SELECT * FROM ws_users WHERE id=?", (uid,)).fetchone()
            if not row or row["username"] == "@localhost" or uid == actor["id"]:
                raise ValueError("请选择其他可维护账号。")
            self.db.execute("UPDATE ws_users SET role=?,enabled=? WHERE id=?", (role, int(enabled), uid))
            self.db.execute("DELETE FROM ws_tokens WHERE user_id=?", (uid,))
            self.audit(actor, "修改用户角色", uid)

    @staticmethod
    def wechat_ready():
        callback = urlsplit(os.getenv("KB_WECHAT_CALLBACK", ""))
        return bool(
            os.getenv("KB_WECHAT_APP_ID")
            and os.getenv("KB_WECHAT_APP_SECRET")
            and callback.scheme == "https"
            and callback.netloc
            and callback.path == "/auth/wechat/callback"
        )

    def wechat_start(self):
        if not self.wechat_ready():
            raise ValueError("需配置微信开放平台网站应用和已登记的 HTTPS 回调地址。")
        with self.store.lock, self.db:
            state = self.token("wechat", None, 600)
        return "https://open.weixin.qq.com/connect/qrconnect?" + urlencode(
            {
                "appid": os.environ["KB_WECHAT_APP_ID"],
                "redirect_uri": os.environ["KB_WECHAT_CALLBACK"],
                "response_type": "code",
                "scope": "snsapi_login",
                "state": state,
            }
        ) + "#wechat_redirect", state

    def wechat_finish(self, state, browser_state, code):
        if not self.wechat_ready() or not state or not code or not hmac.compare_digest(state, browser_state):
            raise Denied("微信回调校验失败。")
        with self.store.lock, self.db:
            self.take("wechat", state)
        params = urlencode(
            {
                "appid": os.environ["KB_WECHAT_APP_ID"],
                "secret": os.environ["KB_WECHAT_APP_SECRET"],
                "code": code,
                "grant_type": "authorization_code",
            }
        )
        try:
            with urlopen(
                "https://api.weixin.qq.com/sns/oauth2/access_token?" + params, timeout=10
            ) as response:
                reply = json.loads(response.read(65536))
        except Exception:
            raise ValueError("微信开放平台暂未返回登录结果。") from None
        subject = reply.get("openid")
        if not subject or not reply.get("access_token"):
            raise Denied("微信授权码无效或已过期。")
        with self.store.lock, self.db:
            key = digest(os.environ["KB_WECHAT_APP_ID"] + ":" + subject)
            self.db.execute(
                "INSERT OR IGNORE INTO ws_users(username,wechat) VALUES(?,?)", ("@wx-" + key[:20], key)
            )
            row = self.db.execute("SELECT * FROM ws_users WHERE wechat=? AND enabled=1", (key,)).fetchone()
            if not row:
                raise Denied("账号已停用。")
            self.audit(user_view(row), "微信登录")
            return self.token("session", row["id"], 8 * 3600)
