"""Structured CBT dialogue manager.

Implements a thought-record protocol (Beck) as an explicit state machine:

  check_in -> situation -> automatic_thought -> emotion_rating ->
  distortions -> evidence -> balanced_thought -> rerate -> action_plan -> closing

Each stage has slots that must be filled before advancing. The LLM writes the
words; this module decides WHAT the next therapeutic move is, which keeps the
conversation evidence-based and auditable instead of free-form chat.

Adaptive delivery: the fused emotional profile can divert the flow into a
grounding exercise (high distress/arousal) or supportive listening (when the
person asks to vent), then resume the protocol where it left off.
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field

STAGES = (
    "check_in", "situation", "automatic_thought", "emotion_rating", "distortions",
    "evidence", "balanced_thought", "rerate", "action_plan", "closing",
)
SIDE_STAGES = ("grounding", "supportive_listening")

STAGE_SLOTS: dict[str, tuple[str, ...]] = {
    "check_in": ("mood",),
    "situation": ("situation",),
    "automatic_thought": ("automatic_thought",),
    "emotion_rating": ("emotion", "intensity_before"),
    "distortions": ("distortions_acknowledged",),
    "evidence": ("evidence_for", "evidence_against"),
    "balanced_thought": ("balanced_thought",),
    "rerate": ("intensity_after",),
    "action_plan": ("action_step",),
    "closing": (),
}

STAGE_GOALS: dict[str, str] = {
    "check_in": "Build rapport. Warmly ask how they are feeling today and what is on their mind.",
    "situation": "Help them describe ONE specific, recent situation that triggered the feeling (who, what, when, where). Ask for concrete detail.",
    "automatic_thought": "Elicit the automatic thought: ask, in your own natural words, what went through their mind or what they were telling themselves in that moment.",
    "emotion_rating": "Name the emotion they felt and ask them to rate its intensity from 0 to 100.",
    "distortions": "Gently and collaboratively point out the possible thinking trap(s) listed below, explain each in one plain sentence, and ask whether it fits. Never lecture.",
    "evidence": "Examine the thought like a curious detective: ask what evidence supports it AND what evidence doesn't fully support it. Get both sides.",
    "balanced_thought": "Help them write a more balanced, realistic alternative thought in their own words. Offer a starting suggestion only if they are stuck.",
    "rerate": "Ask them to re-rate the original emotion 0-100 now, after looking at the balanced thought. Reflect any change without overclaiming.",
    "action_plan": "Agree on ONE small, concrete, doable next step or coping action for the next 24-48 hours.",
    "closing": "Summarise the thought record together (situation, thought, balanced thought, rating change, next step), affirm their effort, and invite them to continue chatting or start a new one.",
    "grounding": "The person seems highly distressed right now. Pause the exercise and guide a short grounding technique (slow 4-6 breathing or 5-4-3-2-1 senses), one step at a time. Keep sentences short and calm.",
    "supportive_listening": "The person wants to be heard. Use reflective listening and validation only; no techniques, no advice, no questions that push. One gentle open question at most.",
}

# Cognitive distortion catalog (Burns / Beck) with lightweight detectors.
DISTORTIONS: dict[str, tuple[str, list[re.Pattern]]] = {
    "all_or_nothing": ("All-or-nothing thinking: seeing things in black and white.",
                       [re.compile(r"\b(always|never|everything|nothing|completely|totally|every single)\b", re.I)]),
    "catastrophizing": ("Catastrophizing: jumping to the worst possible outcome.",
                        [re.compile(r"\b(disaster|worst|ruined|terrible|end of the world|can'?t survive|horrible)\b", re.I)]),
    "mind_reading": ("Mind reading: assuming you know what others think.",
                     [re.compile(r"\b(they|he|she|everyone|people) (think|thinks|must think|probably think|hate|hates)\b", re.I)]),
    "fortune_telling": ("Fortune telling: predicting things will turn out badly.",
                        [re.compile(r"\b(will never|going to fail|won'?t ever|it'?ll (go|turn out) (badly|wrong)|bound to)\b", re.I)]),
    "labeling": ("Labeling: defining yourself by one harsh label.",
                 [re.compile(r"\bi'?m (such )?(a |an )?(failure|loser|idiot|stupid|worthless|useless|disappointment|mess)\b", re.I)]),
    "should_statements": ("Should statements: rigid rules about how you or others must be.",
                          [re.compile(r"\b(should|shouldn'?t|must|have to|ought to)\b", re.I)]),
    "personalization": ("Personalization: blaming yourself for things outside your control.",
                        [re.compile(r"\b(my fault|because of me|i ruined|i caused|blame myself)\b", re.I)]),
    "overgeneralization": ("Overgeneralization: one event becomes a never-ending pattern.",
                           [re.compile(r"\b(this always happens|every time|nobody ever|no one ever)\b", re.I)]),
    "emotional_reasoning": ("Emotional reasoning: 'I feel it, so it must be true.'",
                            [re.compile(r"\bi feel (like )?(a failure|stupid|worthless|unlovable|like nobody)\b", re.I)]),
}

VENT_PATTERN = re.compile(r"\b(just (want|need) to (vent|talk|be heard)|just listen|don'?t (want|need) advice)\b", re.I)
ACUTE_PATTERN = re.compile(
    r"\b(panic(king)?|panic attack|can'?t breathe|hyperventilat\w*|heart (is )?(racing|pounding)|shaking|freaking out|"
    r"spiral(l?ing)?|losing (it|control|my mind)|going crazy)\b", re.I)
EVIDENCE_SPLIT = re.compile(r"[.,;]?\s+\b(?:but|however|although|on the other hand)\b[,]?\s*", re.I)
SKIP_PATTERN = re.compile(r"\b(skip|move on|next step|let'?s continue|go on)\b", re.I)
NUMBER = re.compile(r"\b(100|[1-9]?\d)\s*(%|/ ?100|percent|out of 100)?", re.I)


def detect_distortions(text: str) -> list[str]:
    return [name for name, (_, pats) in DISTORTIONS.items() if any(p.search(text or "") for p in pats)]


def describe_distortions(names: list[str]) -> list[str]:
    return [DISTORTIONS[n][0] for n in names if n in DISTORTIONS]


def parse_intensity(text: str) -> int | None:
    m = NUMBER.search(text or "")
    if not m:
        return None
    val = int(m.group(1))
    if m.group(2) is None and val <= 10 and re.search(r"\bout of 10\b|/ ?10\b", text, re.I):
        val *= 10
    return max(0, min(100, val))


@dataclass
class CBTState:
    stage: str = "check_in"
    resume_stage: str | None = None
    slots: dict = field(default_factory=dict)
    detected_distortions: list[str] = field(default_factory=list)
    turns_in_stage: int = 0
    grounding_done_at_turn: int = -100
    total_turns: int = 0
    completed_records: int = 0

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict | None) -> "CBTState":
        if not data:
            return cls()
        known = {k: v for k, v in data.items() if k in cls.__dataclass_fields__}
        return cls(**known)


class CBTDialogueManager:
    """Decides the next therapeutic move; does not generate text."""

    MAX_TURNS_PER_STAGE = 3   # avoid getting stuck if a slot is never cleanly filled
    GROUNDING_COOLDOWN = 6

    def heuristic_extract(self, state: CBTState, text: str) -> dict:
        """Offline slot filling used when no LLM extraction is available."""
        text = (text or "").strip()
        out: dict = {}
        stage = state.stage
        long_enough = len(text.split()) >= 3
        if stage == "check_in" and text:
            out["mood"] = text
        elif stage == "situation" and long_enough:
            out["situation"] = text
        elif stage == "automatic_thought" and long_enough:
            out["automatic_thought"] = text
        elif stage == "emotion_rating":
            if (n := parse_intensity(text)) is not None:
                out["intensity_before"] = n
            words = re.findall(r"[a-z]+", text.lower())
            if words:
                out["emotion"] = next((w for w in words if w in EMOTION_WORDS), words[0])
        elif stage == "distortions" and text:
            out["distortions_acknowledged"] = text
        elif stage == "evidence" and long_enough:
            # "X is true. But Y..." gives both sides in one message.
            parts = EVIDENCE_SPLIT.split(text, maxsplit=1)
            if len(parts) == 2 and all(len(p.split()) >= 2 for p in parts):
                if "evidence_for" not in state.slots:
                    out["evidence_for"] = parts[0].strip(" .,;")
                out["evidence_against"] = parts[1].strip()
            else:
                key = "evidence_for" if "evidence_for" not in state.slots else "evidence_against"
                out[key] = text
        elif stage == "balanced_thought" and long_enough:
            out["balanced_thought"] = text
        elif stage == "rerate":
            if (n := parse_intensity(text)) is not None:
                out["intensity_after"] = n
        elif stage == "action_plan" and long_enough:
            out["action_step"] = text
        return out

    def update(self, state: CBTState, user_text: str, extracted: dict, profile: dict | None) -> CBTState:
        state.total_turns += 1
        for k, v in (extracted or {}).items():
            if v not in (None, "", []):
                state.slots[k] = v

        if state.stage == "automatic_thought" or "automatic_thought" in extracted:
            found = detect_distortions(state.slots.get("automatic_thought", "") + " " + user_text)
            state.detected_distortions = sorted(set(state.detected_distortions) | set(found))

        profile = profile or {}
        distress = float(profile.get("distress", 0) or 0)
        arousal = float(profile.get("arousal", 0) or 0)

        # Side-stage: supportive listening on request.
        if VENT_PATTERN.search(user_text or ""):
            if state.stage not in SIDE_STAGES:
                state.resume_stage = state.stage
            state.stage, state.turns_in_stage = "supportive_listening", 0
            return state

        # Side-stage: grounding when the fused profile shows acute distress.
        # Grounding only for ACUTE distress: panic language right now, or high
        # distress that has kept rising over several turns. Merely saying
        # "I'm anxious" should be explored, not interrupted.
        acute = bool(ACUTE_PATTERN.search(user_text or "")) and distress >= 0.5
        sustained = (distress >= 0.8 and arousal >= 0.4 and profile.get("distress_trend") == "rising"
                     and int(profile.get("turns", 0) or 0) >= 3)
        if ((acute or sustained) and state.stage not in SIDE_STAGES
                and state.total_turns - state.grounding_done_at_turn > self.GROUNDING_COOLDOWN):
            state.resume_stage = state.stage
            state.stage, state.turns_in_stage = "grounding", 0
            state.grounding_done_at_turn = state.total_turns
            return state

        if state.stage in SIDE_STAGES:
            state.turns_in_stage += 1
            done = (state.stage == "grounding" and state.turns_in_stage >= 2 and distress < 0.7) or \
                   (state.stage == "supportive_listening" and state.turns_in_stage >= 3) or \
                   SKIP_PATTERN.search(user_text or "")
            if done:
                state.stage = state.resume_stage or "check_in"
                state.resume_stage, state.turns_in_stage = None, 0
            return state

        if state.stage == "closing":
            # A new topic after closing starts a fresh thought record.
            if len((user_text or "").split()) >= 4:
                state.completed_records += 1
                state.slots, state.detected_distortions = {}, []
                state.stage, state.turns_in_stage = "situation", 0
            return state

        state.turns_in_stage += 1
        filled = all(k in state.slots for k in STAGE_SLOTS[state.stage])
        stuck = state.turns_in_stage >= self.MAX_TURNS_PER_STAGE
        if filled or stuck or SKIP_PATTERN.search(user_text or ""):
            state.stage = self._next_stage(state)
            state.turns_in_stage = 0
        return state

    def _next_stage(self, state: CBTState) -> str:
        idx = STAGES.index(state.stage)
        nxt = STAGES[min(idx + 1, len(STAGES) - 1)]
        if nxt == "distortions" and not state.detected_distortions:
            nxt = "evidence"
        return nxt

    def directive(self, state: CBTState) -> dict:
        """Everything the generator needs to know about the next move."""
        missing = [k for k in STAGE_SLOTS.get(state.stage, ()) if k not in state.slots]
        return {
            "stage": state.stage,
            "goal": STAGE_GOALS[state.stage],
            "missing_slots": missing,
            "slots": state.slots,
            "distortions": describe_distortions(state.detected_distortions),
            "resume_stage": state.resume_stage,
            "progress": self.progress(state),
        }

    @staticmethod
    def progress(state: CBTState) -> float:
        stage = state.resume_stage if state.stage in SIDE_STAGES else state.stage
        stage = stage or "check_in"
        return round(STAGES.index(stage) / (len(STAGES) - 1), 2)


EMOTION_WORDS = {
    "sad", "anxious", "angry", "scared", "afraid", "worried", "ashamed", "guilty", "lonely",
    "frustrated", "hopeless", "hurt", "embarrassed", "nervous", "stressed", "overwhelmed",
    "jealous", "disappointed", "panicked", "irritated", "empty", "numb", "upset",
}


def asdict_state(state: CBTState) -> dict:
    return state.to_dict()
