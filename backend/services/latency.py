"""
services/latency.py — Per-turn latency tracker.

Records timestamps for each pipeline stage and computes inter-stage deltas.

Events (in pipeline order):
  speech_end_detected_at   — user stops speaking / audio_end received
  stt_started_at           — transcription begins
  stt_final_at             — transcription complete
  llm_request_started_at   — LLM request dispatched
  llm_first_token_at       — first token / response received
  llm_completed_at         — full LLM response available
  tts_request_started_at   — TTS synthesis begins
  tts_first_audio_at       — first audio chunk / WAV bytes ready
  audio_playback_started_at — audio sent to browser / browser starts playing
"""
import time
from typing import Optional

PIPELINE_EVENTS = [
    "speech_end_detected_at",
    "stt_started_at",
    "stt_final_at",
    "llm_request_started_at",
    "llm_first_token_at",
    "llm_completed_at",
    "tts_request_started_at",
    "tts_first_audio_at",
    "audio_playback_started_at",
]


class LatencyTracker:
    """Records wall-clock timestamps for each pipeline event."""

    def __init__(self):
        self._origin: float = time.perf_counter()
        self._marks: dict[str, float] = {}

    def mark(self, event: str) -> None:
        """Stamp an event with the current time (idempotent — first call wins)."""
        if event not in self._marks:
            self._marks[event] = time.perf_counter()

    def _delta_ms(self, start: str, end: str) -> Optional[float]:
        """Return elapsed milliseconds between two events, or None if either is missing."""
        if start in self._marks and end in self._marks:
            return round((self._marks[end] - self._marks[start]) * 1000, 1)
        return None

    def _abs_ms(self, event: str) -> Optional[float]:
        """Milliseconds from tracker creation to the given event."""
        if event in self._marks:
            return round((self._marks[event] - self._origin) * 1000, 1)
        return None

    def to_summary(self) -> dict:
        """
        Returns a dict with absolute timestamps (ms from tracker start) for
        every event, plus named delta fields for the most important spans.
        """
        d: dict = {ev: self._abs_ms(ev) for ev in PIPELINE_EVENTS}

        # Named latency spans
        d["vad_latency_ms"]        = self._delta_ms("speech_end_detected_at", "stt_started_at")
        d["stt_latency_ms"]        = self._delta_ms("stt_started_at",         "stt_final_at")
        d["llm_first_token_ms"]    = self._delta_ms("llm_request_started_at", "llm_first_token_at")
        d["llm_total_ms"]          = self._delta_ms("llm_request_started_at", "llm_completed_at")
        d["tts_latency_ms"]        = self._delta_ms("tts_request_started_at", "tts_first_audio_at")
        d["total_latency_ms"]      = self._delta_ms("speech_end_detected_at", "audio_playback_started_at")

        return d
