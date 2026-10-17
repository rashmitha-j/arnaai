"""Multi-modal emotion fusion with real-time temporal smoothing.

Inputs per conversational turn:
  * text emotion distribution (from text_analyzer)
  * facial expression distribution averaged over the frames captured while
    the user was typing (computed in-browser with face-api.js, so raw video
    never leaves the device)
  * the previous emotional profile for the session

The service is stateless: the caller passes `previous` back in, which lets
it scale horizontally on Cloud Run without sticky sessions.
"""
from __future__ import annotations

from .text_analyzer import LABELS, TextEmotion

# Valence / arousal coordinates for each discrete label (Russell circumplex).
CIRCUMPLEX = {
    "neutral":   (0.0, 0.0),
    "happy":     (0.8, 0.5),
    "sad":       (-0.7, -0.4),
    "angry":     (-0.6, 0.8),
    "fearful":   (-0.7, 0.7),
    "disgusted": (-0.6, 0.3),
    "surprised": (0.2, 0.8),
}
DISTRESS_WEIGHTS = {"sad": 1.0, "fearful": 1.0, "angry": 0.8, "disgusted": 0.6}

TEXT_WEIGHT = 0.6
FACE_WEIGHT = 0.4
SMOOTHING = 0.55          # weight of the newest observation in the EMA
MIN_FACE_SAMPLES = 3      # below this, face evidence is down-weighted


def _clean(dist: dict | None) -> dict[str, float] | None:
    if not dist:
        return None
    vals = {k: max(0.0, float(dist.get(k, 0.0))) for k in LABELS}
    total = sum(vals.values())
    if total <= 0:
        return None
    return {k: v / total for k, v in vals.items()}


def _incongruence(text: dict, face: dict) -> float:
    """Total-variation distance between modalities (0 = agree, 1 = disagree).

    High incongruence (e.g. 'I'm fine' + a sad face) is clinically useful:
    the therapy engine uses it to gently check in."""
    return round(0.5 * sum(abs(text[k] - face[k]) for k in LABELS), 4)


def fuse(text_emotion: TextEmotion, face: dict | None = None,
         face_samples: int = 0, previous: dict | None = None) -> dict:
    text = text_emotion.emotions
    face_dist = _clean(face)

    # Text that carries little emotion should lean more on the face, and
    # sparse face evidence should lean more on text.
    tw, fw = TEXT_WEIGHT, FACE_WEIGHT
    if face_dist is None:
        tw, fw = 1.0, 0.0
    else:
        if face_samples < MIN_FACE_SAMPLES:
            fw *= 0.5
        tw *= 0.6 + 0.4 * text_emotion.intensity
        s = tw + fw
        tw, fw = tw / s, fw / s

    current = {k: tw * text[k] + fw * (face_dist[k] if face_dist else 0.0) for k in LABELS}

    prev = _clean((previous or {}).get("emotions"))
    if prev:
        smoothed = {k: SMOOTHING * current[k] + (1 - SMOOTHING) * prev[k] for k in LABELS}
    else:
        smoothed = current
    total = sum(smoothed.values()) or 1.0
    smoothed = {k: round(v / total, 4) for k, v in smoothed.items()}

    valence = sum(smoothed[k] * CIRCUMPLEX[k][0] for k in LABELS)
    arousal = sum(smoothed[k] * CIRCUMPLEX[k][1] for k in LABELS)
    # Distress = how much negative emotion is present, scaled by how strongly it
    # is expressed (a distribution can be "all sad" while the text is mild).
    negative_mass = sum(smoothed[k] * w for k, w in DISTRESS_WEIGHTS.items())
    expressed = text_emotion.intensity if face_dist is None else \
        0.7 * text_emotion.intensity + 0.3 * max(face_dist[k] for k in DISTRESS_WEIGHTS)
    distress = min(1.0, negative_mass * (0.45 + 0.55 * expressed)
                   + max(0.0, -text_emotion.valence) * 0.15)

    dominant = max(smoothed, key=smoothed.get)
    ranked = sorted(smoothed.values(), reverse=True)
    confidence = round(ranked[0] - ranked[1] + ranked[0] * 0.5, 4)

    prev_distress = (previous or {}).get("distress")
    trend = "stable"
    if isinstance(prev_distress, (int, float)):
        delta = distress - prev_distress
        trend = "rising" if delta > 0.08 else "easing" if delta < -0.08 else "stable"

    turns = int((previous or {}).get("turns", 0)) + 1

    return {
        "emotions": smoothed,
        "dominant": dominant,
        "confidence": min(1.0, confidence),
        "valence": round(valence, 4),
        "arousal": round(arousal, 4),
        "distress": round(distress, 4),
        "distress_trend": trend,
        "incongruence": _incongruence(text, face_dist) if face_dist else None,
        "modalities": {
            "text": text,
            "face": {k: round(v, 4) for k, v in face_dist.items()} if face_dist else None,
            "weights": {"text": round(tw, 3), "face": round(fw, 3)},
        },
        "turns": turns,
    }
