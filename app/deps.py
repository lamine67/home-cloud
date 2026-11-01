"""Общий контекст приложения и зависимости FastAPI (авторизация, права)."""
from __future__ import annotations

import time
from collections import defaultdict, deque
from dataclasses import dataclass, field

from fastapi import Depends, HTTPException, Request
from itsdangerous import BadSignature, URLSafeTimedSerializer

from .config import Settings
from .db import Database

class RateLimiter:
    """Простое ограничение числа неудачных попыток входа (в памяти процесса)."""

    def __init__(self, max_attempts: int, window_sec: int):
        self.max_attempts = max_attempts
        self.window = window_sec
        self._fails: dict[str, deque] = defaultdict(deque)

    def _trim(self, key: str) -> deque:
        q = self._fails[key]
        now = time.monotonic()
        while q and now - q[0] > self.window:
            q.popleft()
        return q

    def check(self, key: str) -> None:
        if len(self._trim(key)) >= self.max_attempts:
            raise HTTPException(status_code=429, detail="Слишком много неудачных попыток. Подождите несколько минут.")

    def fail(self, key: str) -> None:
        self._trim(key).append(time.monotonic())

    def reset(self, key: str) -> None:
        self._fails.pop(key, None)

@dataclass
class Context:
    settings: Settings
    db: Database
    serializer: URLSafeTimedSerializer
    limiter: RateLimiter
    usage_cache: dict = field(default_factory=dict)

def get_ctx(request: Request) -> Context:
    return request.app.state.ctx

def client_ip(request: Request) -> str:
    ctx = get_ctx(request)
    if ctx.settings.trust_proxy:
        fwd = request.headers.get("x-forwarded-for", "")
        if fwd:
            return fwd.split(",")[0].strip()
    return request.client.host if request.client else "unknown"

def _load_session(ctx: Context, token: str | None):
    if not token:
        return None
    try:
        payload = ctx.serializer.loads(token, salt="session", max_age=ctx.settings.session_days * 86400)
    except BadSignature:
        return None
    if not isinstance(payload, dict) or "u" not in payload:
        return None
    with ctx.db.connect() as conn:
        user = conn.execute("SELECT * FROM users WHERE username = ?", (payload["u"],)).fetchone()
    if not user or user["disabled"] or user["token_version"] != payload.get("v"):
        return None
    return user

def get_current_user(request: Request):
    return _load_session(get_ctx(request), request.cookies.get("session"))

def require_user(request: Request):
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Не авторизован")
    return user

def require_admin(user=Depends(require_user)):
    if not user["is_admin"]:
        raise HTTPException(status_code=403, detail="Только для администратора")
    return user

def require_writable_user(user=Depends(require_user)):
    """Запись в личные папки: аккаунты «только чтение» не могут менять файлы."""
    if user["cloud_readonly"]:
        raise HTTPException(status_code=403, detail="Аккаунт в режиме «только чтение»")
    return user

def require_shared_writer(request: Request, user=Depends(require_user)):
    """Запись в общую папку: определяется настройкой SHARED_WRITE."""
    mode = get_ctx(request).settings.shared_write
    if mode == "none":
        raise HTTPException(status_code=403, detail="Общая папка доступна только для чтения")
    if mode == "admin" and not user["is_admin"]:
        raise HTTPException(status_code=403, detail="В общей папке изменения может вносить только администратор")
    if user["cloud_readonly"]:
        raise HTTPException(status_code=403, detail="Аккаунт в режиме «только чтение»")
    return user
