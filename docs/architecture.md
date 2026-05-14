# Architecture

## System Overview

The Polyglot Voice Agent uses a **hybrid architecture**: all audio processing runs locally on the server, while only the final transcript and conversation memory are sent to the cloud LLM (OpenRouter).

```mermaid
flowchart LR
    subgraph Browser["Browser (Client)"]
        MIC[🎤 Microphone\nWeb Audio API]
        WAVENC[WAV Encoder\nOfflineAudioContext]
        WS_CLIENT[WebSocket Client]
        PLAYBACK[Audio Playback\nAudioContext]
        BROWSER_TTS[Browser TTS\nspeechSynthesis]
        UI[Dashboard UI\nTranscript · Memory\nLatency · Badges]
    end

    subgraph Backend["Backend (FastAPI — Local)"]
        WS_SERVER[WebSocket Server\n/ws/{session_id}]
        SESSION[Session Manager\nper-tab state]

        subgraph STT_BLOCK["STT Layer (Local)"]
            VAD[Silero VAD\n(via faster-whisper\nvad_filter=True)]
            WHISPER[faster-whisper\nSTT + lang detect]
        end

        LANG_ROUTER[Language Router\nWhisper signal +\nrule-based overrides]
        MOCK_TOOLS[Mock Tools\norder · hotel · weather]
        MEMORY[Structured Memory\n+ rolling history]

        subgraph TTS_BLOCK["TTS Layer (Local)"]
            PIPER[Piper TTS\n.onnx voice models]
            TTS_FALLBACK[→ None\nbrowser fallback signal]
        end
    end

    subgraph Cloud["Cloud (OpenRouter)"]
        LLM[LLM\ngoogle/gemini-2.5-flash\nor configured model]
    end

    MIC --> WAVENC --> WS_CLIENT
    WS_CLIENT -->|"audio_chunk + audio_end\n(JSON + base64 WAV)"| WS_SERVER
    WS_CLIENT -->|"text_input\n(JSON)"| WS_SERVER

    WS_SERVER --> SESSION
    SESSION --> STT_BLOCK
    VAD --> WHISPER
    WHISPER -->|"transcript + lang code"| LANG_ROUTER
    LANG_ROUTER --> MOCK_TOOLS
    MOCK_TOOLS --> MEMORY
    MEMORY -->|"transcript + context"| LLM
    LLM -->|"response text"| TTS_BLOCK
    PIPER -->|"WAV bytes"| WS_SERVER
    TTS_FALLBACK -->|"tts_browser msg"| WS_SERVER

    WS_SERVER -->|"transcript · agent_response\naudio_response · tts_browser\nmemory_update · latency_update"| WS_CLIENT
    WS_CLIENT --> PLAYBACK
    WS_CLIENT --> BROWSER_TTS
    WS_CLIENT --> UI
```

---

## Component Descriptions

### Browser

| Component | Role |
|---|---|
| Web Audio API (`ScriptProcessorNode`) | Captures raw PCM float32 from mic at native sample rate |
| `OfflineAudioContext` | Resamples audio to 16 000 Hz |
| WAV Encoder | In-browser PCM→WAV encoding (no external deps) |
| WebSocket Client | Sends audio/text; receives all pipeline events |
| `AudioContext.decodeAudioData` | Plays Piper WAV responses |
| `speechSynthesis` | Browser TTS fallback when Piper is not configured |

### Backend (FastAPI)

| Component | Role |
|---|---|
| `main.py` | App entry, WebSocket handler, turn pipeline |
| `session.py` | Per-tab state: memory + chat history |
| `providers/stt_faster_whisper.py` | Local STT with built-in Silero VAD; language auto-detect |
| `providers/llm_openrouter.py` | Async httpx client to OpenRouter |
| `providers/tts_piper.py` | Piper TTS via Python API then CLI subprocess |
| `services/language_router.py` | Merges Whisper language signal with rule-based overrides |
| `services/memory.py` | `StructuredMemory` + rolling chat history |
| `services/mock_tools.py` | Deterministic order/hotel/weather data + regex memory extractor |
| `services/latency.py` | Per-event timestamp tracker; computes delta spans |

### Cloud

| Component | Role |
|---|---|
| OpenRouter `/chat/completions` | Hosted LLM inference; only receives transcript + memory context |

---

## Data Flow (single turn)

```
1. User speaks → browser WAV (16 kHz mono) → WebSocket audio_chunk + audio_end
2. Backend:  STT (faster-whisper + VAD) → transcript + whisper_lang
3. Backend:  LanguageRouter.detect(text, whisper_lang, memory) → active_lang
4. Backend:  maybe_update_memory(text, memory)  — regex extraction
5. Backend:  LLM prompt = system_prompt(memory) + chat_history → OpenRouter
6. Backend:  PiperTTS.synthesize(response, active_lang) → WAV bytes (or None)
7. Backend → Browser:  transcript · agent_response · audio_response (or tts_browser)
                        memory_update · latency_update
8. Browser:  play WAV (Piper) OR speak (speechSynthesis)
             update transcript / response / memory / latency panels
```

---

## Privacy Boundary

```
┌─────────────────────────────────────────────────────┐
│  LOCAL (never leaves the machine)                   │
│  Raw audio · PCM samples · WAV files                │
│  Whisper model weights · Piper voice models         │
│  Silero VAD model                                   │
└─────────────────────────────────────────────────────┘
                         │  transcript text only
                         ▼
┌─────────────────────────────────────────────────────┐
│  CLOUD (OpenRouter)                                 │
│  Final transcript + structured memory context       │
│  (no audio, no raw PCM)                             │
└─────────────────────────────────────────────────────┘
```

---

## Provider Swap Matrix

| Slot | Production | Demo / No hardware | No API key |
|---|---|---|---|
| STT | `FasterWhisperSTTProvider` | `MockSTTProvider` + text input | same |
| LLM | `OpenRouterLLMProvider` | same (requires key) | `MockLLMProvider` |
| TTS | `PiperTTSProvider` | `MockTTSProvider` + browser TTS | same |

Switch via `.env` flags: `USE_MOCK_STT=true`, `USE_MOCK_LLM=true`, `USE_MOCK_TTS=true`.
