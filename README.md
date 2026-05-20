# Polyglot Voice Agent

A real-time multilingual voice companion that supports English, Hindi, and Spanish. Language switches are detected within 1–2 utterances using local STT (faster-whisper) with rule-based overrides. Conversation context is preserved across language switches. Audio stays local; only the final transcript and memory go to OpenRouter.

**Latency target:** under 1.2 s end-of-speech → start-of-agent-speech (stretch: under 800 ms), achieved via:
- `tiny` Whisper model (~250 ms STT on CPU)
- Sentence-level LLM streaming — browser TTS starts on the *first* sentence, not after the full response
- Urdu/Hindi disambiguation — `ur` detections are re-transcribed in Devanagari automatically

---

## Architecture Summary

```
Browser mic → WAV (browser-encoded) → WebSocket
→ FastAPI → faster-whisper STT + VAD + language detect
→ LanguageRouter (Whisper + rules)
→ MemoryManager + MockTools
→ OpenRouter LLM
→ Piper TTS (or browser speechSynthesis fallback)
→ Browser playback
```

See [docs/architecture.md](docs/architecture.md) for the full Mermaid diagram and component breakdown.

---

## Prerequisites

| Requirement | Notes |
|---|---|
| Python 3.10+ | 3.11 recommended |
| pip | Standard |
| OpenRouter API key | Get one free at https://openrouter.ai |
| ffmpeg (optional) | Only needed if sending non-WAV audio formats |
| Piper + voice models (optional) | Browser TTS fallback works without it |

---

## Quick Start

### 1 — Clone / enter the project

```bash
cd polyglot-voice-agent
```

### 2 — Create and activate a virtual environment

```bash
# Windows
python -m venv .venv
.venv\Scripts\activate

# macOS / Linux
python -m venv .venv
source .venv/bin/activate
```

### 3 — Install dependencies

```bash
pip install -r requirements.txt
```

> **Note:** `faster-whisper` downloads the Whisper model on first run (~39 MB for `tiny`).
> This happens automatically when you first send audio.

### 4 — Configure environment

```bash
copy .env.example .env        # Windows
# cp .env.example .env        # macOS/Linux
```

Edit `.env` and set your OpenRouter API key:

```env
OPENROUTER_API_KEY=sk-or-v1-...
OPENROUTER_MODEL=google/gemini-2.5-flash
```

> **Without an API key:** The app still runs using `MockLLMProvider` with keyword-based responses. Set `USE_MOCK_LLM=true` or leave `OPENROUTER_API_KEY` empty.

### 5 — Run the backend

```bash
# Recommended — single command from the project root
python run.py

# Or without hot-reload (slightly faster startup)
python run.py --no-reload

# Or manually from the backend directory
cd backend
uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

### 6 — Open the browser

Navigate to: **http://localhost:8000**

---

## Using the App

### Microphone input
1. Click **🎤 Record** — grant mic permission if prompted.
2. Speak your message.
3. Click **⏹ Stop** — audio is encoded to WAV, sent to backend, transcribed, and processed.

### Text input
Type in the text box and press **Enter** or click **Send**.
Full language detection and LLM processing runs exactly as with audio.

### Scenario test buttons
Click any scenario turn button at the bottom of the page to automatically send that turn as text. Useful for demos without a microphone.

### Dashboard panels
| Panel | Shows |
|---|---|
| Voice Hub (left column) | Mic state (standby / recording / processing), Record + Stop buttons |
| Language badge | Active language: EN / HI / ES |
| TTS badge | `Piper` or `browser` |
| Chat thread (centre) | Turn-by-turn bubbles — user (right) and agent (left) with typing indicator |
| Memory cards (right, Cards tab) | Order tracking, Hotel booking, Weather — live structured memory |
| Memory JSON (right, JSON tab) | Raw memory object |
| Telemetry meters | STT / LLM / TTS breakdown bars + total latency per turn |
| Scenario buttons (bottom) | Pre-loaded test turns for all 4 scenarios |

---

## CLI Simulation (no browser needed)

The CLI runs the full pipeline — language detection, memory, LLM — without audio or a browser.

```bash
# Interactive mode (type messages manually)
python scripts/cli_simulation.py

# Force mock LLM (works without API key)
python scripts/cli_simulation.py --mock

# Auto-run scenario 1
python scripts/cli_simulation.py --scenario 1

# Auto-run all four scenarios
python scripts/cli_simulation.py --all-scenarios

# All scenarios with mock LLM
python scripts/cli_simulation.py --mock --all-scenarios
```

Each turn prints: user input, detected language, agent response, populated memory fields, and latency numbers.

---

## Environment Variables

| Variable | Default | Description |
|---|---|---|
| `OPENROUTER_API_KEY` | _(required for real LLM)_ | Your OpenRouter API key |
| `OPENROUTER_MODEL` | `google/gemini-2.5-flash` | Model to use. Options: `openai/gpt-4o-mini`, `anthropic/claude-3.5-haiku`, `qwen/qwen-2.5-7b-instruct` |
| `OPENROUTER_SITE_URL` | `http://localhost:8000` | Sent as `HTTP-Referer` header |
| `OPENROUTER_APP_NAME` | `polyglot-voice-agent` | Sent as `X-Title` header |
| `WHISPER_MODEL_SIZE` | `tiny` | Whisper model: `tiny`, `base`, `small`, `medium`, `large-v2` |
| `PIPER_EXECUTABLE` | `piper` | Path to Piper binary |
| `PIPER_VOICE_PATH_EN` | _(empty)_ | Path to English `.onnx` voice model |
| `PIPER_VOICE_PATH_HI` | _(empty)_ | Path to Hindi `.onnx` voice model |
| `PIPER_VOICE_PATH_ES` | _(empty)_ | Path to Spanish `.onnx` voice model |
| `USE_MOCK_STT` | `false` | Force MockSTTProvider (skips faster-whisper) |
| `USE_MOCK_LLM` | `false` | Force MockLLMProvider (skips OpenRouter) |
| `USE_MOCK_TTS` | `false` | Force MockTTSProvider (browser TTS always) |

