"""ArnaAI therapy engine microservice (Flask)."""
from __future__ import annotations

import hmac
import logging
import os
import sys
import time
from collections import defaultdict, deque
from functools import wraps
from pathlib import Path

from flask import Flask, g, jsonify, request
from flask_cors import CORS

try:  # load .env for local development before settings are read
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass

# Allow the in-process emotion fallback when running from the monorepo.
sys.path.append(str(Path(__file__).resolve().parents[1] / "emotion"))

from therapy.config import settings  # noqa: E402
from therapy.emotion_client import EmotionClient  # noqa: E402
from therapy.engine import TherapyEngine  # noqa: E402
from therapy.escalation import EscalationService  # noqa: E402
from therapy.llm import build_therapist  # noqa: E402
from therapy.prompts import CRISIS_RESOURCES  # noqa: E402
from therapy.store import build_store  # noqa: E402

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"),
                    format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("arnaai")

MAX_MESSAGE_CHARS = 4000


def create_app(store=None, therapist=None, emotion_client=None) -> Flask:
    app = Flask(__name__)
    CORS(app, origins=settings.allowed_origins, supports_credentials=False)

    store = store or build_store(settings)
    engine = TherapyEngine(
        store=store,
        therapist=therapist or build_therapist(settings),
        emotion_client=emotion_client or EmotionClient(settings.emotion_service_url, settings.service_key),
        escalations=EscalationService(store, settings.escalation_webhook_url, os.getenv("PUBLIC_APP_URL", "")),
        history_window=settings.history_window,
    )
    app.config["engine"] = engine
    hits: dict[str, deque] = defaultdict(deque)

    # ------------------------------------------------------------ middleware
    @app.after_request
    def security_headers(resp):
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["X-Frame-Options"] = "DENY"
        resp.headers["Referrer-Policy"] = "no-referrer"
        resp.headers["Cache-Control"] = "no-store"
        return resp

    def rate_limited(fn):
        @wraps(fn)
        def wrapper(*a, **kw):
            key = request.headers.get("X-Forwarded-For", request.remote_addr or "?").split(",")[0].strip()
            q, now = hits[key], time.time()
            while q and now - q[0] > 60:
                q.popleft()
            if len(q) >= settings.rate_limit_per_minute:
                return jsonify(error="Too many messages. Please slow down a little."), 429
            q.append(now)
            return fn(*a, **kw)
        return wrapper

    def bearer() -> str:
        h = request.headers.get("Authorization", "")
        return h[7:] if h.startswith("Bearer ") else ""

    def session_auth(fn):
        @wraps(fn)
        def wrapper(session_id, *a, **kw):
            if not store.verify_session_token(session_id, bearer()):
                return jsonify(error="unauthorized"), 401
            return fn(session_id, *a, **kw)
        return wrapper

    def counselor_auth(fn):
        @wraps(fn)
        def wrapper(*a, **kw):
            token = bearer()
            if not settings.counselor_token or not hmac.compare_digest(token, settings.counselor_token):
                return jsonify(error="unauthorized"), 401
            g.counselor = request.headers.get("X-Counselor-Name", "counselor")[:80]
            return fn(*a, **kw)
        return wrapper

    # ------------------------------------------------------------ public
    @app.get("/health")
    def health():
        return jsonify(status="ok", service="therapy", model=getattr(engine.therapist, "name", "?"),
                       extraction_model=getattr(engine.therapist, "extraction_model", None),
                       storage=settings.storage_backend)

    @app.get("/api/resources")
    def resources():
        return jsonify(resources=CRISIS_RESOURCES)

    @app.post("/api/sessions")
    @rate_limited
    def create_session():
        body = request.get_json(silent=True) or {}
        consent = {"terms": bool(body.get("accepted_terms")), "camera": bool(body.get("camera_consent")),
                   "at": time.time()}
        if not consent["terms"]:
            return jsonify(error="Please accept the terms to continue."), 400
        session, token, greeting = engine.start_session(consent)
        return jsonify(session_id=session["id"], token=token, messages=[greeting]), 201

    @app.get("/api/sessions/<session_id>")
    @session_auth
    def get_session(session_id):
        s = store.get_session(session_id)
        state = s.get("cbt_state") or {}
        return jsonify(session_id=session_id, mode=s["mode"], status=s["status"],
                       cbt={"stage": state.get("stage"), "resume_stage": state.get("resume_stage"),
                            "slots": state.get("slots", {})},
                       emotion=s.get("profile"), messages=store.get_messages(session_id))

    @app.get("/api/sessions/<session_id>/messages")
    @session_auth
    def poll_messages(session_id):
        after = float(request.args.get("after", 0) or 0)
        s = store.get_session(session_id)
        return jsonify(mode=s["mode"], status=s["status"], messages=store.get_messages(session_id, after=after))

    @app.post("/api/sessions/<session_id>/messages")
    @session_auth
    @rate_limited
    def send_message(session_id):
        body = request.get_json(silent=True) or {}
        text = str(body.get("text", "")).strip()
        if not text:
            return jsonify(error="Message is empty."), 400
        if len(text) > MAX_MESSAGE_CHARS:
            return jsonify(error="Message is too long."), 400
        face = body.get("face") if isinstance(body.get("face"), dict) else None
        s = store.get_session(session_id)
        if not (s.get("consent") or {}).get("camera"):
            face = None  # never use facial data without consent
        try:
            samples = int(body.get("face_samples", 0))
        except (TypeError, ValueError):
            samples = 0
        return jsonify(engine.handle_turn(session_id, text, face, samples))

    @app.delete("/api/sessions/<session_id>")
    @session_auth
    def delete_session(session_id):
        store.delete_session(session_id)
        return jsonify(deleted=True)

    # ------------------------------------------------------------ counselor (human in the loop)
    @app.get("/api/counselor/escalations")
    @counselor_auth
    def list_escalations():
        status = request.args.get("status") or None
        items = store.list_escalations(status)
        return jsonify(escalations=items)

    @app.get("/api/counselor/escalations/<escalation_id>")
    @counselor_auth
    def escalation_detail(escalation_id):
        esc = store.get_escalation(escalation_id)
        if not esc:
            return jsonify(error="not found"), 404
        s = store.get_session(esc["session_id"]) or {}
        return jsonify(escalation=esc,
                       session={"id": esc["session_id"], "mode": s.get("mode"), "status": s.get("status"),
                                "risk_level": s.get("risk_level"), "emotion": s.get("profile"),
                                "cbt": s.get("cbt_state")},
                       messages=store.get_messages(esc["session_id"]))

    @app.post("/api/counselor/escalations/<escalation_id>/messages")
    @counselor_auth
    def counselor_message(escalation_id):
        text = str((request.get_json(silent=True) or {}).get("text", "")).strip()
        if not text:
            return jsonify(error="Message is empty."), 400
        try:
            return jsonify(message=engine.counselor_reply(escalation_id, g.counselor, text[:MAX_MESSAGE_CHARS]))
        except KeyError:
            return jsonify(error="not found"), 404

    @app.post("/api/counselor/escalations/<escalation_id>/resolve")
    @counselor_auth
    def resolve(escalation_id):
        body = request.get_json(silent=True) or {}
        try:
            esc = engine.resolve(escalation_id, str(body.get("notes", ""))[:4000], bool(body.get("return_to_ai", True)))
            return jsonify(escalation=esc)
        except KeyError:
            return jsonify(error="not found"), 404

    return app


if __name__ == "__main__":
    create_app().run(host="0.0.0.0", port=int(os.getenv("PORT", "8080")), debug=False)
