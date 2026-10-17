"""Safety / risk assessment.

The deterministic layer runs on every message and never depends on the LLM,
so a model outage can't silently disable crisis detection. An optional LLM
review (see engine.py) may only RAISE the risk level, never lower it.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

LEVELS = ("none", "low", "moderate", "high", "imminent")


def _rx(*patterns: str) -> list[re.Pattern]:
    return [re.compile(p, re.IGNORECASE) for p in patterns]


IMMINENT = _rx(
    r"\b(going to|gonna|about to|plan(ning)? to|decided to|will)\s+(kill|end|hang|shoot)\s+(myself|my life|it all)\b",
    r"\b(tonight|today|right now)\b.*\b(kill myself|end (my life|it all)|suicide)\b",
    r"\b(kill myself|end (my life|it all)|suicide)\b.*\b(tonight|today|right now)\b",
    r"\b(wrote|written|writing) (a |my )?(suicide|goodbye) (note|letter)s?\b",
    r"\b(have|got|bought) (the )?(pills|rope|gun|blade)s? (ready|to (do|end))\b",
    r"\bthis is (my|the) last (message|goodbye)\b",
)
HIGH = _rx(
    r"\b(kill|killing) myself\b", r"\bsuicid(e|al)\b", r"\bend(ing)? (my life|it all)\b",
    r"\b(want|wish|wanna) (to )?(die|be dead|not exist)\b", r"\btake my (own )?life\b",
    r"\bno reason to (live|go on)\b", r"\bbetter off (dead|without me)\b",
    # Passive ideation: "everyone would be better off if I wasn't here", "I wish I wasn't alive".
    r"\b(better off|happier|easier)\b.{0,40}\bif (i (wasn'?t|weren'?t|was not|were not|am not)|i'?m not) (here|around|alive)\b",
    r"\b(better off|happier|easier)\b.{0,40}\bif (i (was|were)|i'?m) (gone|dead|not here|not around)\b",
    r"\bwish i (was|were) (dead|gone|not here|not alive|never born)\b", r"\bwish i (wasn'?t|weren'?t) (here|alive|born)\b",
    r"\b(don'?t|do not) want to (be here|be alive|wake up|exist)( any ?more)?\b", r"\bdon'?t want to live any ?more\b",
    r"\bgiv(e|ing|en) away (all )?my (things|stuff|belongings|possessions)\b",
    r"\bgiv(e|ing|en) (all )?my (things|stuff|belongings|possessions) away\b",
    r"\b(cut|cutting|hurt|hurting|harm|harming|burn|burning) myself\b", r"\bself[- ]?harm\b",
    r"\boverdose\b", r"\b(kill|hurt|harm) (him|her|them|someone|people|my \w+)\b",
    r"\b(being|been|getting) (abused|hit|beaten|raped)\b",
)
MODERATE = _rx(
    r"\bhopeless\b", r"\bcan'?t go on\b", r"\bcan'?t (take|do) (it|this) any ?more\b",
    r"\bno point( in (anything|living|trying))?\b", r"\b(don'?t|can'?t|do not|cannot) see (the|any) point\b",
    r"\bwhat'?s the point\b", r"\b(everyone|everybody|people|my family) (would|'d) be better off\b", r"\bdisappear\b", r"\bgive up on (life|everything)\b",
    r"\bnobody would (care|notice|miss me)\b", r"\bworthless\b", r"\btrapped\b",
    r"\bburden (to|on) (everyone|my family|others)\b", r"\bnot eating\b", r"\bhaven'?t slept\b",
)
LOW = _rx(r"\bdepress(ed|ion)\b", r"\bpanic attack", r"\boverwhelm", r"\bcan'?t cope\b", r"\bexhausted\b")

# Explicit first-person denials ("I would never kill myself").
NEGATIONS = _rx(
    r"\b(not|never|no longer|don'?t|wouldn'?t)\s+(want(ing)? to |going to |thinking (about|of) )?(kill(ing)? myself|suicid|hurt(ing)? myself|die)\b",
)
# Third-party / academic context. We do NOT clear these (false negatives are far
# worse than false positives); we cap them at "moderate" so a human reviews them
# asynchronously while the AI keeps supporting the person.
CONTEXT = _rx(r"\b(movie|film|book|novel|show|series|song|article|essay|class|lecture|research|news|documentary)\b")
FIRST_PERSON_INTENT = _rx(r"\b(myself|my (own )?life|i (want|wish|plan|am going) to die|me dead)\b")

CATEGORY_HINTS = {
    "suicidal_ideation": _rx(r"suicid", r"kill(ing)? myself", r"end (my life|it all)", r"(want|wish) to die", r"better off dead", r"take my (own )?life",
                          r"better off\b.{0,40}\bif i", r"wish i (was|were|wasn'?t|weren'?t) (dead|gone|not|here|alive|born|never)",
                          r"don'?t want to (be here|be alive|wake up|exist|live any ?more)", r"(suicide|goodbye) (note|letter)",
                          r"giv(e|ing|en) away (all )?my|my (things|stuff|belongings|possessions) away"),
    "self_harm": _rx(r"(cut|hurt|harm|burn)(ting|ing)? myself", r"self[- ]?harm", r"overdose"),
    "harm_to_others": _rx(r"(kill|hurt|harm) (him|her|them|someone|people|my \w+)"),
    "abuse": _rx(r"(abused|hit|beaten|raped)"),
}


@dataclass
class RiskAssessment:
    level: str = "none"
    categories: list[str] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)
    source: str = "rules"

    @property
    def rank(self) -> int:
        return LEVELS.index(self.level)

    def requires_human(self) -> bool:
        return self.rank >= LEVELS.index("high")

    def needs_review(self) -> bool:
        return self.level == "moderate"

    def raise_to(self, level: str, reason: str, source: str) -> None:
        if LEVELS.index(level) > self.rank:
            self.level = level
            self.source = source
        self.reasons.append(reason)

    def to_dict(self) -> dict:
        return {"level": self.level, "categories": self.categories, "reasons": self.reasons, "source": self.source}


def assess(text: str, recent_levels: list[str] | None = None) -> RiskAssessment:
    # Phone keyboards type curly apostrophes; the patterns expect ASCII.
    text = (text or "").replace("’", "'").replace("‘", "'")
    r = RiskAssessment()
    negated = any(p.search(text) for p in NEGATIONS)

    def hits(patterns):
        # Human-readable evidence for the counselor: the phrase that matched.
        return [m.group(0) for p in patterns if (m := p.search(text))]

    if (h := hits(IMMINENT)) and not negated:
        r.raise_to("imminent", f'signs of immediate intent: "{h[0]}"', "rules")
    if (h := hits(HIGH)) and not negated:
        r.raise_to("high", f'high-risk language: "{h[0]}"', "rules")
    if h := hits(MODERATE):
        r.raise_to("moderate", f'hopelessness language: "{h[0]}"', "rules")
    if h := hits(LOW):
        r.raise_to("low", f'distress language: "{h[0]}"', "rules")

    contextual = any(p.search(text) for p in CONTEXT) and not any(p.search(text) for p in FIRST_PERSON_INTENT)
    if contextual and r.rank > LEVELS.index("moderate"):
        r.level = "moderate"
        r.reasons.append("third-party/academic context: capped at moderate for human review")

    for cat, pats in CATEGORY_HINTS.items():
        if any(p.search(text) for p in pats) and not negated:
            r.categories.append(cat)

    # Sustained moderate signals across turns warrant a human look.
    recent = recent_levels or []
    if r.level == "moderate" and sum(1 for lv in recent[-4:] if lv in ("moderate", "high")) >= 2:
        r.raise_to("high", "sustained moderate risk across recent turns", "trajectory")
    return r
