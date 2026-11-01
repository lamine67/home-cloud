"""Файловый менеджер: общая папка и личное хранилище, архивы, общий доступ, ярлыки."""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Callable, List, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from starlette.background import BackgroundTask

from .db import utcnow
from .deps import (Context, get_ctx, require_shared_writer, require_user, require_writable_user)
from .storage import (cached_dir_size, dir_size_bytes, human_size, invalidate_usage, safe_join,
                      user_root, valid_name)

VIDEO_EXTS = {"mp4", "mkv", "avi", "mov", "webm", "flv", "wmv", "m4v"}
AUDIO_EXTS = {"mp3", "wav", "flac", "ogg", "m4a", "aac", "wma"}
CONVERTIBLE_EXTS = VIDEO_EXTS | AUDIO_EXTS
COPY_CHUNK = 1024 * 1024

# общие помощники

def _like_prefix(prefix: str) -> str:
    return prefix.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "/%"

def _meta(entry: Path) -> dict:
    st = entry.stat()
    return {
        "name": entry.name,
        "size": st.st_size,
        "size_human": human_size(st.st_size),
        "modified": datetime.fromtimestamp(st.st_mtime).strftime("%d.%m.%Y"),
        "mtime": st.st_mtime,
    }

def _rel(root: Path, path: Path) -> str:
    return path.relative_to(root.resolve()).as_posix()

def _unique_copy_name(src: Path) -> Path:
    base, suffix = (src.name, "") if src.is_dir() else (src.stem, src.suffix)
    candidate = src.parent / f"{base} (копия){suffix}"
    n = 2
    while candidate.exists():
        candidate = src.parent / f"{base} (копия {n}){suffix}"
        n += 1
    return candidate

def _tree_size(path: Path) -> int:
    return path.stat().st_size if path.is_file() else dir_size_bytes(path)

def _has_large_file(items: list, limit: int) -> bool:
    for item in items:
        if item.is_file():
            if item.stat().st_size > limit:
                return True
        elif item.is_dir():
            for entry in item.rglob("*"):
                if entry.is_file() and entry.stat().st_size > limit:
                    return True
    return False

def _zip_response(items: list, tmp_dir: Path, filename: str) -> FileResponse:
    tmp_dir.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(suffix=".zip", dir=str(tmp_dir))
    os.close(fd)
    tmp_path = Path(tmp_name)
    with zipfile.ZipFile(tmp_path, "w", zipfile.ZIP_STORED) as zf:
        for item in items:
            if item.is_file():
                zf.write(item, arcname=item.name)
            elif item.is_dir():
                for entry in item.rglob("*"):
                    if entry.is_file():
                        zf.write(entry, arcname=str(Path(item.name) / entry.relative_to(item)))
    return FileResponse(tmp_path, filename=filename, media_type="application/zip",
                        background=BackgroundTask(lambda: tmp_path.unlink(missing_ok=True)))

def _quota_info(user) -> tuple[int, Optional[int]]:
    gb = user["quota_gb"] or 0
    return gb, (gb * 1024 ** 3 if gb else None)

def _check_quota_for(ctx: Context, user, root: Path, extra: int) -> None:
    gb, limit = _quota_info(user)
    if limit and dir_size_bytes(root) + extra > limit:
        raise HTTPException(status_code=413, detail=f"Превышена квота хранилища ({gb} ГБ)")

class PathIn(BaseModel):
    path: str = ""

class FolderIn(BaseModel):
    path: str = ""
    name: str

class RenameIn(BaseModel):
    path: str
    new_name: str

class MoveIn(BaseModel):
    path: str
    dest_folder: str = ""

class BulkMoveIn(BaseModel):
    paths: List[str] = Field(default_factory=list)
    dest_folder: str = ""

class BulkDeleteIn(BaseModel):
    paths: List[str] = Field(default_factory=list)

# фабрика роутера пространства

