"""Работа с путями и размерами: защита от выхода за пределы корня, подсчёт места."""
from __future__ import annotations

import os
import time
from pathlib import Path

from fastapi import HTTPException

from .config import Settings

def human_size(num: float) -> str:
    for unit in ("Б", "КБ", "МБ", "ГБ", "ТБ"):
        if num < 1024:
            return f"{num:.0f} {unit}" if unit == "Б" else f"{num:.1f} {unit}"
        num /= 1024
    return f"{num:.1f} ПБ"

def safe_join(root: Path, rel_path: str) -> Path:
    """Возвращает абсолютный путь внутри `root` или бросает 400."""
    rel = (rel_path or "").replace("\\", "/").strip("/")
    if "\x00" in rel:
        raise HTTPException(status_code=400, detail="Некорректный путь")
    root_resolved = root.resolve()
    target = (root_resolved / rel).resolve()
    if target != root_resolved and root_resolved not in target.parents:
        raise HTTPException(status_code=400, detail="Некорректный путь")
    return target

_WINDOWS_FORBIDDEN = set('<>:"|?*')

def valid_name(name: str) -> bool:
    """Допустимое имя одного файла/папки (без разделителей, не `.`/`..`, без управляющих символов)."""
    if not name or len(name) > 255 or name in (".", "..") or "/" in name or "\\" in name:
        return False
    if any(ord(ch) < 32 for ch in name):
        return False
    if os.name == "nt" and any(ch in _WINDOWS_FORBIDDEN for ch in name):
        return False
    return True

def user_root(settings: Settings, username: str) -> Path:
    root = settings.personal_dir / username
    root.mkdir(parents=True, exist_ok=True)
    return root

def dir_size_bytes(path: Path) -> int:
    total = 0
    if not path.exists():
        return 0
    for current, _dirs, files in os.walk(path):
        for name in files:
            try:
                total += (Path(current) / name).stat().st_size
            except OSError:
                pass
    return total

def cached_dir_size(ctx, key: str, path: Path, ttl: float = 20.0) -> int:
    """Размер каталога с короткоживущим кэшем (чтобы не обходить дерево на каждый запрос)."""
    now = time.monotonic()
    hit = ctx.usage_cache.get(key)
    if hit and now - hit[0] < ttl:
        return hit[1]
    size = dir_size_bytes(path)
    ctx.usage_cache[key] = (now, size)
    return size

def invalidate_usage(ctx, key: str) -> None:
    ctx.usage_cache.pop(key, None)
