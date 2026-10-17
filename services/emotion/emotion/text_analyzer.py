"""Text emotion analysis.

Combines VADER sentiment (valence) with a negation-aware emotion lexicon so
that text emotions live in the SAME label space as the facial expression
model on the frontend (face-api.js): this is what makes fusion possible.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer

# Shared label space with face-api.js expression net.
LABELS = ("neutral", "happy", "sad", "angry", "fearful", "disgusted", "surprised")

# Lexicon: word stem -> (emotion, weight). Stems are matched as prefixes so
# "anxious", "anxiety" both hit "anxi". Kept small & auditable on purpose.
LEXICON: dict[str, tuple[str, float]] = {
    # sad
    "sad": ("sad", 1.0), "unhapp": ("sad", 1.0), "depress": ("sad", 1.4),
    "lonel": ("sad", 1.2), "alone": ("sad", 0.8), "hopeless": ("sad", 1.5),
    "empty": ("sad", 1.0), "cry": ("sad", 1.1), "tear": ("sad", 0.7),
    "grief": ("sad", 1.3), "griev": ("sad", 1.3), "miss": ("sad", 0.5),
    "worthless": ("sad", 1.5), "tired": ("sad", 0.6), "exhaust": ("sad", 0.8),
    "heartbr": ("sad", 1.3), "lost": ("sad", 0.6), "down": ("sad", 0.5),
    "numb": ("sad", 1.0), "fail": ("sad", 0.8), "useless": ("sad", 1.2),
    "regret": ("sad", 0.9), "guilt": ("sad", 0.9), "ashamed": ("sad", 1.0),
    "suicid": ("sad", 1.6), "die": ("sad", 1.3), "dying": ("sad", 1.3), "dead": ("sad", 1.2), "kill": ("sad", 1.2),
    "hurt": ("sad", 0.9), "pain": ("sad", 0.9), "give up": ("sad", 1.2),
    # fearful / anxious
    "anxi": ("fearful", 1.3), "worr": ("fearful", 1.1), "scar": ("fearful", 1.2),
    "afraid": ("fearful", 1.2), "fear": ("fearful", 1.2), "panic": ("fearful", 1.5),
    "nervous": ("fearful", 1.0), "stress": ("fearful", 0.9), "overwhelm": ("fearful", 1.1),
    "terrif": ("fearful", 1.4), "dread": ("fearful", 1.2), "tense": ("fearful", 0.8),
    "uneasy": ("fearful", 0.8), "insecure": ("fearful", 0.8), "pressure": ("fearful", 0.7),
    # angry
    "angr": ("angry", 1.2), "anger": ("angry", 1.2), "mad": ("angry", 0.9),
    "furious": ("angry", 1.5), "rage": ("angry", 1.5), "hate": ("angry", 1.1),
    "annoy": ("angry", 0.8), "irritat": ("angry", 0.9), "frustrat": ("angry", 1.0),
    "resent": ("angry", 1.0), "unfair": ("angry", 0.8), "pissed": ("angry", 1.2),
    # disgusted
    "disgust": ("disgusted", 1.3), "gross": ("disgusted", 0.9), "sick of": ("disgusted", 0.9),
    "revolt": ("disgusted", 1.1), "repuls": ("disgusted", 1.2),
    # surprised
    "surpris": ("surprised", 1.0), "shock": ("surprised", 1.1), "unexpect": ("surprised", 0.8),
    "suddenly": ("surprised", 0.5), "wow": ("surprised", 0.8), "amaz": ("surprised", 0.8),
    # happy
    "happy": ("happy", 1.0), "glad": ("happy", 0.9), "joy": ("happy", 1.1),
    "great": ("happy", 0.7), "good": ("happy", 0.5), "better": ("happy", 0.6),
    "excit": ("happy", 1.0), "grateful": ("happy", 1.0), "thank": ("happy", 0.6),
    "calm": ("happy", 0.6), "relax": ("happy", 0.7), "proud": ("happy", 0.9),
    "hope": ("happy", 0.6), "love": ("happy", 0.8), "relief": ("happy", 0.8),
    "reliev": ("happy", 0.8), "peace": ("happy", 0.7), "content": ("happy", 0.6),
}

WHOLE_WORD = {"die", "dead", "kill", "hurt", "pain", "mad", "miss", "down", "lost", "sad", "fail",
              "good", "cry", "tear", "joy", "hope", "calm", "love", "hate", "wow", "glad", "gross", "rage"}
SHORT_SUFFIXES = {"", "s", "d", "ed", "ing", "ful", "ly", "ness", "er", "est", "y"}

NEGATORS = {"not", "no", "never", "dont", "don't", "isnt", "isn't", "wasnt", "wasn't",
            "cant", "can't", "cannot", "hardly", "barely", "nothing", "without", "aren't", "arent"}

_TOKEN = re.compile(r"[a-z']+")
_vader = SentimentIntensityAnalyzer()


@dataclass
class TextEmotion:
    emotions: dict[str, float]
    valence: float          # -1..1 (VADER compound)
    intensity: float        # 0..1, how emotionally loaded the text is
    matched_terms: list[str]

    def to_dict(self) -> dict:
        return {
            "emotions": self.emotions,
            "valence": round(self.valence, 4),
            "intensity": round(self.intensity, 4),
            "matched_terms": self.matched_terms,
        }


def _normalize(scores: dict[str, float]) -> dict[str, float]:
    total = sum(scores.values())
    if total <= 0:
        return {k: (1.0 if k == "neutral" else 0.0) for k in LABELS}
    return {k: round(scores.get(k, 0.0) / total, 4) for k in LABELS}


def analyze_text(text: str) -> TextEmotion:
    text = (text or "").strip()
    if not text:
        return TextEmotion(_normalize({}), 0.0, 0.0, [])

    lowered = text.lower()
    tokens = _TOKEN.findall(lowered)
    raw = {k: 0.0 for k in LABELS}
    matched: list[str] = []

    # Multi-word stems first.
    for stem, (emo, w) in LEXICON.items():
        if " " in stem and stem in lowered:
            raw[emo] += w
            matched.append(stem)

    for i, tok in enumerate(tokens):
        for stem, (emo, w) in LEXICON.items():
            if " " in stem or not tok.startswith(stem):
                continue
            # Short stems must be whole words (+ inflection): "die" != "diet", "mad" != "made".
            if stem in WHOLE_WORD and tok[len(stem):] not in SHORT_SUFFIXES:
                continue
            negated = any(t in NEGATORS for t in tokens[max(0, i - 3):i])
            if negated:
                # "not happy" -> sadness; "not worried" -> mild relief
                if emo == "happy":
                    raw["sad"] += w * 0.8
                else:
                    raw["happy"] += w * 0.3
                matched.append(f"not {tok}")
            else:
                raw[emo] += w
                matched.append(tok)
            break

    compound = _vader.polarity_scores(text)["compound"]

    # Let valence steer when the lexicon is silent.
    if compound <= -0.3:
        raw["sad"] += abs(compound) * 0.6
    elif compound >= 0.3:
        raw["happy"] += compound * 0.6

    lexical_mass = sum(raw.values())
    intensity = min(1.0, 0.5 * abs(compound) + 0.25 * lexical_mass)
    # Neutral absorbs whatever is not emotionally loaded.
    raw["neutral"] = max(0.15, 1.6 - lexical_mass)

    return TextEmotion(_normalize(raw), compound, intensity, matched)
