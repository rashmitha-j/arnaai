"""Field-level encryption for conversation content (AES-128-CBC + HMAC via Fernet).

Message text and CBT notes are encrypted before they reach the database, so a
leaked database dump or an over-privileged DB reader sees ciphertext only.
On GCP the key lives in Secret Manager and is injected as DATA_ENCRYPTION_KEY.
"""
from __future__ import annotations

import base64
import hashlib
import json
import logging

from cryptography.fernet import Fernet, InvalidToken

log = logging.getLogger(__name__)


class Cipher:
    def __init__(self, key: str):
        if not key:
            log.warning("DATA_ENCRYPTION_KEY not set: using an insecure development key. "
                        "Generate one with `python -c \"from cryptography.fernet import Fernet; "
                        "print(Fernet.generate_key().decode())\"`")
            key = base64.urlsafe_b64encode(hashlib.sha256(b"arnaai-dev-only").digest()).decode()
        self._f = Fernet(key.encode() if isinstance(key, str) else key)

    def encrypt(self, text: str) -> str:
        return self._f.encrypt(text.encode("utf-8")).decode("ascii")

    def decrypt(self, token: str) -> str:
        try:
            return self._f.decrypt(token.encode("ascii")).decode("utf-8")
        except InvalidToken:
            log.error("Could not decrypt a record (wrong key?)")
            return "[unreadable]"

    def encrypt_json(self, obj) -> str:
        return self.encrypt(json.dumps(obj))

    def decrypt_json(self, token: str, default=None):
        raw = self.decrypt(token) if token else ""
        try:
            return json.loads(raw)
        except (ValueError, TypeError):
            return default
