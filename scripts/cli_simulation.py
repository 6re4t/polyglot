#!/usr/bin/env python3
"""
cli_simulation.py — CLI test harness for the Polyglot Voice Agent.

Runs the full pipeline (language detection → memory update → LLM → display)
without a microphone or browser. Audio/TTS is skipped; responses are printed.

Usage:
  # Interactive mode (type messages manually)
  python scripts/cli_simulation.py

  # Force mock LLM (no API key needed)
  python scripts/cli_simulation.py --mock

  # Auto-run a specific scenario
  python scripts/cli_simulation.py --scenario 1

  # Auto-run all four scenarios
  python scripts/cli_simulation.py --all-scenarios

  # Pipe input for CI/automation
  echo "What is the weather in Mumbai?" | python scripts/cli_simulation.py --mock
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from pathlib import Path

# ── Path setup: add backend/ to sys.path ─────────────────────────────────────
_BACKEND = Path(__file__).parent.parent / "backend"
sys.path.insert(0, str(_BACKEND))

from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent / ".env")

from services.language_router import LanguageRouter
from services.memory import SessionMemory
from services.mock_tools import maybe_update_memory
from services.latency import LatencyTracker

# ── Scenario definitions ──────────────────────────────────────────────────────

SCENARIOS: dict[int, list[tuple[str, str]]] = {
    1: [
        ("en",    "Hi, I need to check the status of my order. The order ID is 4421."),
        ("en",    "Yes, the email on the account is rahul@example.com."),
        ("hi",    "Theek hai, lekin delivery kal tak ho jaayegi kya?"),
        ("hi",    "Aur agar nahi hua toh refund mil sakta hai?"),
        ("en",    "Actually let's switch back — can you email me the tracking link?"),
    ],
    2: [
        ("es",    "Hola, quiero reservar un hotel en Bangalore para el próximo fin de semana."),
        ("es",    "Para dos personas, presupuesto de 5000 rupias por noche."),
        ("en",    "Sorry, my Spanish is rusty. Can we continue in English? Tell me again about the second option."),
        ("en",    "Book it. Confirm the dates please."),
    ],
    3: [
        ("mixed", "Mujhe ek pizza order karna hai, but make it veg only please."),
        ("en",    "And add a coke too."),
    ],
    4: [
        ("en",    "What's the weather in Mumbai today?"),
        ("hi",    "Aur Delhi mein?"),
        ("es",    "¿Y en Chennai?"),
        ("en",    "Compare all three for me."),
    ],
}

SCENARIO_NAMES = {
    1: "Customer Support: Order Status",
    2: "Travel Planning: Hotel Booking",
    3: "Code-Switching Within an Utterance",
    4: "Rapid Switching Stress Test",
}

# ── ANSI colours (disabled on non-TTY) ───────────────────────────────────────

_USE_COLOR = sys.stdout.isatty()

_C = {
    "reset":  "\033[0m"   if _USE_COLOR else "",
    "bold":   "\033[1m"   if _USE_COLOR else "",
    "dim":    "\033[2m"   if _USE_COLOR else "",
    "en":     "\033[94m"  if _USE_COLOR else "",   # blue
    "hi":     "\033[92m"  if _USE_COLOR else "",   # green
    "es":     "\033[93m"  if _USE_COLOR else "",   # yellow
    "mixed":  "\033[95m"  if _USE_COLOR else "",   # purple
    "red":    "\033[91m"  if _USE_COLOR else "",
    "cyan":   "\033[96m"  if _USE_COLOR else "",
}


def c(text: str, key: str) -> str:
    return f"{_C.get(key, '')}{text}{_C['reset']}"


# ── System prompt (mirrors main.py) ──────────────────────────────────────────

def _system_prompt(memory: SessionMemory) -> str:
    ctx = memory.structured.to_context_string()
    return f"""\
You are a multilingual real-time voice support assistant.

STRICT RULES:
- Always reply in the user's LATEST active language.
- Keep replies short, natural, and voice-friendly — 2 to 4 sentences maximum.
- Preserve context across language switches. NEVER reset memory.
- For English: clear natural English.
- For Hindi: natural romanized Hindi / Hinglish matching the user's style.
- For Spanish: natural conversational Spanish.

MOCK TOOL DATA (ground truth):
  Orders: Order 4421 / rahul@example.com → out for delivery, tomorrow.
    Tracking: https://example.com/track/4421
    Refund: available if delivery fails after promised date.
  Hotels in Bangalore (next weekend, 2 pax, ₹5000/night):
    1. Comfort Stay Indiranagar  ₹4500/night
    2. Metro Grand Koramangala  ₹4900/night
    3. Garden Nest Whitefield   ₹4200/night
  Weather: Mumbai 32°C warm+humid | Delhi 36°C hot+dry | Chennai 33°C breezy

