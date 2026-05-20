/**
 * app.js — Polyglot Voice Agent frontend
 *
 * Responsibilities:
 *  - WebSocket session management
 *  - Microphone recording via Web Audio API (produces 16 kHz mono WAV)
 *  - Sending audio / text to backend
 *  - Handling all incoming WS message types
 *  - Piper WAV playback via AudioContext
 *  - Browser speechSynthesis TTS fallback
 *  - Updating all UI panels (transcript, response, memory, latency, badges)
 *  - Custom state visualizer changes
 *  - Structured memory card rendering and tab controllers
 */

'use strict';

// ── Session ID (persists across page reloads for the same tab) ──────────────
let SESSION_ID = sessionStorage.getItem('sessionId');
if (!SESSION_ID) {
  SESSION_ID = crypto.randomUUID();
  sessionStorage.setItem('sessionId', SESSION_ID);
}

// ── State ───────────────────────────────────────────────────────────────────
let ws             = null;
let isRecording    = false;
let audioCtx       = null;
let mediaStream    = null;
let scriptNode     = null;
let sourceNode     = null;
let pcmSamples     = [];   // Float32 samples collected during recording
let turnCounter    = 0;
let latencyRows    = [];   // Array of latency summary objects for the table
let pendingUserBubble = null;  // user bubble shown before transcript arrives

// ── VAD state (auto-stop on silence) ────────────────────────────────────────────
const VAD_SPEECH_THRESHOLD = 0.015; // RMS energy: above = speech, below = silence
const VAD_SILENCE_MS       = 900;   // ms of continuous silence to trigger auto-stop
const VAD_MIN_SPEECH_MS    = 400;   // don’t start silence countdown until user has spoken this long
let   vadHasSpeech    = false;
let   vadSpeechStart  = null;
let   vadSilenceStart = null;

// ── Barge-in / interrupt state ───────────────────────────────────────────────
const BARGE_THRESHOLD = 0.020;   // RMS above this during TTS = user is speaking
let   isSpeaking      = false;   // true while agent speechSynthesis is active
let   bargeinStream   = null;
let   bargeinCtx      = null;
let   bargeinSrc      = null;
let   bargeinNode     = null;

// ── DOM refs ────────────────────────────────────────────────────────────────
const micBtn        = document.getElementById('mic-btn');
const micBtnIcon    = document.getElementById('mic-btn-icon');
const micBtnLabel   = document.getElementById('mic-btn-label');
const textInput     = document.getElementById('text-input');
const sendBtn       = document.getElementById('send-btn');
const connBadge     = document.getElementById('conn-badge');
const langBadge     = document.getElementById('lang-badge');
const ttsBadge      = document.getElementById('tts-badge');
const statusBar     = document.getElementById('status-bar');
const chatThread    = document.getElementById('chat-thread');
const memoryPre     = document.getElementById('memory-pre');
const latencyTbody  = document.getElementById('latency-tbody');

// ── Voice State Visualizer Helper ──────────────────────────────────────────
function setVoiceState(state, title, desc) {
  const panel = document.getElementById('voice-hub-panel');
  if (!panel) return;
  panel.className = `voice-hub-panel panel state-${state}`;
  if (title) document.getElementById('voice-title').textContent = title;
  if (desc) document.getElementById('voice-desc').textContent = desc;
}

// Badge text updater that doesn't blow away the dot icon
function updateBadgeText(badge, text) {
  const textEl = badge.querySelector('.badge-text');
  if (textEl) {
    textEl.textContent = text;
  } else {
    badge.textContent = text;
  }
}

const MIC_SVG_PATHS  = '<path d="M12 2a3 3 0 0 0-3 3v7a3 3 0 0 0 6 0V5a3 3 0 0 0-3-3Z"></path><path d="M19 10v1a7 7 0 0 1-14 0v-1"></path><line x1="12" y1="19" x2="12" y2="22"></line>';
const STOP_SVG_PATHS = '<rect x="4" y="4" width="16" height="16" rx="2" ry="2"></rect>';

function setMicBtnRecording(on) {
  if (on) {
    micBtn.classList.add('recording');
    micBtn.title     = 'Stop recording';
    micBtnLabel.textContent = 'Stop';
    micBtnIcon.innerHTML    = STOP_SVG_PATHS;
  } else {
    micBtn.classList.remove('recording');
    micBtn.title     = 'Start recording';
    micBtnLabel.textContent = 'Record';
    micBtnIcon.innerHTML    = MIC_SVG_PATHS;
  }
}

