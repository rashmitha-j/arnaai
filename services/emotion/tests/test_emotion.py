import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ["SERVICE_KEY"] = "test-service-key"  # set before import so a local .env can't override it

from app import app  # noqa: E402
from emotion import analyze_text, fuse  # noqa: E402


def test_sad_text_detected():
    r = analyze_text("I feel so lonely and hopeless lately")
    assert max(r.emotions, key=r.emotions.get) == "sad"
    assert r.valence < 0


def test_anxiety_maps_to_fearful():
    r = analyze_text("I'm really anxious and worried about my exams")
    assert max(r.emotions, key=r.emotions.get) == "fearful"


def test_negation_flips_happy():
    r = analyze_text("I am not happy at all")
    assert r.emotions["sad"] > r.emotions["happy"]


def test_neutral_text():
    r = analyze_text("I went to the store today")
    assert max(r.emotions, key=r.emotions.get) == "neutral"


def test_fusion_uses_face_and_flags_incongruence():
    text = analyze_text("I'm fine")
    face = {"sad": 0.9, "neutral": 0.1}
    p = fuse(text, face, face_samples=10)
    assert p["modalities"]["weights"]["face"] > 0
    assert p["incongruence"] > 0.4
    assert p["emotions"]["sad"] > 0.2


def test_smoothing_and_trend():
    first = fuse(analyze_text("I am happy and calm"))
    second = fuse(analyze_text("I feel hopeless, depressed and alone"), previous=first)
    assert second["distress"] > first["distress"]
    assert second["distress_trend"] == "rising"
    assert second["turns"] == 2


def test_api_fuse_endpoint():
    c = app.test_client()
    body = {"text": "I'm so stressed", "face": {"fearful": 1}, "face_samples": 5}
    assert c.post("/v1/fuse", json=body).status_code == 401
    r = c.post("/v1/fuse", json=body, headers={"X-Service-Key": "test-service-key"})
    assert r.status_code == 200
    assert r.get_json()["dominant"] == "fearful"
