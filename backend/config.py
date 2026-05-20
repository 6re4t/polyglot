"""
config.py — Application settings loaded from environment / .env file.
"""
import os
from pathlib import Path
from dotenv import load_dotenv

# Load .env from the project root (one level up from backend/)
_env_path = Path(__file__).parent.parent / ".env"
load_dotenv(dotenv_path=_env_path)


class Settings:
    # ── OpenRouter ─────────────────────────────────────────────────────────
    OPENROUTER_API_KEY: str = os.getenv("OPENROUTER_API_KEY", "")
    OPENROUTER_MODEL: str = os.getenv("OPENROUTER_MODEL", "google/gemini-2.5-flash")
    OPENROUTER_SITE_URL: str = os.getenv("OPENROUTER_SITE_URL", "http://localhost:8000")
    OPENROUTER_APP_NAME: str = os.getenv("OPENROUTER_APP_NAME", "polyglot-voice-agent")

    # ── STT ────────────────────────────────────────────────────────────────
    WHISPER_MODEL_SIZE: str = os.getenv("WHISPER_MODEL_SIZE", "tiny")

    # ── TTS (Piper) ────────────────────────────────────────────────────────
    PIPER_EXECUTABLE: str = os.getenv("PIPER_EXECUTABLE", "piper")
    PIPER_VOICE_PATH_EN: str = os.getenv("PIPER_VOICE_PATH_EN", "")
    PIPER_VOICE_PATH_HI: str = os.getenv("PIPER_VOICE_PATH_HI", "")
    PIPER_VOICE_PATH_ES: str = os.getenv("PIPER_VOICE_PATH_ES", "")

    # ── Mock overrides ─────────────────────────────────────────────────────
    USE_MOCK_STT: bool = os.getenv("USE_MOCK_STT", "false").lower() == "true"
    USE_MOCK_LLM: bool = os.getenv("USE_MOCK_LLM", "false").lower() == "true"
    USE_MOCK_TTS: bool = os.getenv("USE_MOCK_TTS", "false").lower() == "true"

    @property
    def piper_voice_map(self) -> dict:
        return {
            "en": self.PIPER_VOICE_PATH_EN,
            "hi": self.PIPER_VOICE_PATH_HI,
            "es": self.PIPER_VOICE_PATH_ES,
        }

    @property
    def piper_available(self) -> bool:
        """True if at least one Piper voice .onnx file is configured and exists."""
        for path in self.piper_voice_map.values():
            if path and Path(path).is_file():
                return True
        return False


settings = Settings()
