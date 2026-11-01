"""Вход, выход, 2FA (TOTP), смена пароля и управление пользователями."""
from __future__ import annotations

import shutil
from typing import Optional

import pyotp
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from itsdangerous import BadSignature
from pydantic import BaseModel, Field

from .db import DUMMY_HASH, MIN_PASSWORD_LEN, USERNAME_RE, hash_password, utcnow, verify_password
from .deps import Context, client_ip, get_ctx, require_admin, require_user
from .storage import cached_dir_size, dir_size_bytes, human_size, user_root

router = APIRouter(prefix="/api")

TOTP_PENDING_MAX_AGE = 300  # 5 минут на ввод кода после пароля
DEVICE_TRUST_DAYS = 30

# сессии и cookie

def _set_cookie(ctx: Context, resp, name: str, value: str, max_age: int) -> None:
    resp.set_cookie(name, value, httponly=True, secure=ctx.settings.cookie_secure, samesite="lax", max_age=max_age)

def issue_session_response(ctx: Context, user, *, totp_passed: bool = False) -> JSONResponse:
    with ctx.db.connect() as conn:
        conn.execute("UPDATE users SET last_login_at = ? WHERE id = ?", (utcnow(), user["id"]))
    resp = JSONResponse({"ok": True, "username": user["username"], "is_admin": bool(user["is_admin"])})
    token = ctx.serializer.dumps({"u": user["username"], "v": user["token_version"]}, salt="session")
    _set_cookie(ctx, resp, "session", token, ctx.settings.session_days * 86400)
    if user["totp_enabled"]:
        if ctx.settings.allow_quick_login:
            trust = ctx.serializer.dumps({"u": user["username"], "v": user["token_version"]}, salt="device")
            _set_cookie(ctx, resp, "device_trust", trust, DEVICE_TRUST_DAYS * 86400)
        if totp_passed and ctx.settings.totp_trust_days:
            trust = ctx.serializer.dumps({"u": user["username"], "v": user["token_version"]}, salt="totp_trust")
            _set_cookie(ctx, resp, "totp_trust", trust, ctx.settings.totp_trust_days * 86400)
    return resp

def _totp_trusted(ctx: Context, request: Request, user) -> bool:
    if not ctx.settings.totp_trust_days:
        return False
    token = request.cookies.get("totp_trust")
    if not token:
        return False
    try:
        payload = ctx.serializer.loads(token, salt="totp_trust", max_age=ctx.settings.totp_trust_days * 86400)
    except BadSignature:
        return False
    return payload.get("u") == user["username"] and payload.get("v") == user["token_version"]

def _get_user(ctx: Context, username: str):
    with ctx.db.connect() as conn:
        return conn.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()

def _check_totp(user, code: str) -> bool:
    if not user or not user["totp_enabled"] or not user["totp_secret"]:
        return False
    return bool(pyotp.TOTP(user["totp_secret"]).verify(str(code).strip(), valid_window=1))

# вход / выход

class LoginIn(BaseModel):
    username: str = Field(max_length=64)
    password: str = Field(max_length=256)

class TotpIn(BaseModel):
    pending_token: str
    code: str = Field(max_length=16)

class CodeIn(BaseModel):
    code: str = Field(max_length=16)

@router.post("/login")
def api_login(data: LoginIn, request: Request):
    ctx = get_ctx(request)
    username = data.username.strip()
    key = f"{client_ip(request)}|{username.lower()}"
    ctx.limiter.check(key)
    user = _get_user(ctx, username)
    ok = verify_password(data.password, user["password_hash"] if user else DUMMY_HASH)
    if not user or not ok:
        ctx.limiter.fail(key)
        raise HTTPException(status_code=401, detail="Неверный логин или пароль")
    if user["disabled"]:
        raise HTTPException(status_code=403, detail="Аккаунт отключён администратором")
    ctx.limiter.reset(key)
    if user["totp_enabled"] and not _totp_trusted(ctx, request, user):
        pending = ctx.serializer.dumps({"u": user["username"], "v": user["token_version"]}, salt="2fa_pending")
        return {"ok": True, "requires_totp": True, "pending_token": pending}
    return issue_session_response(ctx, user)

