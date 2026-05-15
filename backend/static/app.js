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

// ── DOM refs ────────────────────────────────────────────────────────────────
const micBtn        = document.getElementById('mic-btn');
const stopBtn       = document.getElementById('stop-btn');
const textInput     = document.getElementById('text-input');
const sendBtn       = document.getElementById('send-btn');
const connBadge     = document.getElementById('conn-badge');
const langBadge     = document.getElementById('lang-badge');
const ttsBadge      = document.getElementById('tts-badge');
const statusBar     = document.getElementById('status-bar');
const chatThread    = document.getElementById('chat-thread');
const memoryPre     = document.getElementById('memory-pre');
const latencyTbody  = document.getElementById('latency-tbody');

// ── WebSocket ────────────────────────────────────────────────────────────────

function connectWS() {
  const proto = location.protocol === 'https:' ? 'wss' : 'ws';
  const url   = `${proto}://${location.host}/ws/${SESSION_ID}`;
  ws = new WebSocket(url);

  ws.onopen = () => {
    setStatus('Connected', false);
    connBadge.textContent = '● Connected';
    connBadge.classList.remove('off');
    // Ping to keep alive
    setInterval(() => ws && ws.readyState === WebSocket.OPEN && ws.send(JSON.stringify({type:'ping'})), 20000);
  };

  ws.onclose = () => {
    setStatus('Disconnected — reconnecting in 3s…', true);
    connBadge.textContent = '● Disconnected';
    connBadge.classList.add('off');
    setTimeout(connectWS, 3000);
  };

  ws.onerror = (e) => {
    setStatus('WebSocket error', true);
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
      setStatus(`Transcribed [${msg.language?.toUpperCase()}]: "${msg.text}"`, false);
      addUserBubble(msg.text, msg.language || 'en');
      updateLangBadge(msg.language);
      showTypingIndicator();
      break;

    case 'agent_response':
      setStatus('Agent responded.', false);
      removeTypingIndicator();
      addAgentBubble(msg.text, msg.language || 'en');
      break;

    case 'audio_response':
      // Piper WAV audio from backend
      ttsBadge.textContent = 'TTS: Piper';
      ttsBadge.classList.add('piper');
      playWavBase64(msg.data).catch(console.error);
      break;

    case 'tts_browser':
      // Browser speechSynthesis fallback
      ttsBadge.textContent = 'TTS: browser';
      ttsBadge.classList.remove('piper');
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
      console.error('Backend error:', msg.message);
      break;
  }
}

// ── Microphone recording ──────────────────────────────────────────────────────

micBtn.addEventListener('click', startRecording);
stopBtn.addEventListener('click', stopRecording);

async function startRecording() {
  if (isRecording) return;
  try {
    mediaStream = await navigator.mediaDevices.getUserMedia({ audio: true, video: false });
  } catch (e) {
    setStatus(`Mic access denied: ${e.message}`, true);
    return;
  }

  // Use AudioContext at the native sample rate; we resample to 16 kHz on stop.
  audioCtx   = new (window.AudioContext || window.webkitAudioContext)();
  sourceNode = audioCtx.createMediaStreamSource(mediaStream);

  // ScriptProcessorNode: 4096-sample buffer, 1 input channel, 1 output channel.
  // Deprecated but universally supported; fine for a demo.
  scriptNode = audioCtx.createScriptProcessor(4096, 1, 1);
  pcmSamples = [];

  scriptNode.onaudioprocess = (e) => {
    const channelData = e.inputBuffer.getChannelData(0);
    pcmSamples.push(new Float32Array(channelData));
  };

  sourceNode.connect(scriptNode);
  scriptNode.connect(audioCtx.destination);

  isRecording = true;
  micBtn.textContent = '🔴 Recording…';
  micBtn.classList.add('recording');
  micBtn.disabled = true;
  stopBtn.disabled = false;
  setStatus('Recording… press Stop when done.', false);
}

