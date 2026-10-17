"""Persistence layer: SQLite for local development, Firestore on GCP.

Sensitive fields (message text, CBT state, emotional profiles, counselor
notes) are encrypted with `Cipher` before they are written. Only routing
metadata (ids, timestamps, status, risk level) is stored in the clear so the
counselor queue can be queried.
"""
from __future__ import annotations

import hashlib
import secrets
import sqlite3
import threading
import time
import uuid
from typing import Protocol

from .crypto import Cipher


def now() -> float:
    return time.time()


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


class Store(Protocol):
    def create_session(self, consent: dict) -> tuple[dict, str]: ...
    def get_session(self, session_id: str) -> dict | None: ...
    def verify_session_token(self, session_id: str, token: str) -> bool: ...
    def update_session(self, session_id: str, **fields) -> None: ...
    def delete_session(self, session_id: str) -> None: ...
    def add_message(self, session_id: str, role: str, content: str, meta: dict | None = None) -> dict: ...
    def get_messages(self, session_id: str, after: float = 0, limit: int = 200) -> list[dict]: ...
    def create_escalation(self, session_id: str, level: str, reason: dict, priority: str) -> dict: ...
    def get_open_escalation(self, session_id: str) -> dict | None: ...
    def list_escalations(self, status: str | None = None) -> list[dict]: ...
    def get_escalation(self, escalation_id: str) -> dict | None: ...
    def update_escalation(self, escalation_id: str, **fields) -> None: ...