// ── WebSocket ────────────────────────────────────────────────────────────────

function connectWS() {
  const proto = location.protocol === 'https:' ? 'wss' : 'ws';
  const url   = `${proto}://${location.host}/ws/${SESSION_ID}`;
  ws = new WebSocket(url);

  ws.onopen = () => {
    setStatus('Connected', false);
    updateBadgeText(connBadge, 'Connected');
    connBadge.className = 'badge on';
    statusBar.className = 'connected';
    // Ping to keep alive
    setInterval(() => ws && ws.readyState === WebSocket.OPEN && ws.send(JSON.stringify({type:'ping'})), 20000);
  };

  ws.onclose = () => {
    setStatus('Disconnected — reconnecting in 3s…', true);
    updateBadgeText(connBadge, 'Disconnected');
    connBadge.className = 'badge off';
    statusBar.className = 'error';
    setVoiceState('standby', 'System Offline', 'Attempting server reconnection...');
    setTimeout(connectWS, 3000);
  };

  ws.onerror = (e) => {
    setStatus('WebSocket error', true);
    statusBar.className = 'error';
    console.error('WS error', e);
  };

  ws.onmessage = (event) => {
    let msg;
    try { msg = JSON.parse(event.data); }
    catch { return; }
    handleMessage(msg);
  };
}

function wsSend(obj) {
  if (ws && ws.readyState === WebSocket.OPEN) {
    ws.send(JSON.stringify(obj));
  }
}

// ── Message handler ──────────────────────────────────────────────────────────

function handleMessage(msg) {
  switch (msg.type) {

    case 'pong':
      break;

    case 'transcript':
      if (window.speechSynthesis) window.speechSynthesis.cancel(); // stop previous agent speech
      isSpeaking = false;
      stopBargeinMonitor();
      setStatus(`Transcribed [${msg.language?.toUpperCase()}]: "${msg.text}"`, false);
      setVoiceState('processing', 'Thinking...', 'Awaiting LLM generation...');
      addUserBubble(msg.text, msg.language || 'en');
      updateLangBadge(msg.language);
      showTypingIndicator();
      break;

    case 'agent_response':
      setStatus('Agent responded.', false);
      setVoiceState('processing', 'Speaking...', 'Rendering response...');
      removeTypingIndicator();
      addAgentBubble(msg.text, msg.language || 'en');
      break;

    case 'audio_response':
      // Piper WAV audio from backend
      updateBadgeText(ttsBadge, 'TTS: Piper');
      ttsBadge.className = 'badge piper';
      playWavBase64(msg.data).catch(console.error);
      break;

    case 'tts_browser':
      // Browser speechSynthesis fallback
      updateBadgeText(ttsBadge, 'TTS: Browser');
      ttsBadge.className = 'badge';
      speakBrowser(msg.text, msg.language);
      break;

    case 'memory_update':
      renderMemory(msg.memory);
      break;

    case 'latency_update':
      renderLatency(msg.data);
      break;

    case 'info':
      setStatus(msg.message, false);
      break;

    case 'error':
      setStatus(`Error: ${msg.message}`, true);
      statusBar.className = 'error';
      setVoiceState('standby', 'System Standby', `Error: ${msg.message}`);
      console.error('Backend error:', msg.message);
      break;
  }
}

// ── Microphone recording ──────────────────────────────────────────────────────

micBtn.addEventListener('click', () => {
  if (isRecording) stopRecording();
  else startRecording();
});