@router.post("/login/totp")
def api_login_totp(data: TotpIn, request: Request):
    ctx = get_ctx(request)
    key = f"{client_ip(request)}|totp"
    ctx.limiter.check(key)
    try:
        payload = ctx.serializer.loads(data.pending_token, salt="2fa_pending", max_age=TOTP_PENDING_MAX_AGE)
    except BadSignature:
        raise HTTPException(status_code=401, detail="Сессия входа истекла, начните заново")
    user = _get_user(ctx, payload["u"])
    if not user or user["disabled"] or user["token_version"] != payload.get("v"):
        raise HTTPException(status_code=401, detail="Сессия входа недействительна")
    if not _check_totp(user, data.code):
        ctx.limiter.fail(key)
        raise HTTPException(status_code=401, detail="Неверный код")
    ctx.limiter.reset(key)
    return issue_session_response(ctx, user, totp_passed=True)

@router.get("/login/quick-check")
def api_login_quick_check(request: Request):
    """«Быстрый вход»: на устройстве, где уже входили с 2FA, достаточно кода из приложения."""
    ctx = get_ctx(request)
    if not ctx.settings.allow_quick_login:
        return {"available": False}
    token = request.cookies.get("device_trust")
    if not token:
        return {"available": False}
    try:
        payload = ctx.serializer.loads(token, salt="device", max_age=DEVICE_TRUST_DAYS * 86400)
    except BadSignature:
        return {"available": False}
    user = _get_user(ctx, payload["u"])
    if not user or user["disabled"] or not user["totp_enabled"] or user["token_version"] != payload.get("v"):
        return {"available": False}
    return {"available": True, "username": user["username"]}

@router.post("/login/quick")
def api_login_quick(data: CodeIn, request: Request):
    ctx = get_ctx(request)
    if not ctx.settings.allow_quick_login:
        raise HTTPException(status_code=403, detail="Быстрый вход отключён")
    key = f"{client_ip(request)}|quick"
    ctx.limiter.check(key)
    token = request.cookies.get("device_trust")
    try:
        payload = ctx.serializer.loads(token or "", salt="device", max_age=DEVICE_TRUST_DAYS * 86400)
    except BadSignature:
        raise HTTPException(status_code=401, detail="Устройство не доверенное, войдите с паролем")
    user = _get_user(ctx, payload["u"])
    if not user or user["disabled"] or user["token_version"] != payload.get("v") or not _check_totp(user, data.code):
        ctx.limiter.fail(key)
        raise HTTPException(status_code=401, detail="Неверный код")
    ctx.limiter.reset(key)
    return issue_session_response(ctx, user, totp_passed=True)

@router.post("/logout")
def api_logout():
    resp = JSONResponse({"ok": True})
    resp.delete_cookie("session")
    return resp

# профиль

@router.get("/me")
def api_me(request: Request, user=Depends(require_user)):
    ctx = get_ctx(request)
    used = cached_dir_size(ctx, f"u:{user['username']}", user_root(ctx.settings, user["username"]))
    mode = ctx.settings.shared_write
    shared_can_write = (mode == "users") or (mode == "admin" and bool(user["is_admin"]))
    return {
        "username": user["username"],
        "is_admin": bool(user["is_admin"]),
        "totp_enabled": bool(user["totp_enabled"]),
        "cloud_readonly": bool(user["cloud_readonly"]),
        "cloud_can_share": bool(user["cloud_can_share"]),
        "quota_gb": user["quota_gb"],
        "used_bytes": used,
        "used_human": human_size(used),
        "shared_can_write": shared_can_write and not user["cloud_readonly"],
        "can_convert": shutil.which("ffmpeg") is not None,
        "app_name": ctx.settings.app_name,
    }

class PasswordIn(BaseModel):
    current_password: str = Field(max_length=256)
    new_password: str = Field(max_length=256)

@router.post("/me/password")
def api_change_password(data: PasswordIn, request: Request, user=Depends(require_user)):
    ctx = get_ctx(request)
    if not verify_password(data.current_password, user["password_hash"]):
        raise HTTPException(status_code=400, detail="Текущий пароль указан неверно")
    if len(data.new_password) < MIN_PASSWORD_LEN:
        raise HTTPException(status_code=400, detail=f"Новый пароль: не короче {MIN_PASSWORD_LEN} символов")
    with ctx.db.connect() as conn:
        conn.execute(
            "UPDATE users SET password_hash = ?, token_version = token_version + 1 WHERE id = ?",
            (hash_password(data.new_password), user["id"]),
        )
    fresh = _get_user(ctx, user["username"])
    return issue_session_response(ctx, fresh)