CURRENT MEMORY:
{ctx}
"""


# ── Single turn ───────────────────────────────────────────────────────────────

async def run_turn(
    text:         str,
    memory:       SessionMemory,
    lang_router:  LanguageRouter,
    llm,
    turn_num:     int,
) -> dict:
    tracker = LatencyTracker()
    tracker.mark("speech_end_detected_at")

    # Language detection (text-only path)
    tracker.mark("stt_started_at")
    detected = lang_router.detect_text_only(text, memory)
    # Simulate a small STT delay so latency numbers are non-zero
    await asyncio.sleep(0.05)
    tracker.mark("stt_final_at")

    # Memory update
    maybe_update_memory(text, memory)
    memory.last_language = detected
    memory.add_message("user", text)

    # LLM
    tracker.mark("llm_request_started_at")
    result = await llm.generate(memory.chat_history, _system_prompt(memory))
    tracker.mark("llm_completed_at")
    tracker.mark("llm_first_token_at")

    # TTS skipped in CLI mode
    tracker.mark("tts_request_started_at")
    tracker.mark("tts_first_audio_at")
    tracker.mark("audio_playback_started_at")

    memory.add_message("assistant", result.text)

    return {
        "turn":              turn_num,
        "user_text":         text,
        "detected_language": detected,
        "response":          result.text,
        "memory":            memory.structured.to_dict(),
        "latency":           tracker.to_summary(),
        "model":             result.model,
    }


def print_turn(data: dict) -> None:
    lang     = data["detected_language"]
    lat      = data["latency"]
    mem_raw  = data["memory"]
    # Strip None / empty from memory display
    mem_show = {
        k: v for k, v in mem_raw.items()
        if v is not None and v != [] and k != "hotel_options"
    }

    print(f"\n{c('─' * 64, 'dim')}")
    print(c(f"  Turn {data['turn']}", "bold"))
    print(f"  {c('User:', 'dim')}        {data['user_text']}")
    print(f"  {c('Language:', 'dim')}    {c(lang.upper(), lang)}")
    print(f"  {c('Response:', 'dim')}    {c(data['response'], 'bold')}")
    if mem_show:
        mem_str = json.dumps(mem_show, ensure_ascii=False)
        print(f"  {c('Memory:', 'dim')}     {c(mem_str, 'dim')}")
    stt  = lat.get("stt_latency_ms")
    llm_ = lat.get("llm_total_ms")
    tot  = lat.get("total_latency_ms")
    print(
        f"  {c('Latency:', 'dim')}    "
        f"STT={c(str(stt), 'cyan')}ms  "
        f"LLM={c(str(llm_), 'cyan')}ms  "
        f"Total={c(str(tot), 'cyan')}ms  "
        f"[{c(data['model'], 'dim')}]"
    )


# ── Interactive mode ──────────────────────────────────────────────────────────

async def interactive(lang_router: LanguageRouter, llm, use_mock: bool) -> None:
    memory   = SessionMemory()
    turn_num = 0

    print(c("\nPolyglot Voice Agent — CLI Mode", "bold"))
    print(f"  LLM backend : {c('mock (no API key)', 'dim') if use_mock else c('OpenRouter', 'bold')}")
    print(f"  Commands    : {c('quit', 'dim')} to exit  |  {c('reset', 'dim')} to clear memory\n")

    while True:
        try:
            text = input(c("You › ", "bold")).strip()
        except (EOFError, KeyboardInterrupt):
            print(f"\n{c('Goodbye!', 'dim')}")
            break

        if not text:
            continue
        if text.lower() == "quit":
            print(c("Goodbye!", "dim"))
            break
        if text.lower() == "reset":
            memory   = SessionMemory()
            turn_num = 0
            print(c("  Memory cleared.", "dim"))
            continue

        turn_num += 1
        data = await run_turn(text, memory, lang_router, llm, turn_num)
        print_turn(data)


# ── Scenario mode ─────────────────────────────────────────────────────────────

async def run_scenario(
    num:         int,
    lang_router: LanguageRouter,
    llm,
    use_mock:    bool,
) -> None:
    turns = SCENARIOS.get(num)
    if not turns:
        print(f"Unknown scenario: {num}")
        return

    memory = SessionMemory()
    name   = SCENARIO_NAMES.get(num, f"Scenario {num}")
    print(c(f"\n{'═' * 64}", "bold"))
    print(c(f"  Scenario {num} — {name}", "bold"))
    print(c(f"{'═' * 64}", "bold"))

    for i, (_lang_hint, text) in enumerate(turns, 1):
        data = await run_turn(text, memory, lang_router, llm, i)
        print_turn(data)
        if i < len(turns):
            await asyncio.sleep(0.1)   # small pause between turns for readability

    print(c(f"\n  ✓ Scenario {num} complete.", "bold"))


# ── Entry point ───────────────────────────────────────────────────────────────

async def main() -> None:
    parser = argparse.ArgumentParser(
        description="CLI simulation for Polyglot Voice Agent",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--mock",          action="store_true",
                        help="Force mock LLM (no API key needed)")
    parser.add_argument("--scenario",      type=int, choices=[1, 2, 3, 4],
                        help="Run a specific scenario (1–4)")
    parser.add_argument("--all-scenarios", action="store_true",
                        help="Run all four scenarios sequentially")
    args = parser.parse_args()

    use_mock = args.mock or not os.getenv("OPENROUTER_API_KEY")

    if use_mock:
        from providers.mock import MockLLMProvider
        llm = MockLLMProvider()
    else:
        from providers.llm_openrouter import OpenRouterLLMProvider
        llm = OpenRouterLLMProvider()

    lang_router = LanguageRouter()

    if args.all_scenarios:
        for n in [1, 2, 3, 4]:
            await run_scenario(n, lang_router, llm, use_mock)
            if n < 4:
                try:
                    input(c("\n  Press Enter to run next scenario…", "dim"))
                except (EOFError, KeyboardInterrupt):
                    break
    elif args.scenario:
        await run_scenario(args.scenario, lang_router, llm, use_mock)
    else:
        await interactive(lang_router, llm, use_mock)


if __name__ == "__main__":
    asyncio.run(main())
