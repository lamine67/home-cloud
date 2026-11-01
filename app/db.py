"""SQLite: схема, подключение и создание первого администратора."""
from __future__ import annotations

import contextlib
import re
import sqlite3
from datetime import datetime, timezone

import bcrypt

from .config import ConfigError, Settings

USERNAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{2,31}$")
MIN_PASSWORD_LEN = 8

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT NOT NULL UNIQUE COLLATE NOCASE,
    password_hash TEXT NOT NULL,
    is_admin INTEGER NOT NULL DEFAULT 0,
    quota_gb INTEGER NOT NULL DEFAULT 0,
    disabled INTEGER NOT NULL DEFAULT 0,
    cloud_readonly INTEGER NOT NULL DEFAULT 0,
    cloud_can_share INTEGER NOT NULL DEFAULT 1,
    totp_secret TEXT,
    totp_enabled INTEGER NOT NULL DEFAULT 0,
    token_version INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    last_login_at TEXT
);

CREATE TABLE IF NOT EXISTS shortcuts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    path TEXT NOT NULL,
    space TEXT NOT NULL DEFAULT 'common',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS shares (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    owner_username TEXT NOT NULL COLLATE NOCASE,
    path TEXT NOT NULL,
    shared_with TEXT COLLATE NOCASE,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_shares_owner ON shares(owner_username, path);
"""

def utcnow() -> str:
    return datetime.now(timezone.utc).replace(tzinfo=None).isoformat(timespec="seconds")

def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()

def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode(), password_hash.encode())
    except ValueError:
        return False

# Хэш для «холостой» проверки, чтобы время ответа не выдавало существование логина
DUMMY_HASH = hash_password("not-a-real-password")

class Database:
    def __init__(self, path):
        self.path = path

    @contextlib.contextmanager
    def connect(self):
        conn = sqlite3.connect(self.path, timeout=15)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def init(self, settings: Settings) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as conn:
            conn.execute("PRAGMA journal_mode = WAL")
            conn.executescript(SCHEMA)
            count = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
            if count == 0:
                self._bootstrap_admin(conn, settings)

    @staticmethod
    def _bootstrap_admin(conn, settings: Settings) -> None:
        user, password = settings.admin_user, settings.admin_password
        if not user or not password:
            raise ConfigError(
                "Пользователей ещё нет. Задайте ADMIN_USER и ADMIN_PASSWORD для первого запуска "
                "(после создания администратора ADMIN_PASSWORD из окружения можно удалить)."
            )
        if not USERNAME_RE.match(user):
            raise ConfigError("ADMIN_USER: 3-32 символа, латиница, цифры, точка, дефис, подчёркивание")
        if len(password) < MIN_PASSWORD_LEN:
            raise ConfigError(f"ADMIN_PASSWORD должен быть не короче {MIN_PASSWORD_LEN} символов")
        conn.execute(
            "INSERT INTO users (username, password_hash, is_admin, created_at) VALUES (?, ?, 1, ?)",
            (user, hash_password(password), utcnow()),
        )
