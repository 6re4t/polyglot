"""
services/language_router.py — Determine the active language for each utterance.

Primary signal:  Whisper's detected language code (most reliable when audio quality is good).
Secondary:       Rule-based scoring against the transcript text.
Fallback:        Last known language from memory.

Supported output languages: "en" (English), "hi" (Hindi), "es" (Spanish).

Design decisions — see docs/decisions_log.md for full rationale:
  - Whisper language contributes a weight-3 vote to the scoring table.
  - Rule patterns for Hindi/Spanish add extra votes.
  - Explicit English-switch phrases (e.g. "let's switch back", "continue in English")
    override all other signals and force "en" immediately.
  - Mixed Hindi-English (Hinglish): dominant rule score wins; ties break by last_language.
"""
from __future__ import annotations

import logging
import re
from typing import Optional

logger = logging.getLogger(__name__)

SUPPORTED_LANGS  = {"en", "hi", "es"}
DEFAULT_LANG     = "en"
WHISPER_WEIGHT   = 3   # Whisper detection counts as N rule votes


# ── Rule pattern tables ───────────────────────────────────────────────────────

# Any of these → force English regardless of Whisper output
_EN_FORCE_PATTERNS: list[str] = [
    r"continue\s+in\s+english",
    r"switch\s*(?:back\s*)?(?:to\s*)?english",
    r"let'?s?\s*(?:continue|go)\s*(?:back\s*)?in\s*english",
    r"actually\s+let'?s?\s*switch\s*back",
    r"can\s*we\s*continue\s*in\s*english",
    r"speak\s+in\s+english",
    r"in\s+english\s+please",
    r"reply\s+in\s+english",
    r"english\s+please",
]

# Hindi vocabulary / romanized patterns
_HI_PATTERNS: list[str] = [
    r"\btheek\s*hai\b",   r"\baur\b",       r"\bkya\b",
    r"\bnahi\b",          r"\btoh\b",       r"\bkarna\b",
    r"\bsakta\b",         r"\bhai\b",       r"\bkaro\b",
    r"\bmujhe\b",         r"\bho\s*jaayegi\b",
    r"\blekin\b",         r"\bbata\b",      r"\bkab\b",
    r"\bkitne\b",         r"\bkahan\b",     r"\bab\b",
    r"\bchahiye\b",       r"\bkijiye\b",    r"\bwahi\b",
    r"\bpehle\b",         r"\bbaad\b",      r"\bkuch\b",
    r"\bpar\b",           r"\bse\b",        r"\bko\b",
    r"\bka\b",            r"\bki\b",        r"\bke\b",
    r"\bbhi\b",           r"\bphir\b",      r"\byahan\b",
    r"\bvahan\b",         r"\bkaisa\b",     r"\bkaise\b",
    r"\bkya\s*aap\b",
]

# Spanish vocabulary / punctuation patterns
_ES_PATTERNS: list[str] = [
    r"[¿¡]",              # Inverted punctuation is a near-certain signal
    r"\bhola\b",          r"\bquiero\b",    r"\bpara\b",
    r"\breservar\b",      r"\bpresupuesto\b",
    r"\bpersonas\b",      r"\bfin\s*de\s*semana\b",
    r"\brupias\b",        r"\bopciones\b",  r"\bgracias\b",
    r"\bpor\s*favor\b",   r"\bcómo\b",     r"\bcomo\s+est",
    r"\bpuedo\b",         r"\bquiero\b",   r"\btambién\b",
    r"\bno\s*puedo\b",    r"\b(?:el|la|los|las)\s+hotel\b",
    r"\bsin\b",           r"\bsobre\b",    r"\bhotel\b.*\ben\b",
    r"\bde\s+\d",         r"\bpor\s+noche\b",
    r"\bme\s+gustar[íi]a\b",
]

_EN_FORCE_RE = re.compile("|".join(_EN_FORCE_PATTERNS), re.IGNORECASE)


def _count_matches(text: str, patterns: list[str]) -> int:
    return sum(1 for p in patterns if re.search(p, text, re.IGNORECASE))


class LanguageRouter:
    """
    Stateless language detector.
    Call detect() per utterance; pass the session memory for last_language fallback.
    """

    def detect(
        self,
        text: str,
        whisper_lang: Optional[str],
        memory,
    ) -> str:
        """
        Return the active language code: "en", "hi", or "es".

        Parameters
        ----------
        text         : User transcript (may be empty for text-input path).
        whisper_lang : ISO 639-1 code from Whisper, or None if unavailable.
        memory       : SessionMemory — used for last_language fallback.
        """
        # 1. Explicit English-switch override — highest priority
        if _EN_FORCE_RE.search(text):
            logger.debug("Language forced → en (explicit switch phrase)")
            return "en"

        # 2. Score each language
        en_score = 0
        hi_score = _count_matches(text, _HI_PATTERNS)
        es_score = _count_matches(text, _ES_PATTERNS)

        # 3. Whisper language as a strong vote
        wl = (whisper_lang or "").lower()
        if wl == "en":
            en_score += WHISPER_WEIGHT
        elif wl == "hi":
            hi_score += WHISPER_WEIGHT
        elif wl == "es":
            es_score += WHISPER_WEIGHT
        elif wl in SUPPORTED_LANGS:
            # e.g. some other mapping — add partial English vote
            en_score += 1

        scores = {"en": en_score, "hi": hi_score, "es": es_score}
        best   = max(scores.values())
        logger.debug(f"Lang scores (whisper={wl!r}): {scores}")

        # 4. If all zero, fall back to last known language
        if best == 0:
            return memory.last_language or DEFAULT_LANG

        # 5. Break ties with last_language
        candidates = [lang for lang, score in scores.items() if score == best]
        if len(candidates) == 1:
            return candidates[0]

        last = memory.last_language or DEFAULT_LANG
        return last if last in candidates else candidates[0]

    def detect_text_only(self, text: str, memory) -> str:
        """
        Convenience wrapper for the text-input path where Whisper is not involved.
        Uses None as whisper_lang so only rule-based scoring applies.
        """
        return self.detect(text, None, memory)