async function startRecording() {
  if (isRecording) return;
  // Barge-in: cancel any in-progress agent speech before acquiring the mic
  if (isSpeaking || (window.speechSynthesis && window.speechSynthesis.speaking)) {
    window.speechSynthesis.cancel();
    isSpeaking = false;
    stopBargeinMonitor();
  }
  try {
    mediaStream = await navigator.mediaDevices.getUserMedia({ audio: true, video: false });
  } catch (e) {
    setStatus(`Mic access denied: ${e.message}`, true);
    statusBar.className = 'error';
    setVoiceState('standby', 'Mic Access Error', e.message);
    return;
  }

  // Use AudioContext at the native sample rate; we resample to 16 kHz on stop.
  audioCtx   = new (window.AudioContext || window.webkitAudioContext)();
  sourceNode = audioCtx.createMediaStreamSource(mediaStream);

  // ScriptProcessorNode: 4096-sample buffer, 1 input channel, 1 output channel.
  scriptNode = audioCtx.createScriptProcessor(4096, 1, 1);
  pcmSamples = [];

  // Reset VAD state for this recording session
  vadHasSpeech = false; vadSpeechStart = null; vadSilenceStart = null;

  scriptNode.onaudioprocess = (e) => {
    const channelData = e.inputBuffer.getChannelData(0);
    pcmSamples.push(new Float32Array(channelData));

    // VAD: compute RMS energy of this buffer
    let sumSq = 0;
    for (let i = 0; i < channelData.length; i++) sumSq += channelData[i] ** 2;
    const rms = Math.sqrt(sumSq / channelData.length);
    const now = Date.now();

    if (rms > VAD_SPEECH_THRESHOLD) {
      // Speech energy detected
      if (!vadHasSpeech) { vadHasSpeech = true; vadSpeechStart = now; }
      vadSilenceStart = null;  // reset silence clock
      setVoiceState('recording', 'Listening…', 'Voice detected — keep speaking…');
    } else if (vadHasSpeech) {
      // Silence after speech
      if (!vadSilenceStart) vadSilenceStart = now;
      const speechDuration  = now - vadSpeechStart;
      const silenceDuration = now - vadSilenceStart;
      if (speechDuration >= VAD_MIN_SPEECH_MS && silenceDuration >= VAD_SILENCE_MS) {
        stopRecording();   // ← auto-stop
      } else if (speechDuration >= VAD_MIN_SPEECH_MS) {
        setVoiceState('recording', 'Listening…', 'Silence detected — finishing…');
      }
    }
  };

  sourceNode.connect(scriptNode);
  scriptNode.connect(audioCtx.destination);

  isRecording = true;
  setMicBtnRecording(true);
  
  setStatus('Recording… speak now (auto-stops on silence).', false);
  setVoiceState('recording', 'Listening…', 'Waiting for voice…');
}

async function stopRecording() {
  if (!isRecording) return;
  isRecording = false;

  // Reset VAD state
  vadHasSpeech = false; vadSpeechStart = null; vadSilenceStart = null;
  // Disconnect audio graph
  if (scriptNode)  { scriptNode.disconnect(); scriptNode.onaudioprocess = null; }
  if (sourceNode)  { sourceNode.disconnect(); }
  if (mediaStream) { mediaStream.getTracks().forEach(t => t.stop()); }

  setMicBtnRecording(false);
  
  setStatus('Processing audio…', false);
  setVoiceState('processing', 'Thinking...', 'Analyzing speech wave...');

  if (pcmSamples.length === 0) {
    setStatus('No audio captured.', false);
    setVoiceState('standby', 'System Standby', 'No audio captured.');
    return;
  }

  try {
    // Merge all chunks into a single Float32 array
    const totalSamples = pcmSamples.reduce((s, a) => s + a.length, 0);
    const merged = new Float32Array(totalSamples);
    let offset = 0;
    for (const chunk of pcmSamples) {
      merged.set(chunk, offset);
      offset += chunk.length;
    }
    pcmSamples = [];

    // Resample to 16 000 Hz using OfflineAudioContext
    const nativeSR    = audioCtx.sampleRate;
    const targetSR    = 16000;
    const targetLen   = Math.round(merged.length * targetSR / nativeSR);
    const offlineCtx  = new OfflineAudioContext(1, targetLen, targetSR);
    const buffer      = offlineCtx.createBuffer(1, merged.length, nativeSR);
    buffer.copyToChannel(merged, 0);
    const src = offlineCtx.createBufferSource();
    src.buffer = buffer;
    src.connect(offlineCtx.destination);
    src.start();
    const rendered = await offlineCtx.startRendering();
    const pcm16k   = rendered.getChannelData(0);

    // Build WAV file in-browser
    const wavBytes = encodeWav(pcm16k, targetSR);

    // Send to backend as base64
    const b64 = arrayBufferToBase64(wavBytes.buffer);
    wsSend({ type: 'audio_chunk', data: b64 });
    wsSend({ type: 'audio_end' });
    setStatus('Audio sent — waiting for response…', false);
    setVoiceState('processing', 'Thinking...', 'Waiting for transcriber results...');

    await audioCtx.close();
  } catch (e) {
    setStatus(`Audio processing error: ${e.message}`, true);
    statusBar.className = 'error';
    setVoiceState('standby', 'System Standby', `Error: ${e.message}`);
    console.error(e);
  }
}

// ── Text input ───────────────────────────────────────────────────────────────

