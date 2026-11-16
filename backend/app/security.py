"""Encryption of secrets at rest. Keys never leave the backend in clear text."""
from __future__ import annotations

import base64
import hashlib
import secrets

from cryptography.fernet import Fernet

from . import config

_KEY_FILE = config.DATA_DIR / ".secret_key"


def _master() -> bytes:
    raw = config.SECRET_KEY
    if not raw:
        # Development fallback: random key persisted in the data dir (chmod 600).
        if _KEY_FILE.exists():
            raw = _KEY_FILE.read_text().strip()
        else:
            raw = secrets.token_urlsafe(48)
            _KEY_FILE.write_text(raw)
            _KEY_FILE.chmod(0o600)
    return base64.urlsafe_b64encode(hashlib.sha256(raw.encode()).digest())


_fernet = Fernet(_master())


def encrypt(value: str) -> str:
    return _fernet.encrypt(value.encode()).decode() if value else ""


def decrypt(token: str) -> str:
    return _fernet.decrypt(token.encode()).decode() if token else ""


def mask(value: str) -> str:
    if not value:
        return ""
    return value[:3] + "…" + value[-4:] if len(value) > 10 else "••••"
