# Scenario Test Guide

This guide provides step-by-step instructions for verifying all four conversation scenarios.
Each scenario can be run via the **browser UI** (text input or mic) or the **CLI simulation script**.

---

## Prerequisites

The app must be running before executing any scenario:
```bash
cd polyglot-voice-agent/backend
uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

Browser: open `http://localhost:8000`

---

## CLI Quick Reference

```bash
# All scenarios with mock LLM (no API key needed)
python scripts/cli_simulation.py --mock --all-scenarios

# Single scenario with real OpenRouter
python scripts/cli_simulation.py --scenario 1

# Interactive mode
python scripts/cli_simulation.py
```

---

## Scenario 1 — Customer Support: Order Status

**Goal:** Verify language switching EN→HI→EN, order lookup, refund policy, and tracking link.

### Browser Steps

1. Click **T1 EN · Order 4421** scenario button (or type):
   > `Hi, I need to check the status of my order. The order ID is 4421.`
   
   **Expected:**
   - Language badge: `EN`
   - Agent asks for email verification in English.
   - Memory: `order_id: "4421"`

2. Click **T2 EN · Email** (or type):
   > `Yes, the email on the account is rahul@example.com.`
   
   **Expected:**
   - Language badge: `EN`
   - Agent confirms order is out for delivery and arriving tomorrow.
   - Memory: `order_id: "4421"`, `email: "rahul@example.com"`, `order_status: "out for delivery"`, `tracking_link: "https://example.com/track/4421"`

3. Click **T3 HI · Delivery?** (or type):
   > `Theek hai, lekin delivery kal tak ho jaayegi kya?`
   
   **Expected:**
   - Language badge switches to `HI`.
   - Agent responds in Hindi/Hinglish, confirms tomorrow delivery.
   - Memory: `last_language: "hi"`

4. Click **T4 HI · Refund?** (or type):
   > `Aur agar nahi hua toh refund mil sakta hai?`
   
   **Expected:**
   - Language badge: `HI`
   - Agent explains refund policy in Hindi.
   - Memory: `refund_policy` field populated.

5. Click **T5 EN · Tracking** (or type):
   > `Actually let's switch back — can you email me the tracking link?`
   
   **Expected:**
   - Language badge switches back to `EN`.
   - Agent responds in English and mentions/provides `https://example.com/track/4421`.
   - Memory: `last_language: "en"`

### CLI Command
```bash
python scripts/cli_simulation.py --scenario 1
```

### Pass Criteria
- [ ] Language badge changes: EN → EN → HI → HI → EN
- [ ] Turn 2 response includes "out for delivery" and "tomorrow"
- [ ] Turn 3 response is in Hindi/Hinglish (not English)
- [ ] Turn 4 response mentions refund policy
- [ ] Turn 5 response is in English and includes the tracking URL

---

## Scenario 2 — Travel Planning: Hotel Booking

**Goal:** Verify ES→EN language switch, hotel option recall across languages.

### Browser Steps

1. Click **T1 ES · Hotel Bangalore** (or type):
   > `Hola, quiero reservar un hotel en Bangalore para el próximo fin de semana.`
   
   **Expected:**
   - Language badge: `ES`
   - Agent responds in Spanish, may ask for number of people and budget.
   - Memory: `hotel_city: "Bangalore"`, `hotel_dates: "next weekend"`

2. Click **T2 ES · 2 pax ₹5000** (or type):
   > `Para dos personas, presupuesto de 5000 rupias por noche.`
   
   **Expected:**
   - Language badge: `ES`
   - Agent presents all three hotel options in Spanish.
   - Memory: `hotel_people: 2`, `hotel_budget: 5000`, `hotel_options` populated.

3. Click **T3 EN · Switch + Option 2** (or type):
   > `Sorry, my Spanish is rusty. Can we continue in English? Tell me again about the second option.`
   
   **Expected:**
   - Language badge switches to `EN`.
   - Agent responds in English and describes **Metro Grand Koramangala** (₹4900/night).
   - "Can we continue in English" triggers the EN force-override.

4. Click **T4 EN · Book it** (or type):
   > `Book it. Confirm the dates please.`
   
   **Expected:**
   - Language badge: `EN`
   - Agent confirms booking intent for Metro Grand Koramangala and states dates as "next weekend".
   - Memory: `selected_hotel_option: "Metro Grand Koramangala"` (if regex matches "book it" → "second").