def make_space_router(*, space: str, root_for: Callable, write_dep: Callable, personal: bool) -> APIRouter:
    """Один набор эндпоинтов для общей папки (`/api/...`) и личного хранилища (`/api/personal/...`)."""
    router = APIRouter()

    def tmp_dir(ctx: Context) -> Path:
        return ctx.settings.tmp_dir

    def on_deleted(ctx, user, root: Path, target_rel: str) -> None:
        """Чистим ярлыки и ссылки общего доступа, которые указывали на удалённое."""
        with ctx.db.connect() as conn:
            if personal:
                conn.execute("DELETE FROM shortcuts WHERE user_id = ? AND space = ? AND (path = ? OR path LIKE ? ESCAPE '\\')",
                             (user["id"], space, target_rel, _like_prefix(target_rel)))
                conn.execute("DELETE FROM shares WHERE owner_username = ? AND (path = ? OR path LIKE ? ESCAPE '\\')",
                             (user["username"], target_rel, _like_prefix(target_rel)))
            else:
                conn.execute("DELETE FROM shortcuts WHERE space = ? AND (path = ? OR path LIKE ? ESCAPE '\\')",
                             (space, target_rel, _like_prefix(target_rel)))
        if personal:
            invalidate_usage(ctx, f"u:{user['username']}")

    def on_moved(ctx, user, old_rel: str, new_rel: str) -> None:
        with ctx.db.connect() as conn:
            if personal:
                conn.execute("UPDATE shares SET path = ? WHERE owner_username = ? AND path = ?", (new_rel, user["username"], old_rel))
                conn.execute(
                    "UPDATE shares SET path = ? || substr(path, ?) WHERE owner_username = ? AND path LIKE ? ESCAPE '\\'",
                    (new_rel, len(old_rel) + 1, user["username"], _like_prefix(old_rel)),
                )
            conn.execute("DELETE FROM shortcuts WHERE space = ? AND (path = ? OR path LIKE ? ESCAPE '\\')"
                         + (" AND user_id = ?" if personal else ""),
                         (space, old_rel, _like_prefix(old_rel), *( [user["id"]] if personal else [])))

    @router.get("/browse")
    def browse(request: Request, path: str = "", user=Depends(require_user)):
        ctx = get_ctx(request)
        root = root_for(ctx, user)
        target = safe_join(root, path)
        if not target.is_dir():
            raise HTTPException(status_code=404, detail="Папка не найдена")
        shared_map: dict = {}
        if personal:
            with ctx.db.connect() as conn:
                for r in conn.execute("SELECT path, shared_with FROM shares WHERE owner_username = ?", (user["username"],)):
                    shared_map.setdefault(r["path"], []).append(r["shared_with"] or "Все")
        folders, files = [], []
        for entry in sorted(target.iterdir(), key=lambda e: e.name.lower()):
            try:
                if entry.is_dir():
                    folders.append({"name": entry.name})
                elif entry.is_file():
                    meta = _meta(entry)
                    if personal:
                        meta["shared_with"] = shared_map.get(_rel(root, entry), [])
                    files.append(meta)
            except OSError:
                continue
        return {"path": path.strip("/"), "folders": folders, "files": files}

    @router.post("/folders")
    def create_folder(data: FolderIn, request: Request, user=Depends(write_dep)):
        ctx = get_ctx(request)
        name = data.name.strip()
        if not valid_name(name):
            raise HTTPException(status_code=400, detail="Некорректное имя папки")
        parent = safe_join(root_for(ctx, user), data.path)
        if not parent.is_dir():
            raise HTTPException(status_code=404, detail="Папка не найдена")
        new_dir = parent / name
        if new_dir.exists():
            raise HTTPException(status_code=400, detail="Папка с таким именем уже существует")
        new_dir.mkdir()
        return {"ok": True}

    @router.post("/rename")
    def rename_item(data: RenameIn, request: Request, user=Depends(write_dep)):
        ctx = get_ctx(request)
        root = root_for(ctx, user)
        new_name = data.new_name.strip()
        if not data.path.strip("/"):
            raise HTTPException(status_code=400, detail="Нельзя переименовать корень раздела")
        if not valid_name(new_name):
            raise HTTPException(status_code=400, detail="Некорректное имя")
        src = safe_join(root, data.path)
        if not src.exists():
            raise HTTPException(status_code=404, detail="Не найдено")
        dest = src.parent / new_name
        if dest.exists():
            raise HTTPException(status_code=400, detail="Файл с таким именем уже существует")
        old_rel = _rel(root, src)
        src.rename(dest)
        on_moved(ctx, user, old_rel, _rel(root, dest))
        return {"ok": True, "name": dest.name}

    @router.post("/copy")
    def copy_item(data: PathIn, request: Request, user=Depends(write_dep)):
        ctx = get_ctx(request)
        root = root_for(ctx, user)
        if not data.path.strip("/"):
            raise HTTPException(status_code=400, detail="Нельзя скопировать корень раздела")
        src = safe_join(root, data.path)
        if not src.exists():
            raise HTTPException(status_code=404, detail="Не найдено")
        if personal:
            _check_quota_for(ctx, user, root, _tree_size(src))
        dest = _unique_copy_name(src)
        if src.is_dir():
            shutil.copytree(src, dest)
        else:
            shutil.copy2(src, dest)
        if personal:
            invalidate_usage(ctx, f"u:{user['username']}")
        return {"ok": True, "name": dest.name}

    def _move_one(ctx, user, root: Path, path: str, dest_folder: str) -> None:
        if not path.strip("/"):
            raise HTTPException(status_code=400, detail="Нельзя переместить корень раздела")
        src = safe_join(root, path)
        if not src.exists():
            raise HTTPException(status_code=404, detail="Не найдено")
        dest_dir = safe_join(root, dest_folder)
        if not dest_dir.is_dir():
            raise HTTPException(status_code=404, detail="Целевая папка не найдена")
        dest = dest_dir / src.name
        if dest.exists():
            raise HTTPException(status_code=400, detail="В целевой папке уже есть файл с таким именем")
        if src == dest_dir or src in dest_dir.parents:
            raise HTTPException(status_code=400, detail="Нельзя переместить папку саму в себя")
        old_rel = _rel(root, src)
        shutil.move(str(src), str(dest))
        on_moved(ctx, user, old_rel, _rel(root, dest))

    @router.post("/move")
    def move_item(data: MoveIn, request: Request, user=Depends(write_dep)):
        ctx = get_ctx(request)
        _move_one(ctx, user, root_for(ctx, user), data.path, data.dest_folder)
        return {"ok": True}

    @router.post("/bulk-move")
    def bulk_move(data: BulkMoveIn, request: Request, user=Depends(write_dep)):
        ctx = get_ctx(request)
        root = root_for(ctx, user)
        for p in data.paths:
            _move_one(ctx, user, root, p, data.dest_folder)
        return {"ok": True}

    @router.delete("/folders")
    def delete_folder(request: Request, path: str, user=Depends(write_dep)):
        ctx = get_ctx(request)
        root = root_for(ctx, user)
        target = safe_join(root, path)
        if target == root.resolve():
            raise HTTPException(status_code=400, detail="Нельзя удалить корневую папку")
        if not target.is_dir():
            raise HTTPException(status_code=404, detail="Папка не найдена")
        rel = _rel(root, target)
        shutil.rmtree(target)
        on_deleted(ctx, user, root, rel)
        return {"ok": True}

    @router.delete("/files")
    def delete_file(request: Request, path: str, user=Depends(write_dep)):
        ctx = get_ctx(request)
        root = root_for(ctx, user)
        target = safe_join(root, path)
        if not target.is_file():
            raise HTTPException(status_code=404, detail="Файл не найден")
        rel = _rel(root, target)
        target.unlink()
        on_deleted(ctx, user, root, rel)
        return {"ok": True}

    @router.post("/bulk-delete")
    def bulk_delete(data: BulkDeleteIn, request: Request, user=Depends(write_dep)):
        ctx = get_ctx(request)
        root = root_for(ctx, user)
        skipped = []
        for p in data.paths:
            if not p.strip("/"):
                skipped.append(p)
                continue
            target = safe_join(root, p)
            rel = _rel(root, target)
            if target.is_dir():
                shutil.rmtree(target)
            elif target.is_file():
                target.unlink()
            else:
                continue
            on_deleted(ctx, user, root, rel)
        return {"ok": True, "skipped": skipped}

    @router.post("/upload")
    def upload(request: Request, path: str = Form(""), paths: List[str] = Form(...),
               files: List[UploadFile] = File(...), user=Depends(write_dep)):
        ctx = get_ctx(request)
        root = root_for(ctx, user)
        base = safe_join(root, path)
        if not base.is_dir():
            raise HTTPException(status_code=404, detail="Папка не найдена")
        gb, limit = _quota_info(user) if personal else (0, None)
        used = dir_size_bytes(root) if limit else 0
        saved, skipped = 0, []
        for rel, up in zip(paths, files):
            parts = [p for p in rel.replace("\\", "/").split("/") if p]
            if not parts or not all(valid_name(p) for p in parts):
                skipped.append(rel)
                continue
            dest = safe_join(root, "/".join(filter(None, [path.strip("/"), *parts])))
            dest.parent.mkdir(parents=True, exist_ok=True)
            written, over = 0, False
            with open(dest, "wb") as out:
                while True:
                    chunk = up.file.read(COPY_CHUNK)
                    if not chunk:
                        break
                    written += len(chunk)
                    if limit and used + written > limit:
                        over = True
                        break
                    out.write(chunk)
            if over:
                dest.unlink(missing_ok=True)
                if personal:
                    invalidate_usage(ctx, f"u:{user['username']}")
                raise HTTPException(status_code=413, detail=f"Превышена квота хранилища ({gb} ГБ). Загружено файлов: {saved}")
            used += written
            saved += 1
        if personal:
            invalidate_usage(ctx, f"u:{user['username']}")
        return {"ok": True, "saved": saved, "skipped": skipped}

    @router.get("/files/download")
    def download_file(request: Request, path: str, user=Depends(require_user)):
        target = safe_join(root_for(get_ctx(request), user), path)
        if not target.is_file():
            raise HTTPException(status_code=404, detail="Файл не найден")
        return FileResponse(target, filename=target.name, media_type="application/octet-stream")

    @router.get("/folders/download")
    def download_folder(request: Request, path: str, user=Depends(require_user)):
        ctx = get_ctx(request)
        target = safe_join(root_for(ctx, user), path)
        if not target.is_dir():
            raise HTTPException(status_code=404, detail="Папка не найдена")
        if _has_large_file([target], ctx.settings.zip_max_file_bytes):
            raise HTTPException(status_code=400, detail="В папке есть большие файлы: архивация недоступна, скачайте их по отдельности")
        return _zip_response([target], tmp_dir(ctx), f"{target.name or 'folder'}.zip")

    @router.get("/bulk-download")
    def bulk_download(request: Request, path: List[str] = Query(...), user=Depends(require_user)):
        ctx = get_ctx(request)
        root = root_for(ctx, user)
        items = [i for i in (safe_join(root, p) for p in path) if i.exists() and i != root.resolve()]
        if not items:
            raise HTTPException(status_code=404, detail="Ничего не найдено")
        if _has_large_file(items, ctx.settings.zip_max_file_bytes):
            raise HTTPException(status_code=400, detail="Среди выбранного есть большие файлы: скачайте их по отдельности")
        return _zip_response(items, tmp_dir(ctx), "files.zip")

    return router