sendBtn.addEventListener('click', sendText);
textInput.addEventListener('keydown', (e) => { if (e.key === 'Enter') sendText(); });

function sendText() {
  const text = textInput.value.trim();
  if (!text) return;
  textInput.value = '';
  // Optimistically show user bubble before transcript echo arrives
  pendingUserBubble = addUserBubble(text, null);  // lang unknown until server replies
  showTypingIndicator();
  wsSend({ type: 'text_input', text });
  setStatus(`Sent: "${text}"`, false);
  setVoiceState('processing', 'Thinking...', 'Analyzing text inputs...');
}

// ── Scenario buttons ─────────────────────────────────────────────────────────

document.querySelectorAll('.scenario-turn-btn').forEach(btn => {
  btn.addEventListener('click', () => {
    const text = btn.dataset.text;
    if (!text) return;
    textInput.value = text;
    sendText();
    
    // Trigger ripple animation
    btn.style.animation = 'inject-flash 0.8s ease-out';
    setTimeout(() => { btn.style.animation = ''; }, 800);
  });
});

// ── Audio playback (Piper WAV) ────────────────────────────────────────────────

async function playWavBase64(b64) {
  setVoiceState('processing', 'Speaking...', 'Playing synthesized audio response');
  const binary  = atob(b64);
  const bytes   = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);

  const ctx    = new (window.AudioContext || window.webkitAudioContext)();
  const decoded = await ctx.decodeAudioData(bytes.buffer);
  const src    = ctx.createBufferSource();
  src.buffer   = decoded;
  src.connect(ctx.destination);
  src.start();
  src.onended  = () => {
    ctx.close();
    setVoiceState('standby', 'System Standby', 'Ready for next query');
  };
}

// ── Browser TTS fallback ──────────────────────────────────────────────────────

/**
 * Normalize text before handing it to speechSynthesis.
 */
function normalizeSpeechText(text, language) {
  let t = text;

  // Currency symbols before numbers  →  spoken word after
  t = t.replace(/₹\s*([\d,]+)/g,  '$1 rupees');
  t = t.replace(/\$\s*([\d,]+)/g, '$1 dollars');
  t = t.replace(/€\s*([\d,]+)/g,  '$1 euros');

  // Temperature  →  "32 degrees"
  t = t.replace(/([\d.]+)\s*°C/gi, '$1 degrees Celsius');
  t = t.replace(/([\d.]+)\s*°F/gi, '$1 degrees Fahrenheit');

  // Per-night / por noche constructs
  t = t.replace(/([\d,]+)\s*\/\s*night/gi,  '$1 per night');
  t = t.replace(/([\d,]+)\s*\/\s*noche/gi,  '$1 por noche');

  // Percentages
  t = t.replace(/([\d.]+)\s*%/g, '$1 percent');

  // URLs — don't spell them out
  t = t.replace(/https?:\/\/\S+/g, 'link');

  // Commas inside large numbers (1,000 → 1000) so TTS reads naturally
  t = t.replace(/(\d),(\d{3})/g, '$1$2');

  // Bullet / dash list markers that produce odd pauses
  t = t.replace(/^\s*[-–•]\s*/gm, '');

  return t.trim();
}

function speakBrowser(text, language) {
  if (!window.speechSynthesis) return;
  // No cancel() here — streaming sends one sentence at a time and they must queue.
  // Cancellation happens at turn-start in the 'transcript' handler instead.

  const langMap = { en: 'en-US', hi: 'hi-IN', es: 'es-ES' };
  const utter   = new SpeechSynthesisUtterance(normalizeSpeechText(text, language));
  utter.lang    = langMap[language] || 'en-US';
  utter.rate    = 1.0;
  utter.pitch   = 1.0;

  // Try to pick a matching voice if available
  const voices  = window.speechSynthesis.getVoices();
  const match   = voices.find(v => v.lang.startsWith(utter.lang.slice(0, 5)));
  if (match) utter.voice = match;

  utter.onstart = () => {
    isSpeaking = true;
    setVoiceState('processing', 'Speaking...', 'Agent speaking — click mic or speak to interrupt');
    startBargeinMonitor();  // listen for user interruption
  };
  utter.onend = () => {
    // Streaming queues multiple utterances — only go idle when all are done
    setTimeout(() => {
      if (!window.speechSynthesis.speaking) {
        isSpeaking = false;
        stopBargeinMonitor();
        setVoiceState('standby', 'System Standby', 'Ready for next query');
      }
    }, 50);
  };
  utter.onerror = () => {
    isSpeaking = false;
    stopBargeinMonitor();
    setVoiceState('standby', 'System Standby', 'Ready for next query');
  };

  window.speechSynthesis.speak(utter);
}

