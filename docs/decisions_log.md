# Decisions Log

This document records the non-obvious design choices made during the build, with rationale and trade-off analysis.

---

## D-01 · Mixed Hindi-English (Hinglish) language detection

**Decision:** When an utterance contains both Hindi and English tokens, the language router applies rule-based scoring (counting Hindi-pattern matches vs English-pattern matches), falls back to the Whisper language signal (weight = 3 votes), and breaks ties using `last_language` from memory.

**Rationale:** Hinglish is extremely common in Indian conversational contexts (Scenario 3: "Mujhe ek pizza order karna hai, but make it veg only please"). A strict winner-takes-all approach based solely on Whisper would fail on short mixed sentences where Whisper's confidence is low. Combining both signals with a fallback to the last known language produces the most stable UX — the conversation doesn't jump languages unexpectedly.

**Response style:** When the detected language is Hindi, the system prompt instructs the LLM to match the user's romanized / Devanagari style. For Hinglish input, it uses natural Hinglish output rather than forcing pure Hindi.

**Alternative considered:** Hard-coding thresholds like "if >50% tokens are Hindi words → Hindi". Rejected because token-level tokenisation adds a dependency; regex patterns are simpler and sufficient for demo scenarios.

---

## D-02 · TTS fallback chain

**Decision:** The TTS provider attempts (1) piper-tts Python package, then (2) Piper CLI subprocess, then (3) returns `None`. A `None` result causes the backend to send a `tts_browser` WebSocket message; the browser calls `speechSynthesis.speak()`.

**Rationale:** Piper requires separate installation of binary + voice `.onnx` files. For a demo, forcing a hard dependency on Piper would prevent most evaluators from running the app at all. The browser TTS fallback means the app is fully functional out of the box, while Piper is an opt-in upgrade for higher-quality voice.

**Trade-off:** `speechSynthesis` voice quality varies significantly across OS/browser combinations and has limited language support for Hindi on some platforms. This is acceptable for a case-study demo.

---

## D-03 · Whisper model size default

**Decision:** Default to `base` (74 MB, ~1× real-time on modern CPU with int8). Configurable via `WHISPER_MODEL_SIZE` in `.env`.

**Rationale:** The `tiny` model is faster but language detection accuracy drops noticeably for short Hindi and Spanish utterances. `small` gives better multilingual accuracy but is ~240 MB and ~3× slower on CPU. `base` hits the sweet spot for a local demo. Users with a CUDA GPU can switch to `small` or `medium` for better performance.

**Note:** `compute_type="int8"` is used to minimise memory and maximise CPU speed.

---

## D-04 · Audio transport format (JSON + base64 WAV)

**Decision:** Audio is transported as base64-encoded WAV bytes inside a JSON WebSocket text frame, not as binary WebSocket frames or chunked streaming.

**Rationale:** For a demo with button-triggered recording (start → stop), the full audio clip is a few seconds at most. The base64 overhead (~33%) is negligible. Using JSON text frames keeps the protocol uniform and easy to inspect with browser DevTools. Binary frames would require length-prefixing or frame-type disambiguation logic on both sides.

**Alternative considered:** Streaming audio in 200 ms chunks while the user is still speaking, for lower latency. Deferred to a future iteration; it requires server-side Silero VAD streaming which is a significant complexity increase.

---

## D-05 · Client-side WAV encoding

**Decision:** The browser constructs a 16 kHz mono WAV file from raw PCM samples captured via `ScriptProcessorNode` + `OfflineAudioContext` resampling. No external JS library is used.

**Rationale:** `MediaRecorder` outputs `audio/webm;codecs=opus` on most Chromium browsers, which requires `libopus` / `ffmpeg` to decode server-side. To avoid an ffmpeg system dependency, audio is encoded to WAV in the browser (45 lines of standard DataView code) and sent as a format that `soundfile` can decode natively.

---

## D-06 · Language routing rule design

**Decision:** Hindi keywords are matched with ~30 romanized-Hindi regex patterns. Spanish is detected by inverted punctuation (`¿ ¡`) and ~20 vocabulary patterns. Explicit English-switch phrases (`"continue in English"`, `"let's switch back"`, etc.) are hard-wired as force-override patterns that bypass all scoring.

**Rationale:** Whisper language detection on very short utterances (2–5 words) can be unreliable. The rule layer acts as a correction signal. Inverted punctuation is a near-certain signal for Spanish and is cheap to check. Romanized Hindi patterns like `\bhai\b`, `\btoh\b`, `\bkya\b` are common enough to boost scores without causing false positives in English.

**Known limitation:** Some short Hindi words (`\bpar\b`, `\bse\b`, `\bko\b`) also appear in Spanish and English. This is mitigated by the Whisper signal weight (3 votes). Future improvement: use a small language-ID model (fastText `lid.176`) for higher precision.

---

## D-07 · Conversation memory scope

**Decision:** Memory is session-scoped (per browser tab) and held in Python process memory. There is no database persistence.

**Rationale:** This is a demo, not a production system. Adding a database (Redis, SQLite) would increase setup complexity without benefiting the case study evaluation. The memory TTL is 1 hour of inactivity.

---

## D-08 · LLM prompt design

**Decision:** The system prompt always contains (a) language behaviour rules, (b) mock tool data as static text, and (c) the current `StructuredMemory` state as a key-value block. The tool data is embedded verbatim rather than injected via function-calling.

**Rationale:** Function-calling requires model support and adds round-trip complexity. For a demo with fixed data, embedding the data in the system prompt is simpler, more predictable, and works with every OpenRouter model. The structured memory block ensures the LLM can always reference confirmed facts (tracking link, hotel options, weather) without hallucination.

---

## D-09 · VAD strategy

**Decision:** Use faster-whisper's built-in `vad_filter=True` (which uses Silero VAD internally) rather than a separate streaming VAD pass. The browser controls recording start/stop via buttons.

**Rationale:** Silero VAD is already bundled as a faster-whisper dependency. Running a separate Silero VAD pass before transcription would add latency without meaningful benefit in the button-controlled recording model. The built-in filter removes leading/trailing silence, improving transcription accuracy.

**Future improvement:** For a voice-activity-triggered (hands-free) mode, implement streaming Silero VAD on the server to auto-detect utterance boundaries, removing the need for manual stop.