def _common_root(ctx: Context, user) -> Path:
    ctx.settings.storage_dir.mkdir(parents=True, exist_ok=True)
    return ctx.settings.storage_dir

def _personal_root(ctx: Context, user) -> Path:
    return user_root(ctx.settings, user["username"])

common_router = make_space_router(space="common", root_for=_common_root, write_dep=require_shared_writer, personal=False)
personal_router = make_space_router(space="personal", root_for=_personal_root, write_dep=require_writable_user, personal=True)

# только личное: архивы, конвертация

extras = APIRouter()

@extras.post("/personal/archive")
def personal_archive(data: PathIn, request: Request, user=Depends(require_writable_user)):
    ctx = get_ctx(request)
    root = _personal_root(ctx, user)
    if not data.path.strip("/"):
        raise HTTPException(status_code=400, detail="Нельзя архивировать корень раздела")
    src = safe_join(root, data.path)
    if not src.exists():
        raise HTTPException(status_code=404, detail="Не найдено")
    zip_path = src.with_suffix(src.suffix + ".zip") if src.is_file() else src.parent / f"{src.name}.zip"
    if zip_path.exists():
        raise HTTPException(status_code=400, detail="Архив с таким именем уже существует")
    try:
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            if src.is_file():
                zf.write(src, arcname=src.name)
            else:
                for entry in src.rglob("*"):
                    if entry.is_file():
                        zf.write(entry, arcname=str(entry.relative_to(src.parent)))
        _check_quota_for(ctx, user, root, 0)
    except HTTPException:
        zip_path.unlink(missing_ok=True)
        raise
    invalidate_usage(ctx, f"u:{user['username']}")
    return {"ok": True, "name": zip_path.name}