// ── Barge-in monitor (continuous mic energy watch during agent speech) ────────

async function startBargeinMonitor() {
  if (bargeinStream || isRecording) return;  // already running or user is recording
  try {
    bargeinStream = await navigator.mediaDevices.getUserMedia({ audio: true, video: false });
    bargeinCtx    = new (window.AudioContext || window.webkitAudioContext)();
    bargeinSrc    = bargeinCtx.createMediaStreamSource(bargeinStream);
    bargeinNode   = bargeinCtx.createScriptProcessor(2048, 1, 1);
    bargeinNode.onaudioprocess = (e) => {
      if (!isSpeaking || isRecording) { stopBargeinMonitor(); return; }
      const data = e.inputBuffer.getChannelData(0);
      let sumSq = 0;
      for (let i = 0; i < data.length; i++) sumSq += data[i] ** 2;
      if (Math.sqrt(sumSq / data.length) > BARGE_THRESHOLD) {
        isSpeaking = false;  // prevent re-trigger
        setTimeout(() => {
          stopBargeinMonitor();
          if (window.speechSynthesis) window.speechSynthesis.cancel();
          startRecording();
        }, 0);
      }
    };
    bargeinSrc.connect(bargeinNode);
    bargeinNode.connect(bargeinCtx.destination);
  } catch {
    // Mic unavailable — barge-in silently disabled
    bargeinStream = null;
  }
}

function stopBargeinMonitor() {
  if (bargeinNode)   { try { bargeinNode.disconnect(); } catch {} bargeinNode.onaudioprocess = null; bargeinNode = null; }
  if (bargeinSrc)    { try { bargeinSrc.disconnect();  } catch {} bargeinSrc  = null; }
  if (bargeinStream) { bargeinStream.getTracks().forEach(t => t.stop()); bargeinStream = null; }
  if (bargeinCtx)    { bargeinCtx.close().catch(() => {}); bargeinCtx = null; }
}

// ── Chat bubble helpers ───────────────────────────────────────────────────────

const LANG_LABELS = { en: 'EN', hi: 'HI', es: 'ES' };

function updateLangBadge(lang) {
  const label = LANG_LABELS[lang] || lang?.toUpperCase() || '?';
  langBadge.className = `badge ${lang || 'en'}`;
  const textEl = langBadge.querySelector('.badge-text');
  if (textEl) textEl.textContent = label;
  else langBadge.textContent = label;
}

function setStatus(msg, isError) {
  statusBar.textContent = msg;
  statusBar.className   = isError ? 'error' : '';
}

function buildBubble(text, lang, role) {
  const empty = chatThread.querySelector('.chat-empty');
  if (empty) empty.remove();

  const langKey   = lang || 'en';
  const langLabel = LANG_LABELS[langKey] || langKey.toUpperCase();
  const isUser    = role === 'user';

  const row = document.createElement('div');
  row.className = `msg-row ${role}`;
  row.innerHTML = `
    <div class="msg-avatar">
      ${isUser ? `
        <svg class="msg-avatar-svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5">
          <path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"></path>
          <circle cx="12" cy="7" r="4"></circle>
        </svg>
      ` : `
        <svg class="msg-avatar-svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2">
          <path d="M12 2a3 3 0 0 0-3 3v7a3 3 0 0 0 6 0V5a3 3 0 0 0-3-3Z"></path>
          <path d="M19 10v1a7 7 0 0 1-14 0v-1"></path>
          <line x1="12" y1="19" x2="12" y2="22"></line>
        </svg>
      `}
    </div>
    <div class="msg-bubble">
      <div class="msg-meta">
        <span>${isUser ? 'You' : 'Agent'}</span>
        ${lang ? `<span class="msg-lang ${langKey}">${langLabel}</span>` : ''}
      </div>
      <div class="msg-text">${escapeHtml(text)}</div>
    </div>
  `;
  chatThread.appendChild(row);
  chatThread.scrollTop = chatThread.scrollHeight;
  return row;
}