@router.get("/2fa/status")
def api_2fa_status(user=Depends(require_user)):
    return {"enabled": bool(user["totp_enabled"])}

@router.post("/2fa/setup")
def api_2fa_setup(request: Request, user=Depends(require_user)):
    ctx = get_ctx(request)
    if user["totp_enabled"]:
        raise HTTPException(status_code=400, detail="Двухфакторная аутентификация уже включена")
    secret = pyotp.random_base32()
    with ctx.db.connect() as conn:
        conn.execute("UPDATE users SET totp_secret = ? WHERE id = ?", (secret, user["id"]))
    url = pyotp.TOTP(secret).provisioning_uri(name=user["username"], issuer_name=ctx.settings.app_name)
    return {"secret": secret, "otpauth_url": url}

@router.post("/2fa/enable")
def api_2fa_enable(data: CodeIn, request: Request, user=Depends(require_user)):
    ctx = get_ctx(request)
    if user["totp_enabled"]:
        raise HTTPException(status_code=400, detail="Уже включена")
    if not user["totp_secret"]:
        raise HTTPException(status_code=400, detail="Сначала начните настройку (/api/2fa/setup)")
    if not pyotp.TOTP(user["totp_secret"]).verify(data.code.strip(), valid_window=1):
        raise HTTPException(status_code=400, detail="Неверный код")
    with ctx.db.connect() as conn:
        conn.execute("UPDATE users SET totp_enabled = 1 WHERE id = ?", (user["id"],))
    return {"ok": True}

@router.post("/2fa/disable")
def api_2fa_disable(data: CodeIn, request: Request, user=Depends(require_user)):
    ctx = get_ctx(request)
    if not _check_totp(user, data.code):
        raise HTTPException(status_code=400, detail="Неверный код или 2FA не включена")
    with ctx.db.connect() as conn:
        conn.execute("UPDATE users SET totp_enabled = 0, totp_secret = NULL, token_version = token_version + 1 WHERE id = ?", (user["id"],))
    fresh = _get_user(ctx, user["username"])
    return issue_session_response(ctx, fresh)

# пользователи (администратор)

class UserCreate(BaseModel):
    username: str
    password: str = Field(max_length=256)
    is_admin: bool = False
    quota_gb: int = Field(0, ge=0, le=1_000_000)
    cloud_readonly: bool = False
    cloud_can_share: bool = True

class UserPatch(BaseModel):
    is_admin: Optional[bool] = None
    quota_gb: Optional[int] = Field(None, ge=0, le=1_000_000)
    disabled: Optional[bool] = None
    cloud_readonly: Optional[bool] = None
    cloud_can_share: Optional[bool] = None
    password: Optional[str] = Field(None, max_length=256)

def _admins_left(conn, excluding_id: int) -> int:
    return conn.execute(
        "SELECT COUNT(*) FROM users WHERE is_admin = 1 AND disabled = 0 AND id != ?", (excluding_id,)
    ).fetchone()[0]

@router.get("/users")
def list_users(request: Request, _admin=Depends(require_admin)):
    ctx = get_ctx(request)
    with ctx.db.connect() as conn:
        rows = conn.execute("SELECT * FROM users ORDER BY created_at, id").fetchall()
    users = []
    for r in rows:
        used = dir_size_bytes(ctx.settings.personal_dir / r["username"])
        users.append({
            "username": r["username"],
            "is_admin": bool(r["is_admin"]),
            "disabled": bool(r["disabled"]),
            "quota_gb": r["quota_gb"],
            "used_bytes": used,
            "used_human": human_size(used),
            "totp_enabled": bool(r["totp_enabled"]),
            "cloud_readonly": bool(r["cloud_readonly"]),
            "cloud_can_share": bool(r["cloud_can_share"]),
            "created_at": r["created_at"],
            "last_login_at": r["last_login_at"],
        })
    return {"users": users}