@extras.post("/personal/extract")
def personal_extract(data: PathIn, request: Request, user=Depends(require_writable_user)):
    ctx = get_ctx(request)
    root = _personal_root(ctx, user)
    src = safe_join(root, data.path)
    if not src.is_file() or src.suffix.lower() != ".zip":
        raise HTTPException(status_code=400, detail="Это не zip-архив")
    dest_dir = src.with_suffix("")
    if dest_dir.exists():
        raise HTTPException(status_code=400, detail="Папка с таким именем уже существует")
    try:
        with zipfile.ZipFile(src, "r") as zf:
            infos = zf.infolist()
            total = sum(i.file_size for i in infos)
            if len(infos) > 100_000 or total > ctx.settings.extract_max_bytes:
                raise HTTPException(status_code=400, detail="Архив слишком большой для распаковки")
            _check_quota_for(ctx, user, root, total)
            dest_dir.mkdir()
            zf.extractall(dest_dir)  # extractall сам отбрасывает абсолютные пути и «..»
    except zipfile.BadZipFile:
        shutil.rmtree(dest_dir, ignore_errors=True)
        raise HTTPException(status_code=400, detail="Повреждённый архив")
    invalidate_usage(ctx, f"u:{user['username']}")
    return {"ok": True, "name": dest_dir.name}

