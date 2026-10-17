import os
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.append(str(ROOT.parent / "emotion"))  # appended so therapy's app.py wins

os.environ.setdefault("COUNSELOR_TOKEN", "test-counselor-token")
os.environ.setdefault("LLM_PROVIDER", "offline")

import pytest  # noqa: E402
from emotion import analyze_text, fuse  # noqa: E402

from app import create_app  # noqa: E402
from therapy.cbt import CBTDialogueManager, CBTState, detect_distortions, parse_intensity  # noqa: E402
from therapy.crypto import Cipher  # noqa: E402
from therapy.llm import OfflineTherapist  # noqa: E402
from therapy.risk import assess  # noqa: E402
from therapy.store import SQLiteStore  # noqa: E402

COUNSELOR = {"Authorization": "Bearer test-counselor-token", "X-Counselor-Name": "Dr. Rao"}


class InProcessEmotion:
    def fuse(self, text, face, samples, previous):
        return fuse(analyze_text(text), face, samples, previous)


@pytest.fixture
def ctx(tmp_path):
    store = SQLiteStore(str(tmp_path / "t.db"), Cipher(""))
    app = create_app(store=store, therapist=OfflineTherapist(seed=1), emotion_client=InProcessEmotion())
    return app.test_client(), store, tmp_path / "t.db"


def start(client, camera=True):
    r = client.post("/api/sessions", json={"accepted_terms": True, "camera_consent": camera})
    assert r.status_code == 201
    d = r.get_json()
    return d["session_id"], {"Authorization": f"Bearer {d['token']}"}


# ---------------------------------------------------------------- risk
@pytest.mark.parametrize("text,level", [
    ("I want to kill myself", "high"),
    ("I'm going to end it all tonight", "imminent"),
    ("I've been cutting myself again", "high"),
    ("I feel hopeless about everything", "moderate"),
    ("I'm so stressed about exams", "none"),
    ("I would never kill myself, I just feel tired", "low"),
    ("We discussed suicide in my psychology class today", "moderate"),  # context -> human review
    ("After class I want to kill myself", "high"),                      # context doesn't hide intent
    # passive ideation and warning signs, with no explicit "kill myself"
    ("I don't see the point anymore. I've been thinking everyone would be better off if I wasn't here.", "high"),
    ("Honestly it would be easier for everyone if I was gone", "high"),
    ("Some days I wish I wasn't alive", "high"),
    ("I've been giving away my things to friends", "high"),
    ("I've been writing goodbye letters to my family", "imminent"),
    ("I just don't see the point of any of it", "moderate"),
    ("My team would be better off if I wasn't here to slow them down", "high"),
    ("I don't want to live in this city anymore", "none"),                 # not ideation
    ("Everyone would be happier if I'm not around", "high"),
    ("I can’t go on like this", "moderate"),                     # curly apostrophe from phones
])
def test_risk_levels(text, level):
    r = assess(text)
    if text.startswith("I would never"):
        assert r.rank <= 1
    else:
        assert r.level == level, (text, r.to_dict())


def test_sustained_moderate_escalates():
    r = assess("I feel worthless", ["moderate", "moderate"])
    assert r.level == "high"


# ---------------------------------------------------------------- CBT
def test_distortions_and_intensity():
    assert "labeling" in detect_distortions("I'm such a failure")
    assert "all_or_nothing" in detect_distortions("I always mess everything up")
    assert parse_intensity("about 80 out of 100") == 80
    assert parse_intensity("7/10") == 70


def test_cbt_full_thought_record():
    m, s = CBTDialogueManager(), CBTState()
    script = [
        ("I've been feeling low", "situation"),
        ("My manager criticised my report in front of the team on Monday", "automatic_thought"),
        ("I'm such a failure and I always mess things up", "emotion_rating"),
        ("ashamed, about 85", "distortions"),
        ("yes that fits, I do that a lot", "evidence"),
        ("I did make two errors in the report", "evidence"),
        ("but my last three reports were praised by the same manager", "balanced_thought"),
        ("I made a mistake on one report but I'm generally good at my job", "rerate"),
        ("maybe 40 now", "action_plan"),
        ("I'll ask my manager for 10 minutes of feedback tomorrow", "closing"),
    ]
    for text, expected in script:
        s = m.update(s, text, m.heuristic_extract(s, text), {"distress": 0.3, "arousal": 0.1})
        assert s.stage == expected, (text, s.stage)
    assert s.slots["intensity_before"] == 85 and s.slots["intensity_after"] == 40
    assert "labeling" in s.detected_distortions


