"""LangChain chains: slot extraction + response generation.

Two providers are supported through `langchain-google-genai`:
  * gemini  -> Google AI Studio API key (GOOGLE_API_KEY)
  * vertex  -> Vertex AI on GCP using the service account (Cloud Run identity)
plus an `offline` template generator so the whole system runs (and can be
tested) without any API key.
"""
from __future__ import annotations

import json
import logging
import random
from typing import Literal, Optional

from pydantic import BaseModel, Field

from .prompts import EXTRACTION_PROMPT, SYSTEM_PROMPT, TURN_INSTRUCTIONS

log = logging.getLogger(__name__)


class Extraction(BaseModel):
    mood: Optional[str] = None
    situation: Optional[str] = None
    automatic_thought: Optional[str] = None
    emotion: Optional[str] = None
    intensity_before: Optional[int] = Field(default=None, ge=0, le=100)
    distortions_acknowledged: Optional[str] = None
    evidence_for: Optional[str] = None
    evidence_against: Optional[str] = None
    balanced_thought: Optional[str] = None
    intensity_after: Optional[int] = Field(default=None, ge=0, le=100)
    action_step: Optional[str] = None
    risk_concern: Literal["none", "moderate", "high"] = "none"


def _fmt_vars(directive: dict, profile: dict, risk: dict) -> dict:
    p = profile or {}
    resume = (f"(This is a pause. Afterwards we will return to the '{directive['resume_stage']}' stage.)"
              if directive.get("resume_stage") else "")
    risk_note = ""
    if risk.get("level") == "moderate":
        risk_note = ("SAFETY: the user expressed some hopelessness. Validate it, gently ask how they are "
                     "doing with it, and remind them a human counselor is available. Do not push the exercise.")
    inc = p.get("incongruence")
    return {
        "stage": directive["stage"],
        "goal": directive["goal"],
        "missing_slots": ", ".join(directive["missing_slots"]) or "none",
        "slots": json.dumps(directive["slots"], ensure_ascii=False) if directive["slots"] else "empty",
        "distortions": "; ".join(directive["distortions"]) or "none detected",
        "resume_note": resume,
        "dominant": p.get("dominant", "unknown"),
        "confidence": p.get("confidence", "n/a"),
        "distress": p.get("distress", "n/a"),
        "distress_trend": p.get("distress_trend", "n/a"),
        "valence": p.get("valence", "n/a"),
        "arousal": p.get("arousal", "n/a"),
        "incongruence": "camera off" if inc is None else inc,
        "risk_note": risk_note,
    }


class LangChainTherapist:
    def __init__(self, settings):
        from langchain_core.output_parsers import StrOutputParser
        from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
        from langchain_google_genai import ChatGoogleGenerativeAI

        kwargs = {"model": settings.gemini_model, "temperature": settings.temperature,
                  "timeout": settings.llm_timeout, "max_retries": settings.llm_max_retries}
        if settings.llm_provider == "vertex":
            kwargs.update(vertexai=True, project=settings.gcp_project, location=settings.gcp_location)
        else:
            kwargs["google_api_key"] = settings.google_api_key
        self.llm = ChatGoogleGenerativeAI(**kwargs)
        # Slot extraction is a simple structured task: a cheaper model with low thinking keeps it fast.
        self.extractor_llm = ChatGoogleGenerativeAI(**{**kwargs, "model": settings.extraction_model,
                                                       "temperature": 0, "thinking_level": "low"})
        self.extraction_model = settings.extraction_model

        self.response_chain = (
            ChatPromptTemplate.from_messages([
                ("system", SYSTEM_PROMPT),
                MessagesPlaceholder("history"),
                ("system", TURN_INSTRUCTIONS),
            ])
            | self.llm
            | StrOutputParser()
        )
        self.extraction_chain = (
            ChatPromptTemplate.from_template(EXTRACTION_PROMPT)
            | self.extractor_llm.with_structured_output(Extraction)
        )
        self.name = f"{settings.llm_provider}:{settings.gemini_model}"

    def extract(self, message: str, directive: dict) -> Extraction | None:
        try:
            return self.extraction_chain.invoke({
                "stage": directive["stage"],
                "missing_slots": ", ".join(directive["missing_slots"]) or "none",
                "slots": json.dumps(directive["slots"], ensure_ascii=False),
                "message": message,
            })
        except Exception as exc:  # network / quota / parse errors must not break the turn
            log.warning("extraction failed, falling back to heuristics: %s", exc)
            return None

    def respond(self, history: list[dict], directive: dict, profile: dict, risk: dict) -> str:
        from langchain_core.messages import AIMessage, HumanMessage

        msgs = []
        for m in history:
            if m["role"] == "user":
                msgs.append(HumanMessage(m["content"]))
            elif m["role"] in ("assistant", "counselor"):
                prefix = "[Human counselor] " if m["role"] == "counselor" else ""
                msgs.append(AIMessage(prefix + m["content"]))
        return self.response_chain.invoke({"history": msgs, **_fmt_vars(directive, profile, risk)}).strip()


