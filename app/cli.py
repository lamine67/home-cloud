"""Административные команды для случаев, когда вход в веб-интерфейс недоступен."""
from __future__ import annotations

import argparse
import getpass
import sys

from .config import ConfigError, load_settings
from .db import MIN_PASSWORD_LEN, USERNAME_RE, Database, hash_password, utcnow

def _ask_password() -> str:
    pw = getpass.getpass("Новый пароль: ")
    if pw != getpass.getpass("Повторите пароль: "):
        sys.exit("Пароли не совпадают")
    if len(pw) < MIN_PASSWORD_LEN:
        sys.exit(f"Пароль должен быть не короче {MIN_PASSWORD_LEN} символов")
    return pw

def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.cli", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("create-user")
    p.add_argument("username")
    p.add_argument("--admin", action="store_true")
    sub.add_parser("set-password").add_argument("username")
    sub.add_parser("reset-2fa").add_argument("username")
    sub.add_parser("list-users")
    args = parser.parse_args(argv)

    try:
        settings = load_settings()
    except ConfigError as exc:
        print(f"Ошибка конфигурации: {exc}", file=sys.stderr)
        return 2
    db = Database(settings.db_path)
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    with db.connect() as conn:
        from .db import SCHEMA
        conn.executescript(SCHEMA)
        if args.cmd == "list-users":
            for r in conn.execute("SELECT username, is_admin, disabled, totp_enabled FROM users ORDER BY id"):
                print(f"{r['username']}\tadmin={r['is_admin']}\tdisabled={r['disabled']}\t2fa={r['totp_enabled']}")
        elif args.cmd == "create-user":
            if not USERNAME_RE.match(args.username):
                print("Некорректный логин", file=sys.stderr)
                return 1
            conn.execute("INSERT INTO users (username, password_hash, is_admin, created_at) VALUES (?, ?, ?, ?)",
                         (args.username, hash_password(_ask_password()), int(args.admin), utcnow()))
            print("Пользователь создан")
        elif args.cmd == "set-password":
            cur = conn.execute("UPDATE users SET password_hash = ?, token_version = token_version + 1 WHERE username = ?",
                               (hash_password(_ask_password()), args.username))
            print("Пароль обновлён" if cur.rowcount else "Пользователь не найден")
        elif args.cmd == "reset-2fa":
            cur = conn.execute("UPDATE users SET totp_enabled = 0, totp_secret = NULL, token_version = token_version + 1 WHERE username = ?",
                               (args.username,))
            print("2FA сброшена" if cur.rowcount else "Пользователь не найден")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
