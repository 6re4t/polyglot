"""
session.py — WebSocket session state management.

Each browser tab gets a unique session_id (UUID). SessionManager holds all
active sessions in memory. Sessions expire after SESSION_TTL_SECONDS of
inactivity and are cleaned up by a background task in main.py.
"""
from __future__ import annotations

import logging
import time
import uuid
from typing import Dict, Optional

from services.memory import SessionMemory

logger = logging.getLogger(__name__)

SESSION_TTL_SECONDS = 3600  # 1 hour of inactivity


class SessionState:
    """All mutable state for a single connected browser session."""

    def __init__(self, session_id: str):
        self.session_id:  str           = session_id
        self.memory:      SessionMemory = SessionMemory()
        self.created_at:  float         = time.time()
        self.last_active: float         = time.time()

    def touch(self) -> None:
        self.last_active = time.time()

    def is_expired(self) -> bool:
        return (time.time() - self.last_active) > SESSION_TTL_SECONDS


class SessionManager:
    """Thread-safe(ish) in-memory session store for an async FastAPI app."""

    def __init__(self):
        self._sessions: Dict[str, SessionState] = {}

    # ── CRUD ───────────────────────────────────────────────────────────────────

    def create_session(self, session_id: Optional[str] = None) -> SessionState:
        if session_id is None:
            session_id = str(uuid.uuid4())
        session = SessionState(session_id)
        self._sessions[session_id] = session
        logger.info(f"Session created: {session_id}")
        return session

    def get_session(self, session_id: str) -> Optional[SessionState]:
        session = self._sessions.get(session_id)
        if session:
            session.touch()
        return session

    def get_or_create(self, session_id: str) -> SessionState:
        session = self.get_session(session_id)
        if session is None:
            session = self.create_session(session_id)
        return session

    def remove_session(self, session_id: str) -> None:
        self._sessions.pop(session_id, None)
        logger.info(f"Session removed: {session_id}")

    # ── Maintenance ────────────────────────────────────────────────────────────

    def cleanup_expired(self) -> int:
        """Remove sessions that have been inactive beyond TTL. Returns count removed."""
        expired = [sid for sid, s in self._sessions.items() if s.is_expired()]
        for sid in expired:
            self.remove_session(sid)
        if expired:
            logger.info(f"Cleaned up {len(expired)} expired session(s).")
        return len(expired)

    @property
    def active_count(self) -> int:
        return len(self._sessions)
