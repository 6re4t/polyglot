"""
main.py — FastAPI application entry point.

Run from the backend/ directory:
  uvicorn main:app --host 0.0.0.0 --port 8000 --reload

Architecture:
  Browser → WebSocket /ws/{session_id}
    ├─ audio_chunk  → accumulate in buffer
    ├─ audio_end    → VAD/STT → language_router → mock_tools → LLM → TTS → respond
    └─ text_input   → language_router → mock_tools → LLM → TTS → respond

WebSocket message protocol (browser → backend):
  {type: "audio_chunk", data: "<base64 WAV bytes>"}
  {type: "audio_end"}
  {type: "text_input", text: "..."}
  {type: "ping"}

WebSocket message protocol (backend → browser):
  {type: "transcript",    text, language, confidence}
  {type: "agent_response", text, language}
  {type: "audio_response", data: "<base64 WAV>", format: "wav", tts_mode: "piper"}
  {type: "tts_browser",   text, language}     ← triggers browser speechSynthesis
  {type: "memory_update", memory: {...}}
  {type: "latency_update", data: {...}}
  {type: "info",           message}
  {type: "error",          message}
  {type: "pong"}
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import sys
import time
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

# ── Path setup (allows running as: cd backend && uvicorn main:app) ─────────────
sys.path.insert(0, str(Path(__file__).parent))

from config import settings
from session import SessionManager
from services.language_router import LanguageRouter
from services.mock_tools import maybe_update_memory
from services.latency import LatencyTracker

# Provider imports with graceful fallbacks
from providers.mock import MockSTTProvider, MockLLMProvider, MockTTSProvider

try:
    from providers.stt_faster_whisper import FasterWhisperSTTProvider, _FASTER_WHISPER_OK as _HAS_WHISPER
except Exception:
    _HAS_WHISPER = False

try:
    from providers.llm_openrouter import OpenRouterLLMProvider
    _HAS_OPENROUTER = True
except Exception:
    _HAS_OPENROUTER = False

try:
    from providers.tts_piper import PiperTTSProvider
    _HAS_PIPER = True
except Exception:
    _HAS_PIPER = False

# ── Logging ────────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

# ── FastAPI app ────────────────────────────────────────────────────────────────
app = FastAPI(title="Polyglot Voice Agent", version="1.0.0")

_static_dir = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=str(_static_dir)), name="static")


# ── Provider factory ───────────────────────────────────────────────────────────

def _build_stt():
    if settings.USE_MOCK_STT or not _HAS_WHISPER:
        logger.info("STT → MockSTTProvider (USE_MOCK_STT or faster-whisper missing)")
        return MockSTTProvider()
    logger.info(f"STT → FasterWhisperSTTProvider (model={settings.WHISPER_MODEL_SIZE})")
    return FasterWhisperSTTProvider(settings.WHISPER_MODEL_SIZE)


def _build_llm():
    if settings.USE_MOCK_LLM or not settings.OPENROUTER_API_KEY:
        logger.info("LLM → MockLLMProvider (USE_MOCK_LLM or no API key)")
        return MockLLMProvider()
    if not _HAS_OPENROUTER:
        logger.info("LLM → MockLLMProvider (httpx/openrouter module unavailable)")
        return MockLLMProvider()
    logger.info(f"LLM → OpenRouterLLMProvider (model={settings.OPENROUTER_MODEL})")
    return OpenRouterLLMProvider()


def _build_tts():
    if settings.USE_MOCK_TTS or not _HAS_PIPER:
        logger.info("TTS → MockTTSProvider (USE_MOCK_TTS or piper module missing) → browser TTS fallback")
        return MockTTSProvider()
    if not settings.piper_available:
        logger.info("TTS → MockTTSProvider (no valid Piper .onnx voices configured) → browser TTS fallback")
        return MockTTSProvider()
    logger.info("TTS → PiperTTSProvider")
    return PiperTTSProvider(
        voice_map=settings.piper_voice_map,
        executable=settings.PIPER_EXECUTABLE,
    )


stt             = _build_stt()
llm             = _build_llm()
tts             = _build_tts()
session_manager = SessionManager()
lang_router     = LanguageRouter()


# ── Routes ─────────────────────────────────────────────────────────────────────

@app.get("/")
async def index():
    html = (_static_dir / "index.html").read_text(encoding="utf-8")
    return HTMLResponse(html)


@app.get("/health")
async def health():
    return JSONResponse({
        "status":   "ok",
        "stt":      type(stt).__name__,
        "llm":      type(llm).__name__,
        "tts":      type(tts).__name__,
        "model":    settings.OPENROUTER_MODEL,
        "sessions": session_manager.active_count,
    })


@app.get("/sessions/{session_id}/memory")
async def get_memory(session_id: str):
    session = session_manager.get_session(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found.")
    return JSONResponse(session.memory.to_dict())


# ── WebSocket handler ──────────────────────────────────────────────────────────

@app.websocket("/ws/{session_id}")
async def ws_endpoint(websocket: WebSocket, session_id: str):
    await websocket.accept()
    session      = session_manager.get_or_create(session_id)
    audio_buffer = bytearray()
    logger.info(f"WS connected: {session_id}")

    async def send(msg: dict) -> None:
        await websocket.send_text(json.dumps(msg, ensure_ascii=False))

    try:
        while True:
            raw = await websocket.receive_text()
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                await send({"type": "error", "message": "Invalid JSON."})
                continue

            msg_type = msg.get("type", "")

            if msg_type == "ping":
                await send({"type": "pong"})

            elif msg_type == "audio_chunk":
                # Browser sends base64-encoded WAV bytes in one or more chunks
                try:
                    chunk = base64.b64decode(msg["data"])
                    audio_buffer.extend(chunk)
                except (KeyError, Exception) as e:
                    await send({"type": "error", "message": f"Bad audio_chunk: {e}"})

            elif msg_type == "audio_end":
                if audio_buffer:
                    tracker    = LatencyTracker()
                    tracker.mark("speech_end_detected_at")
                    audio_data = bytes(audio_buffer)
                    audio_buffer.clear()
                    await _process_audio(send, session, audio_data, tracker)
                else:
                    await send({"type": "info", "message": "No audio received."})

            elif msg_type == "text_input":
                text = (msg.get("text") or "").strip()
                if text:
                    tracker = LatencyTracker()
                    tracker.mark("speech_end_detected_at")
                    await _process_text(send, session, text, tracker)

            else:
                logger.debug(f"Unknown WS message type: {msg_type!r}")

    except WebSocketDisconnect:
        logger.info(f"WS disconnected: {session_id}")
    except Exception as e:
        logger.error(f"WS error [{session_id}]: {e}", exc_info=True)
        try:
            await send({"type": "error", "message": str(e)})
        except Exception:
            pass


# ── Pipeline helpers ───────────────────────────────────────────────────────────

async def _process_audio(send, session, audio_bytes: bytes, tracker: LatencyTracker) -> None:
    tracker.mark("stt_started_at")
    stt_result = await stt.transcribe(audio_bytes)
    tracker.mark("stt_final_at")

    if not stt_result.text.strip():
        await send({"type": "info", "message": "No speech detected in audio."})
        return

    await send({
        "type":       "transcript",
        "text":       stt_result.text,
        "language":   stt_result.language,
        "confidence": round(stt_result.language_probability, 3),
    })

    active_lang = lang_router.detect(stt_result.text, stt_result.language, session.memory)
    await _process_turn(send, session, stt_result.text, active_lang, tracker)


async def _process_text(send, session, text: str, tracker: LatencyTracker) -> None:
    # For text input Whisper is not used; language comes from rules only
    active_lang = lang_router.detect_text_only(text, session.memory)

    # Fake STT timestamps so latency table is consistent
    tracker.mark("stt_started_at")
    tracker.mark("stt_final_at")

    await send({
        "type":       "transcript",
        "text":       text,
        "language":   active_lang,
        "confidence": 1.0,
    })

    await _process_turn(send, session, text, active_lang, tracker)


async def _process_turn(
    send,
    session,
    text:     str,
    language: str,
    tracker:  LatencyTracker,
) -> None:
    # 1. Update structured memory from this utterance
    maybe_update_memory(text, session.memory)
    session.memory.last_language = language

    # 2. Build system prompt with current memory context
    system_prompt = _build_system_prompt(session.memory, language)

    # 3. Add user message to rolling history
    session.memory.add_message("user", text)

    # 4. Stream LLM — flush to TTS sentence-by-sentence for lowest first-audio latency
    tracker.mark("llm_request_started_at")
    full_text       = ""
    buffer          = ""
    first_audio     = False
    last_flush_time = None   # set on first token; used for time-based safety flush

    async for token in llm.generate_stream(session.memory.chat_history, system_prompt):
        if not full_text:
            tracker.mark("llm_first_token_at")
            last_flush_time = time.perf_counter()
        full_text += token
        buffer    += token

        sentence = buffer.strip()
        elapsed  = time.perf_counter() - last_flush_time if last_flush_time else 0

        # Flush when: sentence boundary hit (≥15 chars) OR buffer stalled ≥1.2 s with ≥30 chars
        should_flush = (
            (len(sentence) >= 15 and sentence[-1] in ".!?।") or
            (len(sentence) >= 30 and elapsed >= 1.2)
        )
        if should_flush:
            last_flush_time = time.perf_counter()
            buffer = ""
            if not first_audio:
                tracker.mark("tts_request_started_at")
            audio = await tts.synthesize(sentence, language)
            if not first_audio:
                tracker.mark("tts_first_audio_at")
                tracker.mark("audio_playback_started_at")
                first_audio = True
            if audio:
                await send({"type": "audio_response", "data": base64.b64encode(audio).decode("ascii"), "format": "wav", "tts_mode": "piper"})
            else:
                await send({"type": "tts_browser", "text": sentence, "language": language})

    tracker.mark("llm_completed_at")

    # 5. Flush any remaining partial sentence (no trailing punctuation)
    remainder = buffer.strip()
    if remainder:
        if not first_audio:
            tracker.mark("tts_request_started_at")
        audio = await tts.synthesize(remainder, language)
        if not first_audio:
            tracker.mark("tts_first_audio_at")
            tracker.mark("audio_playback_started_at")
            first_audio = True
        if audio:
            await send({"type": "audio_response", "data": base64.b64encode(audio).decode("ascii"), "format": "wav", "tts_mode": "piper"})
        else:
            await send({"type": "tts_browser", "text": remainder, "language": language})

    # 6. Push full response for display + memory
    full_text = full_text.strip()
    session.memory.add_message("assistant", full_text)

    await send({"type": "agent_response", "text": full_text, "language": language})
    await send({"type": "memory_update", "memory": session.memory.to_dict()})

    if not first_audio:
        tracker.mark("audio_playback_started_at")
    await send({"type": "latency_update", "data": tracker.to_summary()})


# ── System prompt builder ──────────────────────────────────────────────────────

_LANG_INSTRUCTIONS = {
    "en": "Reply in clear, natural English.",
    "hi": (
        "Reply ONLY in Hindi. Use romanized Hindi (Hinglish) if the user wrote in romanized "
        "script, or Devanagari if the user wrote in Devanagari. NEVER reply in English unless "
        "the user explicitly asks you to switch to English."
    ),
    "es": (
        "Reply ONLY in Spanish. Use natural, conversational Spanish. NEVER reply in English "
        "unless the user explicitly asks you to switch to English."
    ),
}


def _build_system_prompt(memory, language: str = "en") -> str:
    ctx = memory.structured.to_context_string()
    lang_instruction = _LANG_INSTRUCTIONS.get(language, _LANG_INSTRUCTIONS["en"])
    lang_names = {"en": "English", "hi": "Hindi", "es": "Spanish"}
    lang_name = lang_names.get(language, language)
    return f"""\
