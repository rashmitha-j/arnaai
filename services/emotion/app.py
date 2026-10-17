"""ArnaAI emotion detection microservice."""
import hmac
import os

from flask import Flask, jsonify, request

try:  # load .env for local development before settings are read
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass

from emotion import LABELS, analyze_text, fuse

app = Flask(__name__)
SERVICE_KEY = os.getenv("SERVICE_KEY", "")
MAX_TEXT = 5000


@app.before_request
def require_service_key():
    if request.path == "/health" or not SERVICE_KEY:
        return None
    supplied = request.headers.get("X-Service-Key", "")
    if not hmac.compare_digest(supplied, SERVICE_KEY):
        return jsonify(error="unauthorized"), 401
    return None


@app.get("/health")
def health():
    return jsonify(status="ok", service="emotion", labels=LABELS)


@app.post("/v1/text")
def text_endpoint():
    body = request.get_json(silent=True) or {}
    text = str(body.get("text", ""))[:MAX_TEXT]
    return jsonify(analyze_text(text).to_dict())


@app.post("/v1/fuse")
def fuse_endpoint():
    body = request.get_json(silent=True) or {}
    text = str(body.get("text", ""))[:MAX_TEXT]
    face = body.get("face")
    face = face if isinstance(face, dict) else None
    try:
        samples = int(body.get("face_samples", 0))
    except (TypeError, ValueError):
        samples = 0
    previous = body.get("previous") if isinstance(body.get("previous"), dict) else None
    text_emotion = analyze_text(text)
    profile = fuse(text_emotion, face, samples, previous)
    profile["text_analysis"] = text_emotion.to_dict()
    return jsonify(profile)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "8081")), debug=False)
