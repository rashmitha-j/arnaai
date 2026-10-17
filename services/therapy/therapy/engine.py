"""Therapy engine: orchestrates one conversational turn.

    user message (+ face signal)
        │
        ├─► risk assessment (rules, every turn) ───────────────┐
        ├─► emotion service: fuse text + face, smoothed         │
        │                                                       ▼
        │                               high/imminent? ─► escalate to human, pause AI,
        │                                                 deterministic safety reply
        ├─► LLM slot extraction (+ secondary risk check, can only raise risk)
        ├─► CBT dialogue manager: update state, pick next move
        └─► LangChain response chain (Gemini) with move + emotional profile
"""
from __future__ import annotations

import logging
import time

from .cbt import CBTDialogueManager, CBTState
from .llm import OfflineTherapist
from .prompts import GREETING, crisis_message
from .risk import LEVELS, assess

log = logging.getLogger(__name__)


class TherapyEngine:
    def __init__(self, store, therapist, emotion_client, escalations, history_window: int = 12):
        self.store = store
        self.therapist = therapist
        self.fallback = OfflineTherapist()
        self.emotion = emotion_client
        self.escalations = escalations
        self.cbt = CBTDialogueManager()
        self.history_window = history_window

    # ------------------------------------------------------------------ sessions
    def start_session(self, consent: dict) -> tuple[dict, str, dict]:
        session, token = self.store.create_session(consent)
        state = CBTState()
        self.store.update_session(session["id"], cbt_state=state.to_dict())
        greeting = self.store.add_message(session["id"], "assistant", GREETING, {"stage": state.stage})
        return session, token, greeting

    # ------------------------------------------------------------------ turns
    def handle_turn(self, session_id: str, text: str, face: dict | None = None, face_samples: int = 0) -> dict:
        t0 = time.time()
        session = self.store.get_session(session_id)
        if not session:
            raise KeyError(session_id)

        state = CBTState.from_dict(session.get("cbt_state"))
        recent_levels = session.get("risk_history") or []

        risk = assess(text, recent_levels)
        profile = self.emotion.fuse(text, face, face_samples, session.get("profile"))
        user_msg = self.store.add_message(session_id, "user", text, {
            "emotion": {k: profile.get(k) for k in ("dominant", "distress", "valence", "arousal", "incongruence")},
            "risk": risk.level,
        })

        # Session already handed to a human: the AI stays silent.
        if session.get("mode") == "human":
            # Still escalate further if risk increases while waiting.
            if risk.rank >= LEVELS.index("moderate"):
                self.escalations.escalate(session_id, risk.to_dict(), profile)
            self._save(session_id, state, profile, recent_levels, risk)
            return self._result(session_id, None, user_msg, state, profile, risk, mode="human", t0=t0)

        # Secondary LLM review + slot extraction (skipped if rules already say high).
        extracted: dict = {}
        if not risk.requires_human():
            directive_before = self.cbt.directive(state)
            ex = self.therapist.extract(text, directive_before)
            if ex is not None:
                extracted = {k: v for k, v in ex.model_dump().items() if k != "risk_concern" and v is not None}
                if ex.risk_concern == "high":
                    risk.raise_to("high", "LLM safety review flagged high risk", "llm")
                elif ex.risk_concern == "moderate":
                    risk.raise_to("moderate", "LLM safety review flagged moderate risk", "llm")
            else:
                extracted = self.cbt.heuristic_extract(state, text)

        # ---- Human-in-the-loop path
        if risk.requires_human():
            esc = self.escalations.escalate(session_id, risk.to_dict(), profile)
            reply_text = crisis_message(imminent=risk.level == "imminent")
            reply = self.store.add_message(session_id, "assistant", reply_text,
                                           {"stage": "safety", "escalation_id": esc["id"]})
            self.store.update_session(session_id, mode="human", status="escalated")
            self._save(session_id, state, profile, recent_levels, risk)
            return self._result(session_id, reply, user_msg, state, profile, risk, mode="human",
                                escalation=esc, t0=t0)

        if risk.needs_review():
            self.escalations.escalate(session_id, risk.to_dict(), profile)

        # ---- CBT path
        state = self.cbt.update(state, text, extracted, profile)
        directive = self.cbt.directive(state)
        history = self.store.get_messages(session_id)[-self.history_window:]
        model = getattr(self.therapist, "name", "")
        try:
            reply_text = self.therapist.respond(history, directive, profile, risk.to_dict())
        except Exception as exc:
            log.error("LLM generation failed, using offline fallback: %s", exc)
            reply_text = self.fallback.respond(history, directive, profile, risk.to_dict())
            model = self.fallback.name
        reply = self.store.add_message(session_id, "assistant", reply_text,
                                       {"stage": state.stage, "model": model})
        self._save(session_id, state, profile, recent_levels, risk)
        return self._result(session_id, reply, user_msg, state, profile, risk, mode="ai", t0=t0)

    # ------------------------------------------------------------------ helpers
    def _save(self, session_id, state, profile, recent_levels, risk):
        history = (recent_levels + [risk.level])[-20:]
        top = max(history, key=LEVELS.index)
        self.store.update_session(session_id, cbt_state=state.to_dict(), profile=profile,
                                  risk_history=history, risk_level=top)

    def _result(self, session_id, reply, user_msg, state, profile, risk, mode, escalation=None, t0=0.0):
        return {
            "session_id": session_id,
            "mode": mode,
            "reply": reply,
            "user_message": user_msg,
            "cbt": {"stage": state.stage, "resume_stage": state.resume_stage,
                    "progress": self.cbt.progress(state), "slots": state.slots,
                    "distortions": state.detected_distortions},
            "emotion": profile,
            "risk": {"level": risk.level},
            "escalation": {"id": escalation["id"], "priority": escalation["priority"]} if escalation else None,
            "latency_ms": int((time.time() - t0) * 1000),
        }

    # ------------------------------------------------------------------ counselor
    def counselor_reply(self, escalation_id: str, counselor: str, text: str) -> dict:
        esc = self.store.get_escalation(escalation_id)
        if not esc:
            raise KeyError(escalation_id)
        if esc["status"] == "open":
            self.store.update_escalation(escalation_id, status="claimed", counselor=counselor)
        self.store.update_session(esc["session_id"], mode="human")
        return self.store.add_message(esc["session_id"], "counselor", text, {"counselor": counselor})

    def resolve(self, escalation_id: str, notes: str, return_to_ai: bool) -> dict:
        esc = self.store.get_escalation(escalation_id)
        if not esc:
            raise KeyError(escalation_id)
        self.store.update_escalation(escalation_id, status="resolved", notes=notes)
        if return_to_ai:
            self.store.update_session(esc["session_id"], mode="ai", status="active")
            self.store.add_message(esc["session_id"], "assistant",
                                   "I'm back with you. Thank you for talking with the counselor. "
                                   "We can go at whatever pace feels right. What would help most right now?",
                                   {"stage": "handback"})
        else:
            self.store.update_session(esc["session_id"], status="closed")
        return self.store.get_escalation(escalation_id)
