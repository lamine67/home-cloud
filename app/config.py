"""Настройки приложения. Всё берётся из переменных окружения (см. .env.example)."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

_PLACEHOLDER_PREFIXES = ("change-me", "changeme", "replace-me", "your-", "example")

class ConfigError(RuntimeError):
    """Неверная или неполная конфигурация."""

def load_dotenv(path: Path | None = None) -> None:
    """Подхватывает KEY=VALUE из .env рядом с проектом (переменные окружения имеют приоритет)."""
    path = path or (BASE_DIR / ".env")
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        os.environ.setdefault(key.strip(), value)

def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")

def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} должно быть целым числом, получено: {raw!r}") from exc

@dataclass(frozen=True)
class Settings:
    secret_key: str
    data_dir: Path
    storage_dir: Path
    personal_dir: Path
    tmp_dir: Path
    db_path: Path
    admin_user: str
    admin_password: str
    app_name: str
    cookie_secure: bool
    session_days: int
    allow_quick_login: bool
    totp_trust_days: int
    trust_proxy: bool
    shared_write: str  # admin | users | none
    zip_max_file_bytes: int
    extract_max_bytes: int
    login_max_attempts: int
    login_window_sec: int

def load_settings() -> Settings:
    load_dotenv()
    secret = os.environ.get("SECRET_KEY", "").strip()
    if not secret:
        raise ConfigError(
            "Не задан SECRET_KEY. Сгенерируйте: python scripts/init_env.py "
            "или python -c \"import secrets; print(secrets.token_urlsafe(48))\""
        )
    if len(secret) < 32 or secret.lower().startswith(_PLACEHOLDER_PREFIXES):
        raise ConfigError("SECRET_KEY слишком короткий (нужно от 32 символов) или остался значением-заглушкой из .env.example")

    data_dir = Path(os.environ.get("DATA_DIR") or (BASE_DIR / "data")).resolve()
    storage_dir = Path(os.environ.get("STORAGE_DIR") or (data_dir / "storage")).resolve()
    personal_dir = Path(os.environ.get("PERSONAL_DIR") or (data_dir / "personal")).resolve()

    shared_write = (os.environ.get("SHARED_WRITE") or "admin").strip().lower()
    if shared_write not in ("admin", "users", "none"):
        raise ConfigError("SHARED_WRITE: допустимо admin, users или none")

    return Settings(
        secret_key=secret,
        data_dir=data_dir,
        storage_dir=storage_dir,
        personal_dir=personal_dir,
        tmp_dir=data_dir / "tmp",
        db_path=data_dir / "app.db",
        admin_user=(os.environ.get("ADMIN_USER") or "").strip(),
        admin_password=os.environ.get("ADMIN_PASSWORD") or "",
        app_name=(os.environ.get("APP_NAME") or "Домашний Диск").strip(),
        cookie_secure=_env_bool("COOKIE_SECURE", True),
        session_days=max(1, _env_int("SESSION_DAYS", 30)),
        allow_quick_login=_env_bool("ALLOW_QUICK_LOGIN", False),
        totp_trust_days=max(0, _env_int("TOTP_TRUST_DAYS", 0)),
        trust_proxy=_env_bool("TRUST_PROXY", False),
        shared_write=shared_write,
        zip_max_file_bytes=max(1, _env_int("ZIP_MAX_FILE_MB", 300)) * 1024 * 1024,
        extract_max_bytes=max(1, _env_int("EXTRACT_MAX_MB", 4096)) * 1024 * 1024,
        login_max_attempts=max(1, _env_int("LOGIN_MAX_ATTEMPTS", 8)),
        login_window_sec=max(30, _env_int("LOGIN_WINDOW_SEC", 600)),
    )
