"""
providers/base.py — Abstract base classes and result dataclasses for all providers.

Swappable provider interfaces:
  STTProvider  →  FasterWhisperSTTProvider  |  MockSTTProvider
  LLMProvider  →  OpenRouterLLMProvider     |  MockLLMProvider
  TTSProvider  →  PiperTTSProvider          |  MockTTSProvider (→ browser fallback)
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class STTResult:
    """Result from a speech-to-text transcription."""
    text: str
    language: str            # ISO 639-1 code: "en", "hi", "es", etc.
    language_probability: float = 1.0
    segments: list = field(default_factory=list)


@dataclass
class LLMResult:
    """Result from an LLM generation call."""
    text: str
    model: str = ""
    llm_first_token_at: Optional[float] = None   # seconds from request start


class STTProvider(ABC):
    @abstractmethod
    async def transcribe(self, audio_bytes: bytes, sample_rate: int = 16000) -> STTResult:
        """Transcribe raw audio bytes → text + detected language."""
        ...


class LLMProvider(ABC):
    @abstractmethod
    async def generate(self, messages: list[dict], system_prompt: str) -> LLMResult:
        """Generate a response given a chat history and system prompt."""
        ...

    async def generate_stream(self, messages: list[dict], system_prompt: str):
        """Stream response tokens. Default: wraps generate() — yields full text as one chunk."""
        result = await self.generate(messages, system_prompt)
        yield result.text


class TTSProvider(ABC):
    @abstractmethod
    async def synthesize(self, text: str, language: str) -> Optional[bytes]:
        """Synthesize text to speech. Returns WAV bytes, or None to trigger browser TTS."""
        ...