@router.post("/users")
def create_user(data: UserCreate, request: Request, _admin=Depends(require_admin)):
    ctx = get_ctx(request)
    username = data.username.strip()
    if not USERNAME_RE.match(username):
        raise HTTPException(status_code=400, detail="Логин: 3-32 символа, латиница, цифры, точка, дефис, подчёркивание")
    if len(data.password) < MIN_PASSWORD_LEN:
        raise HTTPException(status_code=400, detail=f"Пароль: не короче {MIN_PASSWORD_LEN} символов")
    with ctx.db.connect() as conn:
        if conn.execute("SELECT 1 FROM users WHERE username = ?", (username,)).fetchone():
            raise HTTPException(status_code=400, detail="Такой логин уже существует")
        conn.execute(
            "INSERT INTO users (username, password_hash, is_admin, quota_gb, cloud_readonly, cloud_can_share, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (username, hash_password(data.password), int(data.is_admin), data.quota_gb,
             int(data.cloud_readonly), int(data.cloud_can_share), utcnow()),
        )
    user_root(ctx.settings, username)
    return {"ok": True}

@router.patch("/users/{username}")
def patch_user(username: str, data: UserPatch, request: Request, admin=Depends(require_admin)):
    ctx = get_ctx(request)
    target = _get_user(ctx, username)
    if not target:
        raise HTTPException(status_code=404, detail="Пользователь не найден")
    is_self = target["id"] == admin["id"]
    fields = data.model_dump(exclude_unset=True)
    with ctx.db.connect() as conn:
        if is_self and (fields.get("disabled") is True or fields.get("is_admin") is False):
            raise HTTPException(status_code=400, detail="Нельзя отключить или разжаловать самого себя")
        if (fields.get("is_admin") is False or fields.get("disabled") is True) and target["is_admin"] \
                and _admins_left(conn, target["id"]) == 0:
            raise HTTPException(status_code=400, detail="Нельзя оставить систему без активного администратора")
        sets, args = [], []
        for col in ("is_admin", "disabled", "cloud_readonly", "cloud_can_share"):
            if col in fields and fields[col] is not None:
                sets.append(f"{col} = ?")
                args.append(int(fields[col]))
        if fields.get("quota_gb") is not None:
            sets.append("quota_gb = ?")
            args.append(fields["quota_gb"])
        bump = fields.get("disabled") is True
        if fields.get("password"):
            if len(fields["password"]) < MIN_PASSWORD_LEN:
                raise HTTPException(status_code=400, detail=f"Пароль: не короче {MIN_PASSWORD_LEN} символов")
            sets.append("password_hash = ?")
            args.append(hash_password(fields["password"]))
            bump = True
        if bump:
            sets.append("token_version = token_version + 1")
        if sets:
            conn.execute(f"UPDATE users SET {', '.join(sets)} WHERE id = ?", (*args, target["id"]))
    return {"ok": True}

@router.post("/users/{username}/reset-2fa")
def reset_2fa(username: str, request: Request, _admin=Depends(require_admin)):
    ctx = get_ctx(request)
    with ctx.db.connect() as conn:
        cur = conn.execute(
            "UPDATE users SET totp_enabled = 0, totp_secret = NULL, token_version = token_version + 1 WHERE username = ?",
            (username,),
        )
    if not cur.rowcount:
        raise HTTPException(status_code=404, detail="Пользователь не найден")
    return {"ok": True}

@router.delete("/users/{username}")
def delete_user(username: str, request: Request, purge: bool = False, admin=Depends(require_admin)):
    ctx = get_ctx(request)
    target = _get_user(ctx, username)
    if not target:
        raise HTTPException(status_code=404, detail="Пользователь не найден")
    if target["id"] == admin["id"]:
        raise HTTPException(status_code=400, detail="Нельзя удалить самого себя")
    with ctx.db.connect() as conn:
        if target["is_admin"] and _admins_left(conn, target["id"]) == 0:
            raise HTTPException(status_code=400, detail="Нельзя удалить последнего администратора")
        conn.execute("DELETE FROM shares WHERE owner_username = ? OR shared_with = ?", (target["username"], target["username"]))
        conn.execute("DELETE FROM users WHERE id = ?", (target["id"],))
    if purge:
        folder = ctx.settings.personal_dir / target["username"]
        if USERNAME_RE.match(target["username"]) and folder.is_dir():
            shutil.rmtree(folder, ignore_errors=True)
    return {"ok": True, "files_kept": not purge}
