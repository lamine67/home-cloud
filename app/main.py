"""Точка входа: `uvicorn app.main:app`."""
from __future__ import annotations

import shutil
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from itsdangerous import URLSafeTimedSerializer

from . import auth, files
from .config import BASE_DIR, Settings, load_settings
from .db import Database
from .deps import Context, RateLimiter, get_current_user

STATIC_DIR = BASE_DIR / "static"
NO_CACHE = {"Cache-Control": "no-store"}

def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or load_settings()
    db = Database(settings.db_path)
    for d in (settings.data_dir, settings.storage_dir, settings.personal_dir):
        d.mkdir(parents=True, exist_ok=True)
    db.init(settings)

    ctx = Context(
        settings=settings,
        db=db,
        serializer=URLSafeTimedSerializer(settings.secret_key),
        limiter=RateLimiter(settings.login_max_attempts, settings.login_window_sec),
    )

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        # Недокачанные временные zip-архивы после перезапуска не нужны
        shutil.rmtree(settings.tmp_dir, ignore_errors=True)
        settings.tmp_dir.mkdir(parents=True, exist_ok=True)
        yield

    app = FastAPI(title=settings.app_name, docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
    app.state.ctx = ctx

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "same-origin")
        if request.url.path.startswith("/api/"):
            response.headers.setdefault("Cache-Control", "no-store")
        return response

    @app.exception_handler(OSError)
    async def os_error_handler(_request: Request, exc: OSError):
        # Ошибки файловой системы (нет места, нет прав, недопустимое имя) отдаём понятным сообщением, а не 500 с трассировкой
        return JSONResponse({"detail": f"Ошибка файловой системы: {exc.strerror or exc}"}, status_code=400)

    app.include_router(auth.router)
    app.include_router(files.common_router, prefix="/api")
    app.include_router(files.personal_router, prefix="/api/personal")
    app.include_router(files.extras, prefix="/api")
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    @app.get("/healthz", include_in_schema=False)
    def healthz():
        return JSONResponse({"status": "ok"})

    @app.get("/login", include_in_schema=False)
    def login_page(request: Request):
        if get_current_user(request):
            return RedirectResponse("/")
        return FileResponse(STATIC_DIR / "login.html", headers=NO_CACHE)

    @app.get("/", include_in_schema=False)
    def index(request: Request):
        if not get_current_user(request):
            return RedirectResponse("/login")
        return FileResponse(STATIC_DIR / "index.html", headers=NO_CACHE)

    return app

_app: FastAPI | None = None

def __getattr__(name: str):
    """`uvicorn app.main:app` создаёт приложение лениво, при первом обращении (удобно для тестов)."""
    global _app
    if name == "app":
        if _app is None:
            _app = create_app()
        return _app
    raise AttributeError(name)