function addUserBubble(text, lang) {
  if (pendingUserBubble) {
    // Patch in lang badge now that server has confirmed the language
    if (lang) {
      const meta = pendingUserBubble.querySelector('.msg-meta');
      if (meta && !meta.querySelector('.msg-lang')) {
        const langKey   = lang;
        const langLabel = LANG_LABELS[langKey] || langKey.toUpperCase();
        const badge = document.createElement('span');
        badge.className = `msg-lang ${langKey}`;
        badge.textContent = langLabel;
        meta.appendChild(badge);
      }
    }
    const b = pendingUserBubble;
    pendingUserBubble = null;
    return b;
  }
  return buildBubble(text, lang, 'user');
}

function addAgentBubble(text, lang) {
  return buildBubble(text, lang, 'agent');
}

// ── Typing indicator ──────────────────────────────────────────────────────────
let _typingRow = null;

function showTypingIndicator() {
  if (_typingRow) return;
  const empty = chatThread.querySelector('.chat-empty');
  if (empty) empty.remove();

  _typingRow = document.createElement('div');
  _typingRow.className = 'msg-row agent typing-indicator';
  _typingRow.innerHTML = `
    <div class="msg-avatar">
      <svg class="msg-avatar-svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2">
        <path d="M12 2a3 3 0 0 0-3 3v7a3 3 0 0 0 6 0V5a3 3 0 0 0-3-3Z"></path>
        <path d="M19 10v1a7 7 0 0 1-14 0v-1"></path>
        <line x1="12" y1="19" x2="12" y2="22"></line>
      </svg>
    </div>
    <div class="msg-bubble">
      <div class="msg-meta"><span>Agent</span></div>
      <div class="msg-text">
        <span class="typing-dot"></span>
        <span class="typing-dot"></span>
        <span class="typing-dot"></span>
      </div>
    </div>
  `;
  chatThread.appendChild(_typingRow);
  chatThread.scrollTop = chatThread.scrollHeight;
}

function removeTypingIndicator() {
  if (_typingRow) { _typingRow.remove(); _typingRow = null; }
}

// ── Tab Navigation Controllers ───────────────────────────────────────────────
window.switchMemoryTab = function(tabName) {
  const cardsBtn = document.getElementById('tab-btn-cards');
  const jsonBtn = document.getElementById('tab-btn-json');
  const cardsContent = document.getElementById('mem-tab-cards');
  const jsonContent = document.getElementById('mem-tab-json');
  
  if (tabName === 'cards') {
    cardsBtn.classList.add('active');
    jsonBtn.classList.remove('active');
    cardsContent.classList.add('active');
    jsonContent.classList.remove('active');
  } else {
    cardsBtn.classList.remove('active');
    jsonBtn.classList.add('active');
    cardsContent.classList.remove('active');
    jsonContent.classList.add('active');
  }
};

window.switchScenarioTab = function(scenarioNum) {
  // Update scenario tabs
  const buttons = document.querySelectorAll('.scenario-tab-btn');
  buttons.forEach((btn, index) => {
    if (index + 1 === scenarioNum) {
      btn.classList.add('active');
    } else {
      btn.classList.remove('active');
    }
  });

  // Update scenario group contents
  const groups = document.querySelectorAll('.scenario-group');
  groups.forEach((grp) => {
    if (grp.id === `scenario-grp-${scenarioNum}`) {
      grp.classList.add('active');
    } else {
      grp.classList.remove('active');
    }
  });
};

