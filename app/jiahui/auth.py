"""Local, server-owned identity for the isolated Jiahui profile.

Accounts are provisioned by a local operator, never by an unauthenticated API.
Passwords and session tokens are never stored in plaintext.
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import hashlib
import hmac
import os
import re
import secrets
import sqlite3
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fastapi import HTTPException, Request

COOKIE_NAME = "jiahui_session"
SESSION_SECONDS = 12 * 60 * 60
PASSWORD_ITERATIONS = 600_000
SCOPES = frozenset({"products", "knowledge", "prices", "inventory", "orders", "audit"})
ROLE_SCOPES = {
    role: frozenset({"products", "knowledge"})
    for role in ("sales", "procurement", "finance", "warehouse", "logistics")
}
ROLE_SCOPES["admin"] = SCOPES
_USERNAME = re.compile(r"^[\w.@-]{1,64}$", re.UNICODE)
_SCOPE = re.compile(r"^(?:[A-Za-z0-9:._-]{1,64}|\*)$")


@dataclass(frozen=True)
class Principal:
    username: str
    role: str
    scopes: frozenset[str]

    @property
    def user_id(self) -> str:
        return self.username

    def require(self, scope: str) -> None:
        if scope not in self.scopes and "*" not in self.scopes:
            raise HTTPException(status_code=403, detail="当前账号无权访问此类资料")

    def as_dict(self) -> dict[str, Any]:
        return {"username": self.username, "role": self.role, "scopes": sorted(self.scopes)}


def _password_hash(password: str, salt: bytes) -> str:
    return hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PASSWORD_ITERATIONS).hex()


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


class AuthStore:
    """Short-lived SQLite connections; account permissions are read on every request."""

    def __init__(self, data_dir: str | Path = "data/jiahui") -> None:
        self.directory = Path(data_dir)
        if not self.directory.exists():
            self.directory.mkdir(parents=True, mode=0o700)
        self.path = self.directory / "auth.sqlite3"
        existed = self.path.exists()
        with self._connect() as db:
            version = db.execute("PRAGMA user_version").fetchone()[0]
            if version > 1:
                raise RuntimeError("Unsupported Jiahui authentication database version")
            if version == 0:
                if existed:
                    self.backup()
                db.executescript("""
                    CREATE TABLE IF NOT EXISTS users (
                        username TEXT PRIMARY KEY, salt TEXT NOT NULL,
                        password_hash TEXT NOT NULL, role TEXT NOT NULL,
                        scopes TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1
                    );
                    CREATE TABLE IF NOT EXISTS sessions (
                        token_hash TEXT PRIMARY KEY, username TEXT NOT NULL,
                        expires_at REAL NOT NULL,
                        FOREIGN KEY(username) REFERENCES users(username)
                    );
                    CREATE TABLE IF NOT EXISTS login_attempts (
                        bucket TEXT PRIMARY KEY, started_at REAL NOT NULL,
                        failures INTEGER NOT NULL
                    );
                    PRAGMA user_version = 1;
                """)
        if not existed:
            os.chmod(self.path, 0o600)

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys = ON")
        return db

    def backup(self) -> Path:
        """Consistent SQLite backup before any operator account mutation."""
        target = self.directory / "backups"
        target.mkdir(exist_ok=True, mode=0o700)
        path = target / f"auth-{time.time_ns()}.sqlite3"
        with sqlite3.connect(self.path) as source, sqlite3.connect(path) as dest:
            source.backup(dest)
        os.chmod(path, 0o600)
        return path

    def has_users(self) -> bool:
        with self._connect() as db:
            return db.execute("SELECT 1 FROM users LIMIT 1").fetchone() is not None

    @staticmethod
    def _validate_access(role: str, scopes: frozenset[str]) -> None:
        if role not in ROLE_SCOPES:
            raise ValueError("未知岗位；使用 sales/procurement/finance/warehouse/logistics/admin")
        invalid = {scope for scope in scopes if not _SCOPE.fullmatch(scope)}
        if invalid:
            raise ValueError("无效数据范围标签：" + ",".join(sorted(invalid)))

    def create_user(
        self, username: str, password: str, role: str, scopes: frozenset[str] | None = None
    ) -> bool:
        """Idempotent provisioning: existing accounts are never overwritten."""
        access = ROLE_SCOPES.get(role, frozenset()) if scopes is None else scopes
        self._validate_access(role, access)
        if not _USERNAME.fullmatch(username):
            raise ValueError("账号须为1至64位字母、数字、中文或 . @ _ -")
        if not 12 <= len(password) <= 1024:
            raise ValueError("密码至少12位，最多1024位")
        with self._connect() as db:
            if db.execute("SELECT 1 FROM users WHERE username = ?", (username,)).fetchone():
                return False
        self.backup()
        salt = secrets.token_bytes(16)
        with self._connect() as db:
            result = db.execute(
                "INSERT OR IGNORE INTO users(username,salt,password_hash,role,scopes) VALUES(?,?,?,?,?)",
                (
                    username,
                    salt.hex(),
                    _password_hash(password, salt),
                    role,
                    ",".join(sorted(access)),
                ),
            )
            return result.rowcount == 1

    def set_access(
        self, username: str, role: str, scopes: frozenset[str], active: bool = True
    ) -> None:
        self._validate_access(role, scopes)
        self.backup()
        with self._connect() as db:
            cursor = db.execute(
                "UPDATE users SET role=?,scopes=?,active=? WHERE username=?",
                (role, ",".join(sorted(scopes)), int(active), username),
            )
            if cursor.rowcount != 1:
                raise ValueError("账号不存在")

    def login(self, username: str, password: str, client: str) -> str:
        now = time.time()
        buckets = [("user:" + username, 8), ("client:" + client, 40)]
        # Reserve an attempt before the expensive password check, including concurrent requests.
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            for raw, limit in buckets:
                key = _token_hash(raw)
                row = db.execute("SELECT * FROM login_attempts WHERE bucket=?", (key,)).fetchone()
                if row and row["started_at"] + 900 > now and row["failures"] >= limit:
                    raise HTTPException(status_code=429, detail="登录尝试过多，请15分钟后重试")
                count = row["failures"] + 1 if row and row["started_at"] + 900 > now else 1
                started = row["started_at"] if row and row["started_at"] + 900 > now else now
                db.execute(
                    "INSERT OR REPLACE INTO login_attempts VALUES(?,?,?)", (key, started, count)
                )
            row = db.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
        salt = bytes.fromhex(row["salt"]) if row else bytes(16)
        expected = row["password_hash"] if row else "0" * 64
        valid = hmac.compare_digest(_password_hash(password, salt), expected)
        if not row or not valid or not row["active"]:
            raise HTTPException(status_code=401, detail="账号或密码错误")
        token = secrets.token_urlsafe(32)
        with self._connect() as db:
            db.execute("DELETE FROM sessions WHERE expires_at <= ?", (now,))
            db.execute(
                "DELETE FROM login_attempts WHERE bucket=?", (_token_hash("user:" + username),)
            )
            db.execute(
                "INSERT INTO sessions VALUES(?,?,?)",
                (_token_hash(token), username, now + SESSION_SECONDS),
            )
        return token

    def principal(self, token: str) -> Principal:
        if not token or len(token) > 256:
            raise HTTPException(status_code=401, detail="请先登录嘉惠内部业务助手")
        with self._connect() as db:
            row = db.execute(
                "SELECT u.username,u.role,u.scopes FROM sessions s JOIN users u USING(username) "
                "WHERE s.token_hash=? AND s.expires_at>? AND u.active=1",
                (_token_hash(token), time.time()),
            ).fetchone()
        if row is None:
            raise HTTPException(status_code=401, detail="登录已失效，请重新登录")
        return Principal(
            row["username"], row["role"], frozenset(filter(None, row["scopes"].split(",")))
        )

    def logout(self, token: str) -> None:
        with self._connect() as db:
            db.execute("DELETE FROM sessions WHERE token_hash=?", (_token_hash(token),))


def auth_store(request: Request) -> AuthStore:
    settings = request.app.state.container.settings
    return AuthStore(getattr(settings, "jiahui_data_dir", "data/jiahui"))


def session_token(request: Request) -> str:
    authorization = request.headers.get("authorization", "")
    if authorization.startswith("Bearer "):
        return authorization[7:]
    return request.cookies.get(COOKIE_NAME, "")


def check_same_origin(request: Request) -> None:
    """Reject browser cross-origin mutation, including localhost cross-port attacks."""
    origin = request.headers.get("origin")
    expected = str(request.base_url).rstrip("/")
    if origin and origin.rstrip("/") != expected:
        raise HTTPException(status_code=403, detail="不允许跨站请求")
    if request.headers.get("sec-fetch-site") == "cross-site":
        raise HTTPException(status_code=403, detail="不允许跨站请求")


async def current_principal(request: Request) -> Principal:
    settings = request.app.state.container.settings
    if getattr(settings, "business_profile", "ecommerce") != "jiahui":
        return Principal("anonymous", "demo", frozenset())
    if request.method not in {"GET", "HEAD", "OPTIONS"}:
        check_same_origin(request)
    principal = await asyncio.to_thread(auth_store(request).principal, session_token(request))
    request.state.principal = principal
    return principal


def main() -> None:
    parser = argparse.ArgumentParser(description="嘉惠账号管理；密码仅通过隐藏输入获取")
    parser.add_argument("command", choices=["create-user", "set-access"])
    parser.add_argument("--username", required=True)
    parser.add_argument("--role", choices=sorted(ROLE_SCOPES), required=True)
    parser.add_argument(
        "--scopes", help="逗号分隔的权限标签；默认标签：" + ",".join(sorted(SCOPES))
    )
    parser.add_argument("--data-dir")
    parser.add_argument("--disable", action="store_true", help="set-access 时禁用账号")
    args = parser.parse_args()
    if args.data_dir is None:
        from app.config.settings import get_settings

        args.data_dir = getattr(get_settings(), "jiahui_data_dir", "data/jiahui")
    access = (
        ROLE_SCOPES[args.role]
        if args.scopes is None
        else frozenset(filter(None, args.scopes.split(",")))
    )
    store = AuthStore(args.data_dir)
    try:
        if args.command == "create-user":
            with store._connect() as db:
                if db.execute("SELECT 1 FROM users WHERE username=?", (args.username,)).fetchone():
                    print("账号已存在，保持原密码和权限不变。")
                    return
            if not sys.stdin.isatty():
                parser.error("请在交互终端初始化账号，密码必须通过隐藏输入获取")
            password = getpass.getpass("新密码（至少12位）：")
            if password != getpass.getpass("再次输入新密码："):
                parser.error("两次密码不一致")
            created = store.create_user(args.username, password, args.role, access)
            print("账号已创建。" if created else "账号已存在，未覆盖。")
        else:
            store.set_access(args.username, args.role, access, not args.disable)
            print("岗位/权限已更新，下一次请求立即生效。")
    except ValueError as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    main()