# --------------------------------------------------------------------------- SQLite
class SQLiteStore:
    SCHEMA = """
    CREATE TABLE IF NOT EXISTS sessions (
        id TEXT PRIMARY KEY, token_hash TEXT NOT NULL, created_at REAL, updated_at REAL,
        status TEXT DEFAULT 'active', mode TEXT DEFAULT 'ai', risk_level TEXT DEFAULT 'none',
        risk_history TEXT DEFAULT '', cbt_state TEXT DEFAULT '', profile TEXT DEFAULT '',
        consent TEXT DEFAULT ''
    );
    CREATE TABLE IF NOT EXISTS messages (
        id TEXT PRIMARY KEY, session_id TEXT NOT NULL, role TEXT NOT NULL,
        content TEXT NOT NULL, meta TEXT DEFAULT '', created_at REAL
    );
    CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id, created_at);
    CREATE TABLE IF NOT EXISTS escalations (
        id TEXT PRIMARY KEY, session_id TEXT NOT NULL, level TEXT, priority TEXT,
        status TEXT DEFAULT 'open', reason TEXT DEFAULT '', counselor TEXT DEFAULT '',
        notes TEXT DEFAULT '', created_at REAL, updated_at REAL
    );
    CREATE INDEX IF NOT EXISTS idx_esc_status ON escalations(status, created_at);
    """
    ENCRYPTED_SESSION = {"cbt_state", "profile", "risk_history", "consent"}

    def __init__(self, path: str, cipher: Cipher):
        self.cipher = cipher
        self._lock = threading.Lock()
        self._db = sqlite3.connect(path, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._db.executescript(self.SCHEMA)
        self._db.commit()

    def _exec(self, sql: str, args: tuple = ()):
        with self._lock:
            cur = self._db.execute(sql, args)
            self._db.commit()
            return cur

    def _one(self, sql: str, args: tuple = ()):
        with self._lock:
            return self._db.execute(sql, args).fetchone()

    def _all(self, sql: str, args: tuple = ()):
        with self._lock:
            return self._db.execute(sql, args).fetchall()

    # sessions
    def _session_row(self, row) -> dict:
        d = dict(row)
        d.pop("token_hash", None)
        d["cbt_state"] = self.cipher.decrypt_json(d["cbt_state"], None)
        d["profile"] = self.cipher.decrypt_json(d["profile"], None)
        d["risk_history"] = self.cipher.decrypt_json(d["risk_history"], []) or []
        d["consent"] = self.cipher.decrypt_json(d["consent"], {}) or {}
        return d

    def create_session(self, consent: dict) -> tuple[dict, str]:
        sid, token, t = uuid.uuid4().hex, secrets.token_urlsafe(32), now()
        self._exec("INSERT INTO sessions (id, token_hash, created_at, updated_at, risk_history, consent) "
                   "VALUES (?,?,?,?,?,?)",
                   (sid, hash_token(token), t, t, self.cipher.encrypt_json([]), self.cipher.encrypt_json(consent)))
        return self.get_session(sid), token

    def get_session(self, session_id: str) -> dict | None:
        row = self._one("SELECT * FROM sessions WHERE id=?", (session_id,))
        return self._session_row(row) if row else None

    def verify_session_token(self, session_id: str, token: str) -> bool:
        row = self._one("SELECT token_hash FROM sessions WHERE id=?", (session_id,))
        return bool(row and token and secrets.compare_digest(row["token_hash"], hash_token(token)))

    def update_session(self, session_id: str, **fields) -> None:
        if not fields:
            return
        cols, vals = [], []
        for k, v in fields.items():
            cols.append(f"{k}=?")
            vals.append(self.cipher.encrypt_json(v) if k in self.ENCRYPTED_SESSION else v)
        cols.append("updated_at=?")
        vals.append(now())
        self._exec(f"UPDATE sessions SET {', '.join(cols)} WHERE id=?", (*vals, session_id))

    def delete_session(self, session_id: str) -> None:
        for table, col in (("messages", "session_id"), ("escalations", "session_id"), ("sessions", "id")):
            self._exec(f"DELETE FROM {table} WHERE {col}=?", (session_id,))

    # messages
    def add_message(self, session_id: str, role: str, content: str, meta: dict | None = None) -> dict:
        mid, t = uuid.uuid4().hex, now()
        self._exec("INSERT INTO messages VALUES (?,?,?,?,?,?)",
                   (mid, session_id, role, self.cipher.encrypt(content), self.cipher.encrypt_json(meta or {}), t))
        return {"id": mid, "session_id": session_id, "role": role, "content": content, "meta": meta or {}, "created_at": t}

    def get_messages(self, session_id: str, after: float = 0, limit: int = 200) -> list[dict]:
        rows = self._all("SELECT * FROM messages WHERE session_id=? AND created_at>? ORDER BY created_at ASC LIMIT ?",
                         (session_id, after, limit))
        return [{**dict(r), "content": self.cipher.decrypt(r["content"]),
                 "meta": self.cipher.decrypt_json(r["meta"], {})} for r in rows]

    # escalations
    def _esc_row(self, row) -> dict:
        d = dict(row)
        d["reason"] = self.cipher.decrypt_json(d["reason"], {})
        d["notes"] = self.cipher.decrypt(d["notes"]) if d["notes"] else ""
        return d

    def create_escalation(self, session_id: str, level: str, reason: dict, priority: str) -> dict:
        eid, t = uuid.uuid4().hex, now()
        self._exec("INSERT INTO escalations (id, session_id, level, priority, status, reason, created_at, updated_at) "
                   "VALUES (?,?,?,?,?,?,?,?)",
                   (eid, session_id, level, priority, "open", self.cipher.encrypt_json(reason), t, t))
        return self.get_escalation(eid)

    def get_open_escalation(self, session_id: str) -> dict | None:
        row = self._one("SELECT * FROM escalations WHERE session_id=? AND status IN ('open','claimed') "
                        "ORDER BY created_at DESC LIMIT 1", (session_id,))
        return self._esc_row(row) if row else None

    def list_escalations(self, status: str | None = None) -> list[dict]:
        if status:
            rows = self._all("SELECT * FROM escalations WHERE status=? ORDER BY created_at DESC", (status,))
        else:
            rows = self._all("SELECT * FROM escalations ORDER BY created_at DESC LIMIT 200")
        return [self._esc_row(r) for r in rows]

    def get_escalation(self, escalation_id: str) -> dict | None:
        row = self._one("SELECT * FROM escalations WHERE id=?", (escalation_id,))
        return self._esc_row(row) if row else None

    def update_escalation(self, escalation_id: str, **fields) -> None:
        if "notes" in fields:
            fields["notes"] = self.cipher.encrypt(fields["notes"] or "")
        cols = [f"{k}=?" for k in fields] + ["updated_at=?"]
        self._exec(f"UPDATE escalations SET {', '.join(cols)} WHERE id=?",
                   (*fields.values(), now(), escalation_id))


# --------------------------------------------------------------------------- Firestore
class FirestoreStore:
    """Firestore implementation (used on Cloud Run). Same encryption guarantees."""

    def __init__(self, cipher: Cipher, project: str | None = None):
        from google.cloud import firestore  # imported lazily: optional dependency

        self.cipher = cipher
        self.fs = firestore
        self.db = firestore.Client(project=project or None)
        self.sessions = self.db.collection("sessions")
        self.escalations = self.db.collection("escalations")

    def _enc_session(self, d: dict) -> dict:
        return {k: (self.cipher.encrypt_json(v) if k in SQLiteStore.ENCRYPTED_SESSION else v) for k, v in d.items()}

    def _dec_session(self, snap) -> dict:
        d = snap.to_dict()
        d.pop("token_hash", None)
        d["id"] = snap.id
        for k, default in (("cbt_state", None), ("profile", None), ("risk_history", []), ("consent", {})):
            d[k] = self.cipher.decrypt_json(d.get(k, ""), default) if d.get(k) else default
        return d

    def create_session(self, consent: dict) -> tuple[dict, str]:
        sid, token, t = uuid.uuid4().hex, secrets.token_urlsafe(32), now()
        self.sessions.document(sid).set({
            "token_hash": hash_token(token), "created_at": t, "updated_at": t, "status": "active",
            "mode": "ai", "risk_level": "none", "risk_history": self.cipher.encrypt_json([]),
            "cbt_state": "", "profile": "", "consent": self.cipher.encrypt_json(consent),
        })
        return self.get_session(sid), token

    def get_session(self, session_id: str) -> dict | None:
        snap = self.sessions.document(session_id).get()
        return self._dec_session(snap) if snap.exists else None

    def verify_session_token(self, session_id: str, token: str) -> bool:
        snap = self.sessions.document(session_id).get()
        return bool(snap.exists and token and
                    secrets.compare_digest(snap.to_dict()["token_hash"], hash_token(token)))

    def update_session(self, session_id: str, **fields) -> None:
        self.sessions.document(session_id).update({**self._enc_session(fields), "updated_at": now()})

    def delete_session(self, session_id: str) -> None:
        ref = self.sessions.document(session_id)
        for m in ref.collection("messages").stream():
            m.reference.delete()
        for e in self.escalations.where("session_id", "==", session_id).stream():
            e.reference.delete()
        ref.delete()

    def add_message(self, session_id: str, role: str, content: str, meta: dict | None = None) -> dict:
        mid, t = uuid.uuid4().hex, now()
        self.sessions.document(session_id).collection("messages").document(mid).set({
            "role": role, "content": self.cipher.encrypt(content),
            "meta": self.cipher.encrypt_json(meta or {}), "created_at": t,
        })
        return {"id": mid, "session_id": session_id, "role": role, "content": content, "meta": meta or {}, "created_at": t}

    def get_messages(self, session_id: str, after: float = 0, limit: int = 200) -> list[dict]:
        q = (self.sessions.document(session_id).collection("messages")
             .where("created_at", ">", after).order_by("created_at").limit(limit))
        out = []
        for s in q.stream():
            d = s.to_dict()
            out.append({"id": s.id, "session_id": session_id, "role": d["role"],
                        "content": self.cipher.decrypt(d["content"]),
                        "meta": self.cipher.decrypt_json(d.get("meta", ""), {}), "created_at": d["created_at"]})
        return out

    def _dec_esc(self, snap) -> dict:
        d = snap.to_dict()
        d["id"] = snap.id
        d["reason"] = self.cipher.decrypt_json(d.get("reason", ""), {})
        d["notes"] = self.cipher.decrypt(d["notes"]) if d.get("notes") else ""
        return d

    def create_escalation(self, session_id: str, level: str, reason: dict, priority: str) -> dict:
        eid, t = uuid.uuid4().hex, now()
        self.escalations.document(eid).set({
            "session_id": session_id, "level": level, "priority": priority, "status": "open",
            "reason": self.cipher.encrypt_json(reason), "counselor": "", "notes": "",
            "created_at": t, "updated_at": t,
        })
        return self.get_escalation(eid)

    def get_open_escalation(self, session_id: str) -> dict | None:
        q = (self.escalations.where("session_id", "==", session_id)
             .where("status", "in", ["open", "claimed"]).limit(1))
        snaps = list(q.stream())
        return self._dec_esc(snaps[0]) if snaps else None

    def list_escalations(self, status: str | None = None) -> list[dict]:
        q = self.escalations
        if status:
            q = q.where("status", "==", status)
        items = [self._dec_esc(s) for s in q.limit(200).stream()]
        return sorted(items, key=lambda e: e["created_at"], reverse=True)

    def get_escalation(self, escalation_id: str) -> dict | None:
        snap = self.escalations.document(escalation_id).get()
        return self._dec_esc(snap) if snap.exists else None

    def update_escalation(self, escalation_id: str, **fields) -> None:
        if "notes" in fields:
            fields["notes"] = self.cipher.encrypt(fields["notes"] or "")
        self.escalations.document(escalation_id).update({**fields, "updated_at": now()})


def build_store(settings) -> Store:
    cipher = Cipher(settings.data_encryption_key)
    if settings.storage_backend == "firestore":
        return FirestoreStore(cipher, settings.gcp_project)
    return SQLiteStore(settings.sqlite_path, cipher)
