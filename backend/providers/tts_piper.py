"""
providers/tts_piper.py — Local TTS using Piper.

Priority chain:
  1. Python piper-tts package  (from piper import PiperVoice)
  2. Piper CLI subprocess       (piper --model … --output_file …)
  3. Return None                → frontend uses browser speechSynthesis

Voice model paths are configured via environment variables:
  PIPER_VOICE_PATH_EN / PIPER_VOICE_PATH_HI / PIPER_VOICE_PATH_ES

Download .onnx voice files from:
  https://github.com/rhasspy/piper/releases/tag/v1.2.0
"""
import asyncio
import io
import logging
import os
import shutil
import subprocess
import tempfile
import wave
from typing import Optional

from .base import TTSProvider

logger = logging.getLogger(__name__)

# ── Optional piper-tts Python package ─────────────────────────────────────────
_PIPER_PYTHON_OK = False
try:
    from piper import PiperVoice  # type: ignore
    _PIPER_PYTHON_OK = True
except ImportError:
    pass  # Fall through to CLI subprocess approach


class PiperTTSProvider(TTSProvider):
    """
    TTS provider backed by Piper.

    If no voice file is configured for the requested language, falls back to
    the English voice (if available), then returns None for browser TTS.

    Configure voice paths via PIPER_VOICE_PATH_EN / _HI / _ES in .env.
    Set PIPER_EXECUTABLE if `piper` is not on your PATH.
    """

    def __init__(self, voice_map: dict = None, executable: str = "piper"):
        # voice_map: {"en": "/path/to/en.onnx", "hi": "...", "es": "..."}
        self._voice_map: dict = voice_map or {}
        self._executable: str = executable
        # Cache loaded PiperVoice instances to avoid re-loading per request
        self._loaded_voices: dict = {}

    # ── Internal helpers ───────────────────────────────────────────────────────

    def _resolve_voice(self, language: str) -> Optional[str]:
        """Return a valid .onnx path for the given language, or None."""
        path = self._voice_map.get(language, "")
        if path and os.path.isfile(path):
            return path
        # English fallback for unsupported / unconfigured languages
        if language != "en":
            en_path = self._voice_map.get("en", "")
            if en_path and os.path.isfile(en_path):
                logger.info(f"No Piper voice for '{language}'; using English fallback.")
                return en_path
        return None

    def _synth_python_api(self, text: str, voice_path: str) -> Optional[bytes]:
        """Use the piper-tts Python package to synthesize."""
        try:
            if voice_path not in self._loaded_voices:
                self._loaded_voices[voice_path] = PiperVoice.load(voice_path)
            voice = self._loaded_voices[voice_path]
            buf = io.BytesIO()
            with wave.open(buf, "wb") as wav_out:
                voice.synthesize(text, wav_out)
            return buf.getvalue()
        except Exception as e:
            logger.warning(f"piper-tts Python API failed: {e}; trying CLI fallback.")
            return None

    def _synth_subprocess(self, text: str, voice_path: str) -> Optional[bytes]:
        """Use the Piper CLI binary via subprocess."""
        piper_bin = shutil.which(self._executable) or self._executable
        if not shutil.which(piper_bin):
            logger.info("Piper binary not found on PATH; browser TTS fallback will be used.")
            return None

        out_path: Optional[str] = None
        try:
            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
                out_path = f.name

            result = subprocess.run(
                [piper_bin, "--model", voice_path, "--output_file", out_path],
                input=text.encode("utf-8"),
                capture_output=True,
                timeout=20,
            )
            if result.returncode == 0 and os.path.isfile(out_path):
                with open(out_path, "rb") as f:
                    return f.read()
            logger.warning(
                f"Piper CLI exited {result.returncode}: {result.stderr.decode(errors='replace')[:200]}"
            )
            return None
        except subprocess.TimeoutExpired:
            logger.warning("Piper CLI timed out.")
            return None
        except FileNotFoundError:
            logger.info("Piper CLI not found; browser TTS fallback will be used.")
            return None
        except Exception as e:
            logger.warning(f"Piper CLI error: {e}")
            return None
        finally:
            if out_path and os.path.isfile(out_path):
                os.unlink(out_path)

    def _synthesize_sync(self, text: str, voice_path: str) -> Optional[bytes]:
        if _PIPER_PYTHON_OK:
            result = self._synth_python_api(text, voice_path)
            if result is not None:
                return result
        return self._synth_subprocess(text, voice_path)

    # ── Public API ─────────────────────────────────────────────────────────────

    async def synthesize(self, text: str, language: str) -> Optional[bytes]:
        voice_path = self._resolve_voice(language)
        if not voice_path:
            logger.info(
                f"No Piper voice configured for '{language}'. "
                "Returning None — frontend will use browser speechSynthesis."
            )
            return None

        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self._synthesize_sync, text, voice_path)