def test_grounding_interrupts_and_resumes():
    m, s = CBTDialogueManager(), CBTState(stage="evidence")
    s = m.update(s, "I can't breathe I'm panicking", {}, {"distress": 0.9, "arousal": 0.7})
    assert s.stage == "grounding" and s.resume_stage == "evidence"
    s = m.update(s, "ok", {}, {"distress": 0.6, "arousal": 0.3})
    s = m.update(s, "a bit calmer", {}, {"distress": 0.4, "arousal": 0.2})
    assert s.stage == "evidence"


def test_plain_anxiety_does_not_interrupt():
    m, s = CBTDialogueManager(), CBTState()
    s = m.update(s, "I've been really anxious and stressed lately", {"mood": "anxious"},
                 {"distress": 0.9, "arousal": 0.5, "turns": 1})
    assert s.stage == "situation"


def test_vent_mode():
    m, s = CBTDialogueManager(), CBTState(stage="situation")
    s = m.update(s, "I just want to vent right now", {}, {})
    assert s.stage == "supportive_listening"


# ---------------------------------------------------------------- API
def test_session_auth_required(ctx):
    client, _, _ = ctx
    sid, _ = start(client)
    assert client.post(f"/api/sessions/{sid}/messages", json={"text": "hi"}).status_code == 401
    bad = {"Authorization": "Bearer nope"}
    assert client.get(f"/api/sessions/{sid}", headers=bad).status_code == 401


def test_terms_required(ctx):
    client, _, _ = ctx
    assert client.post("/api/sessions", json={}).status_code == 400


def test_chat_turn_returns_emotion_and_cbt(ctx):
    client, _, _ = ctx
    sid, h = start(client)
    r = client.post(f"/api/sessions/{sid}/messages", headers=h,
                    json={"text": "I'm really anxious about work", "face": {"fearful": 0.8, "neutral": 0.2},
                          "face_samples": 8})
    d = r.get_json()
    assert r.status_code == 200 and d["mode"] == "ai"
    assert d["emotion"]["dominant"] == "fearful"
    assert d["emotion"]["modalities"]["face"] is not None
    assert d["cbt"]["stage"] == "situation"
    assert d["reply"]["content"]


def test_face_ignored_without_consent(ctx):
    client, _, _ = ctx
    sid, h = start(client, camera=False)
    d = client.post(f"/api/sessions/{sid}/messages", headers=h,
                    json={"text": "hello", "face": {"sad": 1}, "face_samples": 9}).get_json()
    assert d["emotion"]["modalities"]["face"] is None


def test_crisis_escalation_and_human_handoff(ctx):
    client, _, _ = ctx
    sid, h = start(client)
    d = client.post(f"/api/sessions/{sid}/messages", headers=h,
                    json={"text": "I want to kill myself"}).get_json()
    assert d["mode"] == "human" and d["escalation"]["priority"] == "urgent"
    assert "14416" in d["reply"]["content"]

    # AI stays silent while a human owns the session
    d2 = client.post(f"/api/sessions/{sid}/messages", headers=h, json={"text": "hello?"}).get_json()
    assert d2["reply"] is None and d2["mode"] == "human"

    # Counselor queue
    assert client.get("/api/counselor/escalations").status_code == 401
    items = client.get("/api/counselor/escalations?status=open", headers=COUNSELOR).get_json()["escalations"]
    assert len(items) == 1
    eid = items[0]["id"]
    detail = client.get(f"/api/counselor/escalations/{eid}", headers=COUNSELOR).get_json()
    assert any(m["content"] == "I want to kill myself" for m in detail["messages"])

    client.post(f"/api/counselor/escalations/{eid}/messages", headers=COUNSELOR,
                json={"text": "Hi, I'm Dr. Rao. I'm here with you."})
    msgs = client.get(f"/api/sessions/{sid}/messages", headers=h).get_json()["messages"]
    assert msgs[-1]["role"] == "counselor"

    # Resolve and hand back to the AI
    client.post(f"/api/counselor/escalations/{eid}/resolve", headers=COUNSELOR,
                json={"notes": "Safety plan made", "return_to_ai": True})
    d3 = client.post(f"/api/sessions/{sid}/messages", headers=h, json={"text": "thanks, I feel safer"}).get_json()
    assert d3["mode"] == "ai" and d3["reply"]