// ── Dynamic Memory Card Rendering ───────────────────────────────────────────
function renderMemoryCards(structured) {
  const container = document.getElementById('memory-cards-container');
  if (!container) return;
  container.innerHTML = '';

  let hasData = false;

  // 1. Order Tracking Card
  if (structured.order_id || structured.email || structured.order_status) {
    hasData = true;
    const card = document.createElement('div');
    card.className = 'mem-card';
    
    let fields = '';
    if (structured.order_id) fields += `<div class="mem-label">Order ID</div><div class="mem-val">${escapeHtml(structured.order_id)}</div>`;
    if (structured.email) fields += `<div class="mem-label">Email</div><div class="mem-val">${escapeHtml(structured.email)}</div>`;
    if (structured.order_status) fields += `<div class="mem-label">Status</div><div class="mem-val" style="color:var(--green)">${escapeHtml(structured.order_status)}</div>`;
    if (structured.estimated_delivery) fields += `<div class="mem-label">Delivery</div><div class="mem-val">${escapeHtml(structured.estimated_delivery)}</div>`;
    if (structured.tracking_link) fields += `<div class="mem-label">Tracking</div><div class="mem-val"><a href="${escapeHtml(structured.tracking_link)}" target="_blank">Track Shipment ↗</a></div>`;
    if (structured.refund_policy) fields += `<div class="mem-label">Refunds</div><div class="mem-val" style="color:var(--text-secondary);font-size:0.72rem">${escapeHtml(structured.refund_policy)}</div>`;

    card.innerHTML = `
      <div class="mem-card-title">
        <svg class="mem-card-icon order" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 16V8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16z"></path><polyline points="3.27 6.96 12 12.01 20.73 6.96"></polyline><line x1="12" y1="22.08" x2="12" y2="12"></line></svg>
        <span>Order Tracking</span>
      </div>
      <div class="mem-fields-grid">${fields}</div>
    `;
    container.appendChild(card);
  }

  // 2. Hotel Booking Card
  if (structured.hotel_city || structured.hotel_dates || structured.hotel_budget) {
    hasData = true;
    const card = document.createElement('div');
    card.className = 'mem-card';
    
    let fields = '';
    if (structured.hotel_city) fields += `<div class="mem-label">City</div><div class="mem-val">${escapeHtml(structured.hotel_city)}</div>`;
    if (structured.hotel_dates) fields += `<div class="mem-label">Dates</div><div class="mem-val">${escapeHtml(structured.hotel_dates)}</div>`;
    if (structured.hotel_people) fields += `<div class="mem-label">Guests</div><div class="mem-val">${escapeHtml(structured.hotel_people)}</div>`;
    if (structured.hotel_budget) fields += `<div class="mem-label">Budget</div><div class="mem-val">₹${escapeHtml(structured.hotel_budget)}/night</div>`;
    if (structured.selected_hotel_option) fields += `<div class="mem-label">Selection</div><div class="mem-val" style="color:var(--accent);font-weight:700;">${escapeHtml(structured.selected_hotel_option)}</div>`;
    
    if (structured.hotel_options && structured.hotel_options.length > 0) {
      let optItems = '';
      structured.hotel_options.forEach((opt, idx) => {
        const isSelected = structured.selected_hotel_option && opt.name && opt.name.toLowerCase().includes(structured.selected_hotel_option.toLowerCase());
        const style = isSelected ? 'color:var(--green);font-weight:700;' : '';
        const check = isSelected ? '✓ ' : '';
        optItems += `<div class="mem-hotel-item" style="${style}"><span>${check}${idx+1}. ${escapeHtml(opt.name || 'Option')}</span><span>₹${escapeHtml(opt.price)}/n</span></div>`;
      });
      fields += `<div class="mem-hotel-list">${optItems}</div>`;
    }

    card.innerHTML = `
      <div class="mem-card-title">
        <svg class="mem-card-icon hotel" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M3 9l9-7 9 7v11a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"></path><polyline points="9 22 9 12 15 12 15 22"></polyline></svg>
        <span>Hotel Booking</span>
      </div>
      <div class="mem-fields-grid">${fields}</div>
    `;
    container.appendChild(card);
  }

  // 3. Weather Card
  if (structured.weather_cities && structured.weather_cities.length > 0) {
    hasData = true;
    const card = document.createElement('div');
    card.className = 'mem-card';
    
    const weatherData = {
      mumbai: '32°C, Warm & Humid',
      delhi: '36°C, Hot & Dry',
      chennai: '33°C, Warm & Breezy'
    };
    
    let fields = '';
    structured.weather_cities.forEach(city => {
      const cleanCity = city.toLowerCase().trim();
      const info = weatherData[cleanCity] || 'Temp N/A';
      fields += `<div class="mem-label">${city}</div><div class="mem-val">${escapeHtml(info)}</div>`;
    });

    card.innerHTML = `
      <div class="mem-card-title">
        <svg class="mem-card-icon weather" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 1v2M12 21v2M4.22 4.22l1.42 1.42M18.36 18.36l1.42 1.42M1 12h2M21 12h2M4.22 19.78l1.42-1.42M18.36 5.64l1.42-1.42M12 17a5 5 0 1 0 0-10 5 5 0 0 0 0 10z"></path></svg>
        <span>Weather Info</span>
      </div>
      <div class="mem-fields-grid">${fields}</div>
    `;
    container.appendChild(card);
  }

  if (!hasData) {
    container.innerHTML = '<div class="memory-empty-state">No structured memory extracted yet. Try scenario tests.</div>';
  }
}