---

## Piper TTS Setup (Optional)

The app runs without Piper — browser `speechSynthesis` is used as a fallback.
To enable high-quality local TTS:

### Step 1 — Install piper-tts Python package
```bash
pip install piper-tts
```

### Step 2 — Download voice models
Create a `voices/` folder in the project root and download `.onnx` files:

**English** (recommended: `en_US-lessac-medium`):
```
https://github.com/rhasspy/piper/releases/download/v1.2.0/en_US-lessac-medium.onnx
```

**Hindi** (recommended: `hi_IN-deepika-medium`):
```
https://github.com/rhasspy/piper/releases/download/v1.2.0/hi_IN-deepika-medium.onnx
```

**Spanish** (recommended: `es_ES-sharvard-medium`):
```
https://github.com/rhasspy/piper/releases/download/v1.2.0/es_ES-sharvard-medium.onnx
```

### Step 3 — Update `.env`
```env
PIPER_VOICE_PATH_EN=voices/en_US-lessac-medium.onnx
PIPER_VOICE_PATH_HI=voices/hi_IN-deepika-medium.onnx
PIPER_VOICE_PATH_ES=voices/es_ES-sharvard-medium.onnx
```

The TTS badge in the browser will switch from `browser` to `Piper`.

> **Alternative:** Download the standalone `piper` binary from the same GitHub releases page and set `PIPER_EXECUTABLE=path/to/piper`. The backend tries the Python API first, then the CLI subprocess.

---

## Project Structure

```
polyglot-voice-agent/
├── run.py                        # Single-command launcher (python run.py)
├── backend/
│   ├── main.py                   # FastAPI app + streaming WebSocket pipeline
│   ├── config.py                 # Settings from .env
│   ├── session.py                # Per-tab session state
│   ├── providers/
│   │   ├── base.py               # Abstract STT / LLM / TTS interfaces (+ generate_stream)
│   │   ├── stt_faster_whisper.py # Local STT + language detection + ur→hi remap
│   │   ├── llm_openrouter.py     # OpenRouter async client with SSE streaming
│   │   ├── tts_piper.py          # Piper TTS (Python API + CLI fallback)
│   │   └── mock.py               # Mock providers for testing
│   ├── services/
│   │   ├── language_router.py    # Whisper + rule-based language routing (ur→hi alias)
│   │   ├── memory.py             # StructuredMemory + rolling history
│   │   ├── mock_tools.py         # Order / hotel / weather data + regex extractor
│   │   └── latency.py            # Per-turn latency tracker
│   └── static/
│       ├── index.html            # Dashboard UI
│       ├── app.js                # Frontend logic
│       └── styles.css            # Styles
├── scripts/
│   └── cli_simulation.py         # CLI test harness
├── docs/
│   ├── architecture.md           # Mermaid diagram + component docs
│   ├── decisions_log.md          # Design decision records
│   └── scenario_test_guide.md    # Step-by-step test instructions
├── requirements.txt
├── .env.example
└── README.md
```

---

## Supported Scenarios

| # | Name | Languages |
|---|---|---|
| 1 | Customer Support: Order Status | EN → HI → EN |
| 2 | Travel Planning: Hotel Booking | ES → EN |
| 3 | Code-Switching Within Utterance | HI+EN mixed |
| 4 | Rapid Switching Stress Test | EN → HI → ES → EN |

Full test guide: [docs/scenario_test_guide.md](docs/scenario_test_guide.md)

---

## API Endpoints

| Endpoint | Method | Description |
|---|---|---|
| `/` | GET | Serves the dashboard UI |
| `/health` | GET | Returns provider types, model name, active session count |
| `/sessions/{id}/memory` | GET | Returns structured memory + history length for a session |
| `/ws/{session_id}` | WebSocket | Main real-time channel |

---

## Latency Instrumentation

Each turn measures and reports (in milliseconds):
- `stt_latency_ms` — time to transcribe audio
- `llm_total_ms` — time from LLM request to complete response
- `tts_latency_ms` — time to synthesize audio
- `total_latency_ms` — end-of-speech to audio-playback-start

---

## Troubleshooting

**`faster_whisper` import error:**
```bash
pip install faster-whisper
```

**Whisper model not downloading:**
Check network connectivity. Model is cached in `~/.cache/huggingface` after first download.

**`soundfile` decode error:**
The browser must send a valid WAV file. Check the browser console for JS errors in `encodeWav()`.

**OpenRouter 401 error:**
Verify `OPENROUTER_API_KEY` in `.env` is correct and has credits.

**Port 8000 in use:**
```bash
uvicorn main:app --port 8001
```
Then open `http://localhost:8001`.

**Browser TTS not speaking Hindi:**
`hi-IN` voice may not be installed on your OS. On Windows: Settings → Time & Language → Speech → Add voices.

---

## License

MIT. See `LICENSE` (not included in this demo repo).
