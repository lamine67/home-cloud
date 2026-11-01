#!/usr/bin/env python3
"""Создаёт файл .env со случайным SECRET_KEY и паролем первого администратора.

    python scripts/init_env.py            # создать .env рядом с .env.example
    python scripts/init_env.py --http     # для проверки без HTTPS: COOKIE_SECURE=false
    python scripts/init_env.py --user me  # другой логин администратора

Пароль администратора печатается один раз: сохраните его и смените в «Настройки и безопасность».
Существующий .env не перезаписывается (нужен --force).
"""
import argparse
import re
import secrets
import string
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--user", default="admin", help="логин администратора (по умолчанию admin)")
    parser.add_argument("--http", action="store_true", help="сайт без HTTPS: COOKIE_SECURE=false")
    parser.add_argument("--force", action="store_true", help="перезаписать существующий .env")
    args = parser.parse_args()

    target = ROOT / ".env"
    if target.exists() and not args.force:
        print(f"{target} уже существует, ничего не меняю (используйте --force для перезаписи).", file=sys.stderr)
        return 1

    alphabet = string.ascii_letters + string.digits
    password = "".join(secrets.choice(alphabet) for _ in range(16))
    text = (ROOT / ".env.example").read_text(encoding="utf-8")
    replacements = {
        "SECRET_KEY": secrets.token_urlsafe(48),
        "ADMIN_USER": args.user,
        "ADMIN_PASSWORD": password,
    }
    if args.http:
        replacements["COOKIE_SECURE"] = "false"
    for key, value in replacements.items():
        text, n = re.subn(rf"^{key}=.*$", lambda _m, k=key, v=value: f"{k}={v}", text, flags=re.M)
        if n != 1:
            print(f"В .env.example не найден параметр {key}", file=sys.stderr)
            return 2
    target.write_text(text, encoding="utf-8")
    try:
        target.chmod(0o600)
    except OSError:
        pass
    print(f"Создан {target}")
    print(f"Логин администратора: {args.user}")
    print(f"Пароль (показан один раз): {password}")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
