"""Client for the emotion microservice, with a local fallback.

If the emotion service is unreachable the turn still completes: we import
the same analyzer in-process when available, else return a neutral profile.
"""
from __future__ import annotations

import logging

import requests

log = logging.getLogger(__name__)

NEUTRAL = {"emotions": {"neutral": 1.0}, "dominant": "neutral", "confidence": 0.0, "valence": 0.0,
           "arousal": 0.0, "distress": 0.0, "distress_trend": "stable", "incongruence": None, "turns": 0}


class EmotionClient:
    def __init__(self, base_url: str, service_key: str = "", timeout: float = 4.0):
        self.base_url = base_url.rstrip("/")
        self.headers = {"X-Service-Key": service_key} if service_key else {}
        self.timeout = timeout
        self._id_token_audience = base_url if base_url.startswith("https://") else None

    def _auth_headers(self) -> dict:
        headers = dict(self.headers)
        # On Cloud Run, authenticate service-to-service with a Google-signed ID token.
        if self._id_token_audience:
            try:
                import google.auth.transport.requests
                import google.oauth2.id_token

                token = google.oauth2.id_token.fetch_id_token(
                    google.auth.transport.requests.Request(), self._id_token_audience)
                headers["Authorization"] = f"Bearer {token}"
            except Exception:  # local https or missing google-auth: fall back to shared key only
                pass
        return headers

    def fuse(self, text: str, face: dict | None, face_samples: int, previous: dict | None) -> dict:
        payload = {"text": text, "face": face, "face_samples": face_samples, "previous": previous}
        try:
            r = requests.post(f"{self.base_url}/v1/fuse", json=payload,
                              headers=self._auth_headers(), timeout=self.timeout)
            r.raise_for_status()
            return r.json()
        except Exception as exc:
            log.warning("emotion service unavailable (%s); using fallback", exc)
            return self._fallback(text, face, face_samples, previous)

    @staticmethod
    def _fallback(text, face, face_samples, previous) -> dict:
        try:
            from emotion import analyze_text, fuse  # present when running the monorepo locally
            return fuse(analyze_text(text), face, face_samples, previous)
        except Exception:
            return {**NEUTRAL, "turns": int((previous or {}).get("turns", 0)) + 1}