### CLI Command
```bash
python scripts/cli_simulation.py --scenario 2
```

### Pass Criteria
- [ ] Language badge changes: ES → ES → EN → EN
- [ ] Turn 2 response lists all three hotel options in Spanish
- [ ] Turn 3 response is in English; names "Metro Grand Koramangala"
- [ ] Turn 4 confirms "next weekend" dates

---

## Scenario 3 — Code-Switching Within an Utterance

**Goal:** Verify graceful handling of mixed Hindi+English within a single utterance.

### Browser Steps

1. Click **T1 MIX · Veg pizza** (or type):
   > `Mujhe ek pizza order karna hai, but make it veg only please.`
   
   **Expected:**
   - Language badge: `HI` (Hindi keywords dominate or Whisper detects HI).
   - Agent acknowledges in Hindi/Hinglish or a natural mixed style and confirms veg pizza.
   - Memory: `last_topic: "food"`

2. Click **T2 EN · Add Coke** (or type):
   > `And add a coke too.`
   
   **Expected:**
   - Language badge switches to `EN`.
   - Agent responds in English and remembers the pizza order.

### CLI Command
```bash
python scripts/cli_simulation.py --scenario 3
```

### Pass Criteria
- [ ] Turn 1 is not rejected or misrouted; agent acknowledges veg pizza
- [ ] Turn 2 is in English; agent mentions both pizza and Coke

---

## Scenario 4 — Rapid Switching Stress Test

**Goal:** Verify three consecutive language switches (EN→HI→ES→EN) and cross-language memory recall.

### Browser Steps

1. Click **T1 EN · Mumbai** (or type):
   > `What's the weather in Mumbai today?`
   
   **Expected:**
   - Language badge: `EN`
   - Agent: "Mumbai is warm and humid at 32°C"
   - Memory: `weather_cities: ["mumbai"]`

2. Click **T2 HI · Delhi** (or type):
   > `Aur Delhi mein?`
   
   **Expected:**
   - Language badge: `HI` (within 1 turn of switching)
   - Agent: answers Delhi weather in Hindi/Hinglish (~36°C hot+dry)
   - Memory: `weather_cities: ["mumbai", "delhi"]`

3. Click **T3 ES · Chennai** (or type):
   > `¿Y en Chennai?`
   
   **Expected:**
   - Language badge: `ES` (¿ is a near-certain Spanish signal)
   - Agent: answers Chennai weather in Spanish (~33°C)
   - Memory: `weather_cities: ["mumbai", "delhi", "chennai"]`

4. Click **T4 EN · Compare** (or type):
   > `Compare all three for me.`
   
   **Expected:**
   - Language badge: `EN`
   - Agent compares Mumbai (32°C humid), Delhi (36°C dry), Chennai (33°C breezy).
   - No information is forgotten between language switches.

### CLI Command
```bash
python scripts/cli_simulation.py --scenario 4
```

### Pass Criteria
- [ ] Language badge changes each turn: EN → HI → ES → EN
- [ ] Each language switch happens within 1 turn (not 2)
- [ ] Turn 4 accurately references all three cities without prompting
- [ ] Turn 4 response is in English

---

## Automated Full Run

To run all four scenarios back-to-back in CLI with mock LLM:
```bash
python scripts/cli_simulation.py --mock --all-scenarios
```

Expected output for each scenario: 4–5 turns, each showing:
- Detected language (coloured)
- Agent response
- Populated memory fields
- Latency numbers (STT ~50ms mock, LLM varies, Total)

---

## Troubleshooting

| Symptom | Likely Cause | Fix |
|---|---|---|
| Language badge stuck on `EN` | Rule patterns not matching | Check transcript text matches Hindi/Spanish keywords |
| Agent responds in wrong language | LLM ignoring system prompt | Verify `OPENROUTER_MODEL` supports multilingual; try `google/gemini-2.5-flash` |
| "No speech detected" on mic | Audio too short or silent | Speak for >1 second before stopping |
| TTS badge shows "browser" always | Piper not configured | See README → Piper setup; browser TTS is the intended fallback |
| WebSocket disconnects immediately | Port conflict | Kill other processes on 8000; use `--port 8001` |
| `ImportError: faster_whisper` | Not installed | `pip install faster-whisper` (downloads ~74 MB on first run) |
| Mock LLM gives generic response | Keyword not in `_RESPONSES` | Add the key phrase to `providers/mock.py → _RESPONSES` |
