#!/usr/bin/env python3
"""
Скачивает TLS-сертификат Management API Outline с хоста из URL и сохраняет в PEM-файл.

Используется для проверки соединения бота с API (certificate pinning), см. README.

Примеры:
  python scripts/fetch_outline_cert.py --url "https://1.2.3.4:50482/secret"
  python scripts/fetch_outline_cert.py --from-env -o certs/my.pem
"""

from __future__ import annotations

import argparse
import os
import ssl
import sys
from pathlib import Path
from urllib.parse import urlparse


def _load_dotenv_env() -> None:
    try:
        from dotenv import load_dotenv
    except ImportError:
        print(
            "Для --from-env установите зависимости: pip install python-dotenv",
            file=sys.stderr,
        )
        sys.exit(1)
    root = Path(__file__).resolve().parent.parent
    load_dotenv(root / ".env")


def _parse_management_url(raw: str) -> tuple[str, int]:
    raw = raw.strip()
    if not raw.startswith(("http://", "https://")):
        raw = "https://" + raw
    p = urlparse(raw)
    if not p.hostname:
        raise ValueError(f"Не удалось извлечь хост из URL: {raw!r}")
    host = p.hostname
    if p.port is not None:
        port = p.port
    else:
        port = 443 if (p.scheme or "https") == "https" else 80
    return host, port


def fetch_pem(host: str, port: int, timeout: float = 10.0) -> str:
    """Получает сертификат сервера в PEM (как в ssl.get_server_certificate)."""
    addr = (host, port)
    # Python 3.7+: timeout; для IP SNI часто не нужен
    pem = ssl.get_server_certificate(addr, timeout=timeout)
    if not pem.endswith("\n"):
        pem += "\n"
    return pem


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Сохранить PEM-сертификат Management API Outline с хоста URL.",
    )
    src = parser.add_mutually_exclusive_group(required=True)
    src.add_argument(
        "--url",
        "-u",
        help="Полный URL Management API (как в Outline Manager / .env)",
    )
    src.add_argument(
        "--from-env",
        action="store_true",
        help="Взять URL из OUTLINE_API_URL в .env (каталог проекта)",
    )
    parser.add_argument(
        "-o",
        "--output",
        default="outline_cert.pem",
        help="Куда записать PEM (по умолчанию: outline_cert.pem в текущей директории)",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=10.0,
        help="Таймаут подключения в секундах (по умолчанию: 10)",
    )
    args = parser.parse_args()

    if args.from_env:
        _load_dotenv_env()
        url = os.getenv("OUTLINE_API_URL", "").strip()
        if not url:
            print("В .env не задан OUTLINE_API_URL", file=sys.stderr)
            return 1
    else:
        url = args.url.strip()

    try:
        host, port = _parse_management_url(url)
    except ValueError as e:
        print(e, file=sys.stderr)
        return 1

    try:
        pem = fetch_pem(host, port, timeout=args.timeout)
    except OSError as e:
        print(f"Не удалось подключиться к {host}:{port}: {e}", file=sys.stderr)
        return 1
    except ssl.SSLError as e:
        print(f"Ошибка TLS при подключении к {host}:{port}: {e}", file=sys.stderr)
        return 1

    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(pem, encoding="utf-8")
    print(f"Сертификат сохранён: {out.resolve()}")
    print(f"Хост: {host}, порт: {port}")
    print(
        "\nДля нескольких серверов: разные PEM и поле \"cert\" в servers.json "
        "или переменная OUTLINE_CERT в .env (см. README).",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
