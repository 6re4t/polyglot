"""
services/memory.py — Conversation memory: structured state + rolling chat history.

StructuredMemory holds domain-specific fields (order, hotel, weather, language).
SessionMemory combines StructuredMemory with a rolling chat history list.
"""
from __future__ import annotations

import copy
from dataclasses import asdict, dataclass, field
from typing import Dict, List, Optional

# Maximum number of chat turns kept in rolling history
MAX_HISTORY_MESSAGES = 20


@dataclass
class StructuredMemory:
    """
    Typed memory for all tracked domain state.
    Fields are None until populated by mock_tools or conversation context.
    """
    # ── Order tracking ─────────────────────────────────────────────────────
    order_id: Optional[str] = None
    email: Optional[str] = None
    order_status: Optional[str] = None
    estimated_delivery: Optional[str] = None
    tracking_link: Optional[str] = None
    refund_policy: Optional[str] = None

    # ── Hotel booking ──────────────────────────────────────────────────────
    hotel_city: Optional[str] = None
    hotel_dates: Optional[str] = None
    hotel_people: Optional[int] = None
    hotel_budget: Optional[int] = None
    hotel_options: Optional[List[dict]] = None
    selected_hotel_option: Optional[str] = None

    # ── Weather ────────────────────────────────────────────────────────────
    weather_cities: Optional[List[str]] = None

    # ── Language / conversation state ──────────────────────────────────────
    last_language: str = "en"
    last_topic: Optional[str] = None

    # ── Helpers ────────────────────────────────────────────────────────────

    def to_dict(self) -> dict:
        return asdict(self)

    def to_context_string(self) -> str:
        """Render non-null fields as a compact key: value block for LLM injection."""
        d = self.to_dict()
        lines = []
        for key, val in d.items():
            if val is None or val == [] or val == {}:
                continue
            if key == "hotel_options" and isinstance(val, list):
                opts = "; ".join(
                    f"{i+1}) {o.get('name', '?')} ₹{o.get('price', '?')}/night"
                    for i, o in enumerate(val)
                )
                lines.append(f"hotel_options: {opts}")
            elif isinstance(val, list):
                lines.append(f"{key}: {', '.join(str(v) for v in val)}")
            else:
                lines.append(f"{key}: {val}")
        return "\n".join(lines) if lines else "No structured memory yet."


class SessionMemory:
    """
    Top-level memory object for a WebSocket session.
    Holds StructuredMemory + a rolling chat history list.
    """

    def __init__(self):
        self.structured = StructuredMemory()
        self.chat_history: List[Dict] = []

    # ── Chat history management ────────────────────────────────────────────

    def add_message(self, role: str, content: str) -> None:
        self.chat_history.append({"role": role, "content": content})
        # Trim to keep only the most recent N messages
        if len(self.chat_history) > MAX_HISTORY_MESSAGES:
            self.chat_history = self.chat_history[-MAX_HISTORY_MESSAGES:]

    # ── Language shortcut properties ───────────────────────────────────────

    @property
    def last_language(self) -> str:
        return self.structured.last_language

    @last_language.setter
    def last_language(self, value: str) -> None:
        self.structured.last_language = value

    # ── Serialisation ──────────────────────────────────────────────────────

    def to_dict(self) -> dict:
        return {
            "structured": self.structured.to_dict(),
            "history_length": len(self.chat_history),
        }

    def snapshot(self) -> dict:
        """Full snapshot including chat history (for debugging/CLI)."""
        return {
            "structured": self.structured.to_dict(),
            "chat_history": copy.deepcopy(self.chat_history),
        }