class ConvertIn(BaseModel):
    path: str
    target_ext: str

@extras.post("/personal/convert")
def personal_convert(data: ConvertIn, request: Request, user=Depends(require_writable_user)):
    ctx = get_ctx(request)
    if not shutil.which("ffmpeg"):
        raise HTTPException(status_code=501, detail="ffmpeg не установлен на сервере")
    root = _personal_root(ctx, user)
    src = safe_join(root, data.path)
    target_ext = data.target_ext.strip(".").lower()
    if not src.is_file():
        raise HTTPException(status_code=404, detail="Файл не найден")
    if src.suffix.strip(".").lower() not in CONVERTIBLE_EXTS:
        raise HTTPException(status_code=400, detail="Этот тип файла нельзя конвертировать")
    if target_ext not in CONVERTIBLE_EXTS:
        raise HTTPException(status_code=400, detail="Неизвестный целевой формат")
    dest = src.with_suffix(f".{target_ext}")
    if dest.exists():
        raise HTTPException(status_code=400, detail="Файл с таким именем уже существует")
    try:
        result = subprocess.run(["ffmpeg", "-nostdin", "-i", str(src), str(dest)], capture_output=True, timeout=1800)
    except subprocess.TimeoutExpired:
        dest.unlink(missing_ok=True)
        raise HTTPException(status_code=504, detail="Конвертация заняла слишком много времени")
    if result.returncode != 0 or not dest.exists():
        dest.unlink(missing_ok=True)
        raise HTTPException(status_code=500, detail="Ошибка конвертации ffmpeg")
    try:
        _check_quota_for(ctx, user, root, 0)
    except HTTPException:
        dest.unlink(missing_ok=True)
        raise
    invalidate_usage(ctx, f"u:{user['username']}")
    return {"ok": True, "name": dest.name}

# общий доступ к файлам

class ShareIn(BaseModel):
    path: str
    shared_with: Optional[str] = None

@extras.post("/shares")
def create_share(data: ShareIn, request: Request, user=Depends(require_user)):
    ctx = get_ctx(request)
    if not user["cloud_can_share"]:
        raise HTTPException(status_code=403, detail="Этому аккаунту нельзя открывать доступ к файлам")
    path = data.path.strip("/")
    target = safe_join(_personal_root(ctx, user), path)
    if not target.is_file():
        raise HTTPException(status_code=404, detail="Файл не найден")
    shared_with = (data.shared_with or "").strip() or None
    with ctx.db.connect() as conn:
        if shared_with:
            row = conn.execute("SELECT username FROM users WHERE username = ?", (shared_with,)).fetchone()
            if not row:
                raise HTTPException(status_code=400, detail="Такого пользователя нет")
            if row["username"].lower() == user["username"].lower():
                raise HTTPException(status_code=400, detail="Нельзя поделиться с самим собой")
            shared_with = row["username"]
        exists = conn.execute("SELECT id FROM shares WHERE owner_username = ? AND path = ? AND shared_with IS ?",
                              (user["username"], path, shared_with)).fetchone()
        if exists:
            raise HTTPException(status_code=400, detail="Уже доступно этому получателю")
        conn.execute("INSERT INTO shares (owner_username, path, shared_with, created_at) VALUES (?, ?, ?, ?)",
                     (user["username"], path, shared_with, utcnow()))
    return {"ok": True}

@extras.get("/shares")
def list_shares(request: Request, path: str, user=Depends(require_user)):
    ctx = get_ctx(request)
    with ctx.db.connect() as conn:
        rows = conn.execute("SELECT id, shared_with FROM shares WHERE owner_username = ? AND path = ?",
                            (user["username"], path.strip("/"))).fetchall()
    return {"shares": [{"id": r["id"], "shared_with": r["shared_with"] or "Все"} for r in rows]}

