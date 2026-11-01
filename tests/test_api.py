"""Сквозные проверки API на временном каталоге: `pytest -q`."""
import io
import zipfile

import pyotp
import pytest
from fastapi.testclient import TestClient

from app.config import load_settings
from app.main import create_app

SECRET = "t" * 48

@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("SECRET_KEY", SECRET)
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("ADMIN_USER", "admin")
    monkeypatch.setenv("ADMIN_PASSWORD", "correct-horse-1")
    monkeypatch.setenv("COOKIE_SECURE", "false")
    monkeypatch.setenv("LOGIN_MAX_ATTEMPTS", "4")
    app = create_app(load_settings())
    with TestClient(app) as c:
        yield c

def login(client, user="admin", password="correct-horse-1"):
    r = client.post("/api/login", json={"username": user, "password": password})
    assert r.status_code == 200, r.text
    return r

def upload(client, name, content=b"hello", prefix="/api/personal", path=""):
    return client.post(f"{prefix}/upload", data={"path": path, "paths": name},
                       files={"files": (name, io.BytesIO(content))})

def test_requires_auth(client):
    assert client.get("/api/me").status_code == 401
    assert client.get("/api/personal/browse").status_code == 401
    assert client.get("/healthz").json() == {"status": "ok"}
    r = client.get("/", follow_redirects=False)
    assert r.status_code in (302, 307) and r.headers["location"] == "/login"

def test_login_and_pages(client):
    assert client.get("/login").status_code == 200
    r = client.post("/api/login", json={"username": "admin", "password": "wrong"})
    assert r.status_code == 401
    login(client)
    me = client.get("/api/me").json()
    assert me["username"] == "admin" and me["is_admin"] is True
    assert client.get("/").status_code == 200
    assert client.get("/static/app.js").status_code == 200

def test_rate_limit(client):
    for _ in range(4):
        assert client.post("/api/login", json={"username": "admin", "password": "x"}).status_code == 401
    assert client.post("/api/login", json={"username": "admin", "password": "correct-horse-1"}).status_code == 429

def test_personal_files_roundtrip(client):
    login(client)
    assert client.post("/api/personal/folders", json={"path": "", "name": "docs"}).status_code == 200
    assert upload(client, "a.txt", b"hello world", path="docs").json()["saved"] == 1
    data = client.get("/api/personal/browse", params={"path": "docs"}).json()
    assert [f["name"] for f in data["files"]] == ["a.txt"]
    r = client.get("/api/personal/files/download", params={"path": "docs/a.txt"})
    assert r.content == b"hello world"
    assert client.post("/api/personal/copy", json={"path": "docs/a.txt"}).status_code == 200
    assert client.post("/api/personal/rename", json={"path": "docs/a.txt", "new_name": "b.txt"}).status_code == 200
    assert client.post("/api/personal/move", json={"path": "docs/b.txt", "dest_folder": ""}).status_code == 200
    z = client.get("/api/personal/folders/download", params={"path": "docs"})
    assert z.status_code == 200 and zipfile.ZipFile(io.BytesIO(z.content)).namelist()
    assert client.post("/api/personal/archive", json={"path": "docs"}).status_code == 200
    assert client.post("/api/personal/extract", json={"path": "docs.zip"}).status_code == 400  # папка docs уже есть
    assert client.delete("/api/personal/folders", params={"path": "docs"}).status_code == 200
    assert client.post("/api/personal/extract", json={"path": "docs.zip"}).status_code == 200
    r = client.post("/api/personal/bulk-delete", json={"paths": ["b.txt", "docs"]})
    assert r.status_code == 200
    assert client.delete("/api/personal/files", params={"path": "docs.zip"}).status_code == 200

def test_path_traversal_blocked(client):
    login(client)
    for bad in ("../..", "../../etc", "docs/../../x", "/etc/passwd"):
        r = client.get("/api/personal/browse", params={"path": bad})
        assert r.status_code in (400, 404), (bad, r.status_code)
    r = client.get("/api/personal/files/download", params={"path": "../../app.db"})
    assert r.status_code in (400, 404)
    r = client.post("/api/personal/upload", data={"path": "", "paths": "../evil.txt"},
                    files={"files": ("evil.txt", io.BytesIO(b"x"))})
    assert r.json()["saved"] == 0 and r.json()["skipped"]

def test_quota_enforced(client, monkeypatch):
    login(client)
    assert client.post("/api/users", json={"username": "kid", "password": "kid-password-1", "quota_gb": 1}).status_code == 200
    kid = TestClient(client.app)
    login(kid, "kid", "kid-password-1")
    assert upload(kid, "ok.txt", b"x" * 100).status_code == 200
    assert kid.get("/api/me").json()["quota_gb"] == 1
    # уменьшаем лимит до 150 байт: второй файл не помещается и не остаётся на диске
    from app import files
    monkeypatch.setattr(files, "_quota_info", lambda user: (1, 150))
    r = upload(kid, "big.txt", b"y" * 100)
    assert r.status_code == 413
    names = [f["name"] for f in kid.get("/api/personal/browse").json()["files"]]
    assert names == ["ok.txt"]