class OfflineTherapist:
    """Deterministic template generator: no API key needed, used for demos/tests
    and as an automatic fallback if the LLM call fails."""

    name = "offline-templates"

    OPENERS = {
        "high": ["That sounds really heavy.", "I can hear how hard this is.", "Thank you for sharing something so painful."],
        "mid": ["That makes sense.", "I hear you.", "Thanks for putting that into words."],
        "low": ["Thanks for sharing.", "Got it.", "Okay."],
    }
    QUESTIONS = {
        "check_in": "What's been on your mind lately?",
        "situation": "Can you tell me about one specific moment recently when this feeling showed up? Where were you and what happened?",
        "automatic_thought": "In that moment, what went through your mind? What were you telling yourself?",
        "emotion_rating": "What emotion did you feel most strongly then, and how intense was it from 0 to 100?",
        "distortions": "Does that sound like it might be part of what's happening here?",
        "evidence": "Let's look at that thought together. What makes it feel true, and what might not fully fit with it?",
        "balanced_thought": "If a close friend had this thought, what kinder, more balanced way of seeing it might you offer them?",
        "rerate": "Holding that balanced thought in mind, how intense does the original feeling seem now, from 0 to 100?",
        "action_plan": "What's one small step you could take in the next day or two to look after yourself here?",
        "closing": "Is there anything else you'd like to talk through?",
        "grounding": "Let's slow down together. Breathe in gently for 4 counts, and out for 6. Can you name 3 things you can see around you right now?",
        "supportive_listening": "I'm here and I'm listening. Take all the time you need.",
    }

    def __init__(self, seed: int | None = None):
        self.rng = random.Random(seed)

    def extract(self, message: str, directive: dict) -> None:
        return None

    def respond(self, history: list[dict], directive: dict, profile: dict, risk: dict) -> str:
        distress = float((profile or {}).get("distress") or 0)
        band = "high" if distress >= 0.6 else "mid" if distress >= 0.3 else "low"
        opener = self.rng.choice(self.OPENERS[band])
        stage = directive["stage"]
        body = ""
        if stage == "distortions" and directive["distortions"]:
            body = " One pattern I notice: " + directive["distortions"][0]
        elif stage == "closing":
            s = directive["slots"]
            parts = []
            if s.get("automatic_thought") and s.get("balanced_thought"):
                parts.append(f'We took the thought "{s["automatic_thought"]}" and found a more balanced one: "{s["balanced_thought"]}".')
            if s.get("intensity_before") is not None and s.get("intensity_after") is not None:
                parts.append(f"The feeling moved from {s['intensity_before']} to {s['intensity_after']}.")
            if s.get("action_step"):
                parts.append(f"Your next step: {s['action_step']}.")
            body = " " + " ".join(parts) + " That took real effort, well done." if parts else " You did good work today."
        incongruence = (profile or {}).get("incongruence")
        check = ""
        if incongruence is not None and incongruence > 0.5 and stage not in ("grounding",):
            check = " I want to gently check in too: how are you really doing right now?"
            return f"{opener}{body}{check}"
        if risk.get("level") == "moderate":
            return (f"{opener} Feeling that way is exhausting, and you don't have to hold it alone. "
                    "A human counselor is available if you'd like one. How are you doing with these feelings right now?")
        return f"{opener}{body} {self.QUESTIONS[stage]}".strip()


def build_therapist(settings):
    provider = settings.llm_provider
    if provider == "offline" or (provider == "gemini" and not settings.google_api_key):
        if provider != "offline":
            log.warning("GOOGLE_API_KEY not set: running with the offline template therapist.")
        return OfflineTherapist()
    return LangChainTherapist(settings)