@extras.delete("/shares/{share_id}")
def delete_share(share_id: int, request: Request, user=Depends(require_user)):
    ctx = get_ctx(request)
    with ctx.db.connect() as conn:
        conn.execute("DELETE FROM shares WHERE id = ? AND owner_username = ?", (share_id, user["username"]))
    return {"ok": True}

@extras.get("/shared-with-me")
def shared_with_me(request: Request, user=Depends(require_user)):
    ctx = get_ctx(request)
    with ctx.db.connect() as conn:
        rows = conn.execute(
            "SELECT id, owner_username, path, shared_with FROM shares"
            " WHERE (shared_with = ? OR shared_with IS NULL) AND owner_username != ?",
            (user["username"], user["username"]),
        ).fetchall()
    items = []
    for r in rows:
        try:
            entry = safe_join(ctx.settings.personal_dir / r["owner_username"], r["path"])
        except HTTPException:
            continue
        if not entry.is_file():
            continue
        meta = _meta(entry)
        meta.update(share_id=r["id"], owner=r["owner_username"], path=r["path"], is_public=r["shared_with"] is None)
        items.append(meta)
    return {"files": items}

@extras.get("/shared/download")
def shared_download(request: Request, share_id: int, user=Depends(require_user)):
    ctx = get_ctx(request)
    with ctx.db.connect() as conn:
        row = conn.execute("SELECT * FROM shares WHERE id = ?", (share_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Не найдено")
    if row["shared_with"] is not None and row["shared_with"].lower() != user["username"].lower() \
            and row["owner_username"].lower() != user["username"].lower():
        raise HTTPException(status_code=403, detail="Нет доступа")
    target = safe_join(ctx.settings.personal_dir / row["owner_username"], row["path"])
    if not target.is_file():
        raise HTTPException(status_code=404, detail="Файл не найден")
    return FileResponse(target, filename=target.name, media_type="application/octet-stream")

# ярлыки «Быстрый доступ»

class ShortcutIn(BaseModel):
    name: str
    path: str
    space: str = "common"

@extras.get("/shortcuts")
def list_shortcuts(request: Request, user=Depends(require_user)):
    ctx = get_ctx(request)
    with ctx.db.connect() as conn:
        rows = conn.execute("SELECT id, name, path, space FROM shortcuts WHERE user_id = ? ORDER BY created_at, id",
                            (user["id"],)).fetchall()
    return {"shortcuts": [dict(r) for r in rows]}

@extras.post("/shortcuts")
def create_shortcut(data: ShortcutIn, request: Request, user=Depends(require_user)):
    ctx = get_ctx(request)
    name = data.name.strip()
    if not name or len(name) > 255:
        raise HTTPException(status_code=400, detail="Некорректное имя")
    path = data.path.strip("/")
    space = "personal" if data.space == "personal" else "common"
    root = _personal_root(ctx, user) if space == "personal" else _common_root(ctx, user)
    if not safe_join(root, path).is_dir():
        raise HTTPException(status_code=404, detail="Папка не найдена")
    with ctx.db.connect() as conn:
        conn.execute("INSERT INTO shortcuts (user_id, name, path, space, created_at) VALUES (?, ?, ?, ?, ?)",
                     (user["id"], name, path, space, utcnow()))
    return {"ok": True}

@extras.delete("/shortcuts/{shortcut_id}")
def delete_shortcut(shortcut_id: int, request: Request, user=Depends(require_user)):
    ctx = get_ctx(request)
    with ctx.db.connect() as conn:
        conn.execute("DELETE FROM shortcuts WHERE id = ? AND user_id = ?", (shortcut_id, user["id"]))
    return {"ok": True}

# место на диске

@extras.get("/storage")
def api_storage(request: Request, user=Depends(require_user)):
    ctx = get_ctx(request)
    root = _common_root(ctx, user)
    usage = shutil.disk_usage(root)
    personal = cached_dir_size(ctx, f"u:{user['username']}", _personal_root(ctx, user))
    return {
        "used": usage.used,
        "total": usage.total,
        "used_human": human_size(usage.used),
        "total_human": human_size(usage.total),
        "percent": round(usage.used / usage.total * 100, 1) if usage.total else 0,
        "personal_used": personal,
        "personal_used_human": human_size(personal),
        "quota_gb": user["quota_gb"],
    }