You are a multilingual real-time voice support assistant.

ACTIVE LANGUAGE: {lang_name} (code: {language})
LANGUAGE INSTRUCTION: {lang_instruction}

STRICT RULES:
- The user is currently speaking {lang_name}. Your reply MUST be in {lang_name}.
- Keep replies short, natural, and voice-friendly — 2 to 4 sentences maximum.
- Preserve full context across language switches. NEVER reset memory or forget previous turns.
- When the user switches language, switch immediately and confirm naturally without announcing it.
- Do NOT say "I noticed you switched to X language" or similar meta-commentary.

MOCK TOOL DATA (treat this as ground truth — do not fabricate other data):
  Orders:
    - Order 4421, email rahul@example.com → status: out for delivery, arriving tomorrow.
      Tracking: https://example.com/track/4421
      Refund: available if delivery fails after the promised date.
  Hotels in Bangalore (next weekend, 2 people, ₹5000/night budget):
    1. Comfort Stay Indiranagar  — ₹4500/night
    2. Metro Grand Koramangala  — ₹4900/night
    3. Garden Nest Whitefield   — ₹4200/night
  Weather:
    Mumbai  → warm and humid, 32°C
    Delhi   → hot and dry, 36°C
    Chennai → warm and breezy, 33°C

CURRENT CONVERSATION MEMORY:
{ctx}
"""


# ── Startup background task ────────────────────────────────────────────────────

@app.on_event("startup")
async def _on_startup():
    async def _session_cleanup_loop():
        while True:
            await asyncio.sleep(300)   # every 5 minutes
            session_manager.cleanup_expired()

    asyncio.create_task(_session_cleanup_loop())
    logger.info("Polyglot Voice Agent started. Navigate to http://localhost:8000")
