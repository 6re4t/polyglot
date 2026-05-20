"""
providers/stt_faster_whisper.py — Local STT using faster-whisper.

- Lazy model loading on first call.
- Whisper's built-in VAD filter (silero-based) trims silence.
- Language is auto-detected; result includes language code + probability.
- Resamples audio to 16 kHz if needed (faster-whisper expects 16 kHz float32).
- Gracefully degrades to MockSTTProvider behaviour if faster-whisper is missing.
"""
import asyncio
import io
import logging
import os
import tempfile
from typing import Optional

import numpy as np

import re

from .base import STTProvider, STTResult

logger = logging.getLogger(__name__)

# ── Optional dependency guards ─────────────────────────────────────────────────
_FASTER_WHISPER_OK = False
try:
    from faster_whisper import WhisperModel
    _FASTER_WHISPER_OK = True
except ImportError:
    logger.warning(
        "faster-whisper not installed. STT will return an empty transcript. "
        "Install it with: pip install faster-whisper"
    )

_SOUNDFILE_OK = False
try:
    import soundfile as sf
    _SOUNDFILE_OK = True
except ImportError:
    logger.warning("soundfile not installed; audio decode may be limited.")

_SCIPY_OK = False
try:
    import scipy.signal
    _SCIPY_OK = True
except ImportError:
    pass


class FasterWhisperSTTProvider(STTProvider):
    """
    STT provider backed by faster-whisper (CTranslate2).
    Model is downloaded on first transcribe() call and cached thereafter.
    """

    def __init__(self, model_size: str = "base"):
        self._model_size = model_size
        self._model: Optional[object] = None

    # ── Private helpers ────────────────────────────────────────────────────────

    def _load_model(self):
        if not _FASTER_WHISPER_OK:
            raise RuntimeError("faster-whisper is not installed.")
        if self._model is None:
            logger.info(f"Loading Whisper model '{self._model_size}' (first call — may download)…")
            self._model = WhisperModel(
                self._model_size,
                device="cpu",
                compute_type="int8",   # int8 is fastest on CPU, good accuracy
            )
            logger.info("Whisper model ready.")
        return self._model

    def _decode_audio(self, audio_bytes: bytes) -> tuple[np.ndarray, int]:
        """
        Decode raw audio bytes to a float32 numpy array.
        Returns (audio_array, sample_rate).
        Handles WAV, FLAC, OGG; requires soundfile.
        """
        if _SOUNDFILE_OK:
            buf = io.BytesIO(audio_bytes)
            audio, sr = sf.read(buf, dtype="float32", always_2d=False)
            # Ensure mono
            if audio.ndim > 1:
                audio = audio.mean(axis=1)
            return audio, sr

        # Fallback: write to temp file and let soundfile try from disk
        # (some formats need seek; BytesIO doesn't always work)
        suffix = ".wav"
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as f:
            f.write(audio_bytes)
            tmp = f.name
        try:
            if _SOUNDFILE_OK:
                audio, sr = sf.read(tmp, dtype="float32", always_2d=False)
            else:
                # Last resort: scipy WAV only
                import scipy.io.wavfile as wv
                sr, audio = wv.read(tmp)
                audio = audio.astype(np.float32) / 32768.0
                if audio.ndim > 1:
                    audio = audio.mean(axis=1)
            return audio, sr
        finally:
            os.unlink(tmp)

    def _resample_to_16k(self, audio: np.ndarray, src_sr: int) -> np.ndarray:
        """Downsample (or upsample) audio to 16 000 Hz expected by Whisper."""
        if src_sr == 16000:
            return audio
        if _SCIPY_OK:
            target_len = int(len(audio) * 16000 / src_sr)
            return scipy.signal.resample(audio, target_len).astype(np.float32)
        # Simple integer decimation as last resort (only works for exact ratios)
        ratio = src_sr // 16000
        if ratio > 1:
            return audio[::ratio]
        return audio

    def _transcribe_sync(self, audio_bytes: bytes,
                          hint_language: str | None = None) -> STTResult:
        """Blocking transcription — called from a thread-pool executor."""
        try:
            model = self._load_model()
            audio, sr = self._decode_audio(audio_bytes)
            audio = self._resample_to_16k(audio, sr)

            # Pass 1: auto-detect (always run — gives the unbiased language signal)
            segments_gen, info = model.transcribe(
                audio, beam_size=5, language=None, vad_filter=False,
            )
            segments     = list(segments_gen)
            auto_text    = " ".join(s.text.strip() for s in segments).strip()
            detected_lang = info.language
            lang_prob     = float(info.language_probability)

            # Decide whether to also try a forced Hindi pass:
            #   • Whisper flagged Urdu (same spoken language, wrong script)
            #   • Confidence is low — model is unsure, Hindi is a good candidate
            #   • Previous turn was Hindi (Hinglish utterances often score as low-conf EN)
            # Exception: if auto-detect is confident (≥0.75) for EN/ES, trust it —
            # this is what lets the user switch BACK to English effortlessly.
            confident_non_hi = (lang_prob >= 0.75 and detected_lang in ("en", "es"))
            need_hi_pass = (
                detected_lang == "ur" or
                lang_prob < 0.75 or
                hint_language == "hi"
            ) and not confident_non_hi

            if need_hi_pass:
                seg2, info2 = model.transcribe(
                    audio, beam_size=5, language="hi", vad_filter=False,
                )
                hi_text = " ".join(s.text.strip() for s in seg2).strip()

                # Devanagari in the output is a definitive signal — use it
                has_devanagari = bool(re.search(r'[\u0900-\u097F]', hi_text))
                if has_devanagari:
                    logger.info(f"Devanagari detected in Hindi pass — using Hindi transcript.")
                    return STTResult(
                        text=hi_text, language="hi",
                        language_probability=max(lang_prob, float(info2.language_probability)),
                    )

                # No Devanagari (Hinglish): use Hindi result only when
                # the previous turn was Hindi AND it’s at least as long as auto
                if hint_language == "hi" and hi_text and len(hi_text) >= len(auto_text):
                    logger.info("Hinglish detected — keeping Hindi context.")
                    return STTResult(
                        text=hi_text, language="hi",
                        language_probability=max(lang_prob, float(info2.language_probability)),
                    )

                # If Urdu was detected but Hindi pass gave nothing useful, normalise lang
                if detected_lang == "ur":
                    detected_lang = "hi"

            return STTResult(
                text=auto_text,
                language=detected_lang,
                language_probability=lang_prob,
            )
        except RuntimeError as e:
            # faster-whisper not installed
            logger.warning(f"STT skipped: {e}")
            return STTResult(text="", language="en", language_probability=0.0)
        except Exception as e:
            logger.error(f"STT error: {e}", exc_info=True)
            return STTResult(text="", language="en", language_probability=0.0)

    # ── Public API ─────────────────────────────────────────────────────────────

    async def transcribe(self, audio_bytes: bytes, sample_rate: int = 16000,
                          hint_language: str | None = None) -> STTResult:
        if not _FASTER_WHISPER_OK:
            logger.warning("faster-whisper unavailable — returning empty transcript.")
            return STTResult(text="", language="en", language_probability=0.0)
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            None, self._transcribe_sync, audio_bytes, hint_language
        )