def test_moderate_risk_flags_review_without_pausing(ctx):
    client, store, _ = ctx
    sid, h = start(client)
    d = client.post(f"/api/sessions/{sid}/messages", headers=h, json={"text": "I feel hopeless"}).get_json()
    assert d["mode"] == "ai"
    assert store.list_escalations("open")[0]["priority"] == "review"


def test_data_encrypted_at_rest_and_deletable(ctx):
    client, _, db_path = ctx
    sid, h = start(client)
    secret = "my very private thought about Priya"
    client.post(f"/api/sessions/{sid}/messages", headers=h, json={"text": secret})
    raw = sqlite3.connect(db_path).execute("SELECT content FROM messages").fetchall()
    assert all(secret not in r[0] for r in raw)
    assert client.delete(f"/api/sessions/{sid}", headers=h).get_json()["deleted"]
    assert sqlite3.connect(db_path).execute("SELECT count(*) FROM messages").fetchone()[0] == 0


def test_langchain_therapist_builds_without_network():
    from therapy.config import Settings
    from therapy.llm import LangChainTherapist

    t = LangChainTherapist(Settings(llm_provider="gemini", google_api_key="dummy"))
    assert "gemini" in t.name


def test_langchain_prompts_render_with_engine_variables():
    """Guards against template/variable drift without calling Gemini."""
    from therapy.cbt import CBTDialogueManager, CBTState
    from therapy.config import Settings
    from therapy.llm import LangChainTherapist, _fmt_vars

    t = LangChainTherapist(Settings(llm_provider="gemini", google_api_key="dummy"))
    m = CBTDialogueManager()
    d = m.directive(CBTState(stage="evidence", slots={"automatic_thought": "I'm a failure"},
                             detected_distortions=["labeling"]))
    profile = fuse(analyze_text("I feel awful"), {"sad": 1}, 5)
    msgs = t.response_chain.first.invoke({"history": [], **_fmt_vars(d, profile, {"level": "moderate"})}).to_messages()
    text = "\n".join(str(x.content) for x in msgs)
    assert "Stage: evidence" in text and "Labeling" in text and "SAFETY" in text
    ex = t.extraction_chain.first.invoke({"stage": "evidence", "missing_slots": "evidence_for",
                                          "slots": "{}", "message": "hi"})
    assert "evidence_for" in ex.to_string()


def test_heuristic_evidence_splits_both_sides():
    mgr = CBTDialogueManager()
    state = CBTState(stage="evidence")
    out = mgr.heuristic_extract(state, "The mistakes were real. But my manager said the rest of the analysis was strong.")
    assert out == {"evidence_for": "The mistakes were real",
                   "evidence_against": "my manager said the rest of the analysis was strong."}
    # Only one side given: fill the next missing slot as before.
    assert mgr.heuristic_extract(state, "Everyone saw the mistakes") == {"evidence_for": "Everyone saw the mistakes"}


def test_extraction_model_setting(monkeypatch):
    from therapy.config import Settings

    monkeypatch.setenv("GEMINI_MODEL", "reply-model")
    monkeypatch.delenv("EXTRACTION_MODEL", raising=False)
    assert Settings().extraction_model == "gemini-3.5-flash-lite"
    monkeypatch.setenv("EXTRACTION_MODEL", "")          # empty -> reuse the reply model
    assert Settings().extraction_model == "reply-model"
    monkeypatch.setenv("EXTRACTION_MODEL", "other-model")
    assert Settings().extraction_model == "other-model"