def test_users_admin_rules(client):
    login(client)
    assert client.post("/api/users", json={"username": "../x", "password": "password-1"}).status_code in (400, 422)
    assert client.post("/api/users", json={"username": "bob", "password": "short"}).status_code == 400
    assert client.post("/api/users", json={"username": "bob", "password": "bob-password-1"}).status_code == 200
    assert client.post("/api/users", json={"username": "BOB", "password": "bob-password-1"}).status_code == 400
    # нельзя отключить / удалить себя и остаться без админа
    assert client.patch("/api/users/admin", json={"disabled": True}).status_code == 400
    assert client.patch("/api/users/admin", json={"is_admin": False}).status_code == 400
    assert client.delete("/api/users/admin").status_code == 400
    bob = TestClient(client.app)
    login(bob, "bob", "bob-password-1")
    assert bob.get("/api/users").status_code == 403
    # отключение немедленно разлогинивает
    assert client.patch("/api/users/bob", json={"disabled": True}).status_code == 200
    assert bob.get("/api/me").status_code == 401
    assert client.delete("/api/users/bob", params={"purge": True}).status_code == 200

def test_shared_space_permissions(client):
    login(client)
    client.post("/api/users", json={"username": "bob", "password": "bob-password-1"})
    bob = TestClient(client.app)
    login(bob, "bob", "bob-password-1")
    assert upload(client, "readme.txt", prefix="/api").status_code == 200
    assert bob.get("/api/browse").json()["files"][0]["name"] == "readme.txt"
    assert upload(bob, "x.txt", prefix="/api").status_code == 403
    assert bob.post("/api/folders", json={"path": "", "name": "n"}).status_code == 403
    assert bob.delete("/api/files", params={"path": "readme.txt"}).status_code == 403

def test_sharing_between_users(client):
    login(client)
    client.post("/api/users", json={"username": "bob", "password": "bob-password-1"})
    bob = TestClient(client.app)
    login(bob, "bob", "bob-password-1")
    upload(client, "secret.txt", b"top")
    assert bob.get("/api/shared-with-me").json()["files"] == []
    assert client.post("/api/shares", json={"path": "secret.txt", "shared_with": "bob"}).status_code == 200
    items = bob.get("/api/shared-with-me").json()["files"]
    assert len(items) == 1
    assert bob.get("/api/shared/download", params={"share_id": items[0]["share_id"]}).content == b"top"
    # после переименования ссылка следует за файлом, после удаления пропадает
    client.post("/api/personal/rename", json={"path": "secret.txt", "new_name": "s2.txt"})
    assert bob.get("/api/shared-with-me").json()["files"][0]["path"] == "s2.txt"
    client.delete("/api/personal/files", params={"path": "s2.txt"})
    assert bob.get("/api/shared-with-me").json()["files"] == []

def test_totp_flow(client):
    login(client)
    secret = client.post("/api/2fa/setup").json()["secret"]
    assert client.post("/api/2fa/enable", json={"code": "000000"}).status_code == 400
    assert client.post("/api/2fa/enable", json={"code": pyotp.TOTP(secret).now()}).status_code == 200
    fresh = TestClient(client.app)
    r = fresh.post("/api/login", json={"username": "admin", "password": "correct-horse-1"})
    body = r.json()
    assert body["requires_totp"] is True
    assert fresh.post("/api/login/totp", json={"pending_token": body["pending_token"], "code": "111111"}).status_code == 401
    ok = fresh.post("/api/login/totp", json={"pending_token": body["pending_token"], "code": pyotp.TOTP(secret).now()})
    assert ok.status_code == 200
    assert fresh.get("/api/me").json()["totp_enabled"] is True

def test_change_password(client):
    login(client)
    assert client.post("/api/me/password", json={"current_password": "nope", "new_password": "new-password-1"}).status_code == 400
    assert client.post("/api/me/password", json={"current_password": "correct-horse-1", "new_password": "new-password-1"}).status_code == 200
    assert client.get("/api/me").status_code == 200  # текущая сессия продолжает работать
    other = TestClient(client.app)
    assert other.post("/api/login", json={"username": "admin", "password": "correct-horse-1"}).status_code == 401
    login(other, "admin", "new-password-1")

def test_settings_validation(monkeypatch, tmp_path):
    from app.config import ConfigError
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.delenv("SECRET_KEY", raising=False)
    with pytest.raises(ConfigError):
        load_settings()
    monkeypatch.setenv("SECRET_KEY", "change-me-" + "x" * 40)
    with pytest.raises(ConfigError):
        load_settings()