function renderMemory(mem) {
  const structured = mem?.structured || mem || {};
  // Filter out null/undefined/empty values for display
  const filtered = Object.fromEntries(
    Object.entries(structured).filter(([, v]) => v !== null && v !== undefined && v !== '' && !(Array.isArray(v) && v.length === 0))
  );
  memoryPre.textContent = JSON.stringify(filtered, null, 2);
  renderMemoryCards(structured);
}

// ── Telemetry Indicators Rendering ──────────────────────────────────────────
function updateLatencyMeters(data) {
  document.getElementById('lat-total-val').textContent = fmt(data.total_latency_ms) + (data.total_latency_ms != null ? ' ms' : '');
  document.getElementById('lat-stt-val').textContent = fmt(data.stt_latency_ms) + (data.stt_latency_ms != null ? ' ms' : '');
  document.getElementById('lat-llm-val').textContent = fmt(data.llm_total_ms) + (data.llm_total_ms != null ? ' ms' : '');
  document.getElementById('lat-tts-val').textContent = fmt(data.tts_latency_ms) + (data.tts_latency_ms != null ? ' ms' : '');

  const stt = data.stt_latency_ms || 0;
  const llm = data.llm_total_ms || 0;
  const tts = data.tts_latency_ms || 0;
  const max = Math.max(stt, llm, tts, 500); // Floor of 500ms for scale ratio

  document.getElementById('lat-stt-bar').style.width = (stt / max) * 100 + '%';
  document.getElementById('lat-llm-bar').style.width = (llm / max) * 100 + '%';
  document.getElementById('lat-tts-bar').style.width = (tts / max) * 100 + '%';
}

function renderLatency(data) {
  turnCounter++;
  latencyRows.push({ turn: turnCounter, data });

  // Keep only last 10 rows
  if (latencyRows.length > 10) latencyRows.shift();

  // Render modern summary and progress meters
  updateLatencyMeters(data);

  // Render history table
  latencyTbody.innerHTML = '';
  for (const row of latencyRows) {
    const tr = document.createElement('tr');
    const d  = row.data;
    tr.innerHTML = `
      <td>${row.turn}</td>
      <td class="num">${fmt(d.stt_latency_ms)}</td>
      <td class="num">${fmt(d.llm_total_ms)}</td>
      <td class="num">${fmt(d.tts_latency_ms)}</td>
      <td class="num total">${fmt(d.total_latency_ms)}</td>
    `;
    latencyTbody.appendChild(tr);
  }
}

function fmt(v) { return v != null ? `${v}` : '—'; }

function escapeHtml(str) {
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

// ── WAV encoder ───────────────────────────────────────────────────────────────

function encodeWav(float32, sampleRate) {
  const numSamples = float32.length;
  const buf        = new ArrayBuffer(44 + numSamples * 2);
  const view       = new DataView(buf);

  function writeStr(offset, str) {
    for (let i = 0; i < str.length; i++) view.setUint8(offset + i, str.charCodeAt(i));
  }

  writeStr(0, 'RIFF');
  view.setUint32(4,  36 + numSamples * 2, true);
  writeStr(8,  'WAVE');
  writeStr(12, 'fmt ');
  view.setUint32(16, 16, true);          // chunk size
  view.setUint16(20, 1,  true);          // PCM
  view.setUint16(22, 1,  true);          // mono
  view.setUint32(24, sampleRate,         true);
  view.setUint32(28, sampleRate * 2,     true);  // byte rate
  view.setUint16(32, 2,  true);          // block align
  view.setUint16(34, 16, true);          // bits per sample
  writeStr(36, 'data');
  view.setUint32(40, numSamples * 2, true);

  let offset = 44;
  for (let i = 0; i < numSamples; i++) {
    const s = Math.max(-1, Math.min(1, float32[i]));
    view.setInt16(offset, s < 0 ? s * 0x8000 : s * 0x7fff, true);
    offset += 2;
  }
  return new Uint8Array(buf);
}

function arrayBufferToBase64(buffer) {
  let binary = '';
  const bytes = new Uint8Array(buffer);
  for (let i = 0; i < bytes.byteLength; i++) binary += String.fromCharCode(bytes[i]);
  return btoa(binary);
}

// ── Boot ──────────────────────────────────────────────────────────────────────

// Pre-load voices (Chrome loads them async)
if (window.speechSynthesis) {
  window.speechSynthesis.getVoices();
  window.speechSynthesis.onvoiceschanged = () => window.speechSynthesis.getVoices();
}

connectWS();
setVoiceState('standby', 'System Standby', 'Awaiting connection to server...');
