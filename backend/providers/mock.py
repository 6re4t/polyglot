"""
providers/mock.py — Mock providers for testing without hardware or API keys.

MockSTTProvider  — used when USE_MOCK_STT=true or faster-whisper is absent.
MockLLMProvider  — used when USE_MOCK_LLM=true or OPENROUTER_API_KEY is absent.
MockTTSProvider  — always returns None → browser speechSynthesis fires.

MockLLMProvider uses keyword matching against the last user message to return
deterministic, scenario-aware responses without any network calls.
"""
import logging
from typing import Optional

from .base import LLMProvider, LLMResult, STTProvider, STTResult, TTSProvider

logger = logging.getLogger(__name__)

# ── Keyword → (response text, language hint) ──────────────────────────────────
# Keys are lowercase substrings matched against the last user message.
# Order matters: more specific keys first.
_RESPONSES: list[tuple[str, str, str]] = [
    # Scenario 1 — Order Status
    ("rahul@example.com",  "Your order #4421 is out for delivery and should arrive tomorrow! "
                           "Tracking link: https://example.com/track/4421", "en"),
    ("4421",               "Got it! To verify your account, could you please provide the email address "
                           "associated with order #4421?", "en"),
    ("refund mil sakta",   "Haan bilkul! Agar delivery promised date ke baad nahi hoti, "
                           "toh aapko full refund milega.", "hi"),
    ("delivery kal",       "Haan, aapka order kal tak deliver ho jaayega. Tracking link available hai.", "hi"),
    ("theek hai",          "Samajh gaya! Kya aur kuch help chahiye?", "hi"),
    ("tracking link",      "Your tracking link is: https://example.com/track/4421 — "
                           "you should receive an email confirmation shortly.", "en"),
    ("switch back",        "Of course! Your order #4421 tracking link is: "
                           "https://example.com/track/4421", "en"),
    # Scenario 2 — Hotel Booking
    ("book it",            "Perfect! I've confirmed Metro Grand Koramangala for next weekend "
                           "(2 guests, ₹4900/night). Dates: next weekend. Shall I finalize?", "en"),
    ("second option",      "The second option is Metro Grand Koramangala in Koramangala, "
                           "Bangalore at ₹4900/night — centrally located with great amenities.", "en"),
    ("continue in english","Sure! Here are the three hotel options in Bangalore for next weekend: "
                           "1) Comfort Stay Indiranagar ₹4500/night, "
                           "2) Metro Grand Koramangala ₹4900/night, "
                           "3) Garden Nest Whitefield ₹4200/night.", "en"),
    ("5000 rupias",        "¡Perfecto! Para dos personas con un presupuesto de ₹5000/noche en Bangalore, "
                           "tengo tres opciones: 1) Comfort Stay Indiranagar ₹4500/noche, "
                           "2) Metro Grand Koramangala ₹4900/noche, 3) Garden Nest Whitefield ₹4200/noche.", "es"),
    ("bangalore",          "¡Claro! ¿Para cuántas personas y cuál es su presupuesto por noche?", "es"),
    ("hola",               "¡Hola! Estoy aquí para ayudarte. ¿En qué puedo asistirte hoy?", "es"),
    # Scenario 3 — Food / Code-switching
    ("add a coke",         "Got it! I've added a Coke to your order alongside the veg pizza. "
                           "Anything else?", "en"),
    ("pizza",              "Sure! I've noted a vegetarian pizza for you. "
                           "Would you like to add any sides or drinks?", "en"),
    # Scenario 4 — Weather
    ("compare",            "Here's the comparison: Mumbai is warm and humid at 32°C, "
                           "Delhi is hot and dry at 36°C, and Chennai is warm and breezy at 33°C. "
                           "Delhi is the hottest; Chennai is the most comfortable.", "en"),
    ("chennai",            "En Chennai hace calor con una brisa agradable, unos 33°C. "
                           "¡Un clima bastante cómodo!", "es"),
    ("delhi",              "Delhi mein aaj bahut garmi hai — 36°C, hot and dry. "
                           "Paani zyada piyo!", "hi"),
    ("mumbai",             "Mumbai is warm and humid today at around 32°C. "
                           "Quite sticky weather!", "en"),
    ("weather",            "I can help with weather! Which city are you asking about?", "en"),
    # Generic order / hotel fallback
    ("order",              "I can help with your order. Could you please provide your order ID?", "en"),
    ("hotel",              "I can help with hotel bookings! Which city and dates are you looking at?", "en"),
]

_DEFAULT = "I understand. How can I help you further? / Kaise madad kar sakta hoon? / ¿En qué puedo ayudarte?"


class MockSTTProvider(STTProvider):
    """Returns an empty transcript — text_input path is used in mock/demo mode."""

    async def transcribe(self, audio_bytes: bytes, sample_rate: int = 16000) -> STTResult:
        logger.debug("MockSTTProvider: returning empty transcript (use text_input in demo mode).")
        return STTResult(text="", language="en", language_probability=1.0)


class MockLLMProvider(LLMProvider):
    """Keyword-driven deterministic responses — no network calls required."""

    async def generate(self, messages: list[dict], system_prompt: str) -> LLMResult:
        last_user = ""
        for m in reversed(messages):
            if m.get("role") == "user":
                last_user = m.get("content", "").lower()
                break

        for keyword, response, _lang in _RESPONSES:
            if keyword.lower() in last_user:
                return LLMResult(text=response, model="mock-deterministic")

        return LLMResult(text=_DEFAULT, model="mock-deterministic")


class MockTTSProvider(TTSProvider):
    """Always returns None → frontend uses browser speechSynthesis."""

    async def synthesize(self, text: str, language: str) -> Optional[bytes]:
        return None
