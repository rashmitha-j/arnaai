"""Human-in-the-loop escalation.

Priorities:
  * critical -> imminent risk; AI is paused and the session is handed to a human
  * urgent   -> high risk; AI is paused and the session is handed to a human
  * review   -> moderate risk; AI keeps supporting, a counselor reviews async

Notifications go to an optional webhook (Google Chat / Slack incoming webhook).
The payload contains NO conversation content, only ids and risk metadata.
"""
from __future__ import annotations

import logging
import threading

import requests

log = logging.getLogger(__name__)

PRIORITY_FOR_LEVEL = {"imminent": "critical", "high": "urgent", "moderate": "review"}


class EscalationService:
    def __init__(self, store, webhook_url: str = "", dashboard_url: str = ""):
        self.store = store
        self.webhook_url = webhook_url
        self.dashboard_url = dashboard_url

    def escalate(self, session_id: str, risk: dict, profile: dict | None) -> dict:
        existing = self.store.get_open_escalation(session_id)
        priority = PRIORITY_FOR_LEVEL.get(risk["level"], "review")
        order = ["review", "urgent", "critical"]
        if existing:
            # Upgrade priority if things got worse; never open duplicates.
            if order.index(priority) > order.index(existing["priority"]):
                self.store.update_escalation(existing["id"], priority=priority, level=risk["level"])
                existing = self.store.get_escalation(existing["id"])
                self._notify(existing)
            return existing

        reason = {"risk": risk, "distress": (profile or {}).get("distress"),
                  "dominant_emotion": (profile or {}).get("dominant")}
        esc = self.store.create_escalation(session_id, risk["level"], reason, priority)
        self._notify(esc)
        return esc

    def _notify(self, esc: dict) -> None:
        if not self.webhook_url:
            log.info("escalation %s (%s) created; no webhook configured", esc["id"], esc["priority"])
            return
        text = (f"🚨 ArnaAI escalation [{esc['priority'].upper()}] risk={esc['level']} "
                f"id={esc['id']}" + (f"\nOpen: {self.dashboard_url}/counselor" if self.dashboard_url else ""))

        def send():
            try:
                requests.post(self.webhook_url, json={"text": text}, timeout=5)
            except Exception as exc:
                log.error("escalation webhook failed: %s", exc)

        threading.Thread(target=send, daemon=True).start()