async function stopRecording() {
  if (!isRecording) return;
  isRecording = false;

  // Disconnect audio graph
  if (scriptNode)  { scriptNode.disconnect(); scriptNode.onaudioprocess = null; }
  if (sourceNode)  { sourceNode.disconnect(); }
  if (mediaStream) { mediaStream.getTracks().forEach(t => t.stop()); }

  micBtn.textContent = '🎤 Record';
  micBtn.classList.remove('recording');
  micBtn.disabled = false;
  stopBtn.disabled = true;
  setStatus('Processing audio…', false);

  if (pcmSamples.length === 0) {
    setStatus('No audio captured.', false);
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

    await audioCtx.close();
  } catch (e) {
    setStatus(`Audio processing error: ${e.message}`, true);
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
}

// ── Scenario buttons ─────────────────────────────────────────────────────────

document.querySelectorAll('.scenario-turn-btn').forEach(btn => {
  btn.addEventListener('click', () => {
    const text = btn.dataset.text;
    if (!text) return;
    textInput.value = text;
    sendText();
    // Briefly highlight the clicked button
    btn.style.background = 'var(--accent)';
    btn.style.color = '#fff';
    setTimeout(() => { btn.style.background = ''; btn.style.color = ''; }, 600);
  });
});

// ── Audio playback (Piper WAV) ────────────────────────────────────────────────

async function playWavBase64(b64) {
  const binary  = atob(b64);
  const bytes   = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);

  const ctx    = new (window.AudioContext || window.webkitAudioContext)();
  const decoded = await ctx.decodeAudioData(bytes.buffer);
  const src    = ctx.createBufferSource();
  src.buffer   = decoded;
  src.connect(ctx.destination);
  src.start();
  src.onended  = () => ctx.close();
}

// ── Browser TTS fallback ──────────────────────────────────────────────────────

/**
 * Normalize text before handing it to speechSynthesis.
 * Special characters adjacent to digits (₹, °, /) break number recognition
 * in many browser TTS engines, causing "5000" to be read as "five zero zero zero".
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
  window.speechSynthesis.cancel();   // stop any previous utterance

  const langMap = { en: 'en-US', hi: 'hi-IN', es: 'es-ES' };
  const utter   = new SpeechSynthesisUtterance(normalizeSpeechText(text, language));
  utter.lang    = langMap[language] || 'en-US';
  utter.rate    = 1.0;
  utter.pitch   = 1.0;

  // Try to pick a matching voice if available
  const voices  = window.speechSynthesis.getVoices();
  const match   = voices.find(v => v.lang.startsWith(utter.lang.slice(0, 5)));
  if (match) utter.voice = match;

  window.speechSynthesis.speak(utter);
}

// ── Chat bubble helpers ───────────────────────────────────────────────────────

const LANG_LABELS = { en: 'EN', hi: 'HI', es: 'ES' };

function updateLangBadge(lang) {
  const label = LANG_LABELS[lang] || lang?.toUpperCase() || '?';
  langBadge.textContent = label;
  langBadge.className   = `badge ${lang || 'en'}`;
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
    <div class="msg-avatar">${isUser ? '🧑' : '🤖'}</div>
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
    <div class="msg-avatar">🤖</div>
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

function renderMemory(mem) {
  const structured = mem?.structured || mem || {};
  // Filter out null/undefined/empty values for display
  const filtered = Object.fromEntries(
    Object.entries(structured).filter(([, v]) => v !== null && v !== undefined && v !== '' && !(Array.isArray(v) && v.length === 0))
  );
  memoryPre.textContent = JSON.stringify(filtered, null, 2);
}

function renderLatency(data) {
  turnCounter++;
  latencyRows.push({ turn: turnCounter, data });

  // Keep only last 10 rows
  if (latencyRows.length > 10) latencyRows.shift();

  latencyTbody.innerHTML = '';
  for (const row of latencyRows) {
    const tr = document.createElement('tr');
    const d  = row.data;
    tr.innerHTML = `
      <td>${row.turn}</td>
      <td class="num">${fmt(d.stt_latency_ms)}</td>
      <td class="num">${fmt(d.llm_total_ms)}</td>
      <td class="num">${fmt(d.tts_latency_ms)}</td>
      <td class="num">${fmt(d.total_latency_ms)}</td>
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
