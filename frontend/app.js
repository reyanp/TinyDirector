// Poyo companion window. Blender's viewport is the stage; this window is Poyo, a push-to-talk microphone,
// subtitles and a status pill. Everything shown comes from the tutor server: /api/state and the /api/events stream
// of real execution events (director/events.py). Poyo's mood follows those events and its own voice playback,
// never timers that pretend work is happening.
//
// Voice turn: record (MediaRecorder) -> POST /api/voice (speech-to-text + the normal Nemotron turn) -> the turn's
// verified reply is spoken (POST /api/tts) while its cues (director/stage.py) point Blender's camera at the part each
// sentence is about (POST /api/direct). The Type tab runs the same turn from text (debugging fallback).
"use strict";

const $ = (id) => document.getElementById(id);
const els = {
  poyo: $("poyo"), subtitle: $("subtitle"), heard: $("heard"), mic: $("mic"), micHint: $("mic-hint"),
  status: $("status"), statusText: $("status-text"), live: $("live-dot"), modes: $("modes"), mute: $("mute"),
  onstage: $("onstage"), title: $("lesson-title"), meta: $("lesson-meta"), drawer: $("drawer"),
  chat: $("chat"), input: $("input"), send: $("send"), form: $("composer"), reset: $("reset"),
  suggestions: $("suggestions"), events: $("events"), pipeline: $("pipeline"),
  kmap: $("kmap"), kmapCard: $("kmap-card"), kmapNote: $("kmap-note"),
};

const STAGE_LABEL = {
  understand: "understand", scene: "scene", plan: "plan", calculate: "calculate", director: "director",
  builder: "builder", blender: "blender", verify: "verify", load: "load", apply: "blender", explain: "explain",
  turn: "done", knowledge: "knowledge", listen: "listen", direct: "camera",
};
const PIPELINE = [
  ["listen", ["listen"]], ["understand", ["understand", "scene"]], ["recall", ["knowledge"]], ["plan", ["plan"]],
  ["calculate", ["calculate"]], ["build", ["director", "builder", "blender", "load", "apply"]], ["verify", ["verify"]],
  ["explain", ["explain"]],
];
const SUGGEST_START = ["Teach me why LEDs need resistors.", "How does a voltage divider work?",
                       "Teach me how to recognize scam texts.", "Override: how do SIM swaps work?"];
const SUGGEST_TOPIC = ["Explain that in simpler terms.", "Teach me why LEDs need resistors."];
const SUGGEST_CIRCUIT = ["What if I make the resistor 1,000 ohms?", "Why did it get dimmer?", "Increase the resistance.",
                         "Show me the resistor.", "Show me the current.", "Explain that in simpler terms."];
const SUGGEST_SCAM = ["What makes that message suspicious?", "What if I follow the link?", "What should I do instead?",
                      "What if I already typed my password?"];
// OVERRIDE (director/explainer/): a lesson Nemotron generates for any question, labeled "not verified"
const SUGGEST_EXPLAINER = ["Explain that in simpler terms.", "What's the most important part?",
                           "Override: how do vaccines work?", "Teach me why LEDs need resistors."];
const IDLE_HINT = "Hi! Ask me how a circuit works, or say “override” and ask me anything.";
function idleHint() {
  const kind = state.lesson && (state.lesson.info || {}).kind;
  if (kind === "circuit") return "Try: “What if I make the resistor 1,000 ohms?” or “Why?”";
  if (kind === "scam") return "Try: “What makes that message suspicious?”";
  if (kind === "explainer") return "Ask about this lesson, or say “override” + a new question.";
  return state.lesson ? "Ask me about the lesson on stage." : IDLE_HINT;
}

let state = { mode: {}, busy: false, turn: 0, messages: [], lesson: null, last_event_id: 0,
              knowledge: { active: [], discussed: [] } };
let renderedMessages = 0;
let openRows = new Map();
let turnStart = new Map();
let lastTurnShown = 0;
let mood = "idle";
let moodTimer = null;
let muted = false;
try { muted = localStorage.getItem("poyo-muted") === "1"; } catch (e) { /* storage blocked: default on */ }

// ---------------- Poyo ----------------
function setMood(next, holdMs) {
  clearTimeout(moodTimer);
  mood = next;
  els.poyo.className = `poyo ${next}`;
  if (holdMs) moodTimer = setTimeout(() => setMood(rec ? "listening" : state.busy ? "thinking" : "idle"), holdMs);
}
let subtitleTimer = null;
function subtitle(text, opts = {}) {
  clearTimeout(subtitleTimer);
  els.subtitle.textContent = text || "";
  els.subtitle.classList.toggle("dim", Boolean(opts.dim));
  if (opts.clearAfter) subtitleTimer = setTimeout(() => subtitle(""), opts.clearAfter);
}
function heard(text) {
  if (!text) { els.heard.replaceChildren(); return; }
  const q = document.createElement("q");
  q.textContent = text;
  els.heard.replaceChildren(document.createTextNode("You: "), q);
}
function status(text, kind) {
  els.statusText.textContent = text;
  els.status.classList.toggle("working", kind === "working");
  els.status.classList.toggle("error", kind === "error");
}
async function loadPoyo() {
  if (els.poyo.querySelector("svg")) return;  // inlined by the server
  try { els.poyo.innerHTML = await (await fetch("/poyo.svg")).text(); } catch (e) { /* works without the mascot */ }
}

// ---------------- stage summary (title + calculated chips) ----------------
function badge(text, cls) {
  const b = document.createElement("span");
  b.className = `badge ${cls}`;
  b.textContent = text;
  return b;
}
function renderModes() {
  // MOCK / SIMULATED are always visible; live modes are listed in the Activity tab only (keep the window calm)
  const m = state.mode || {};
  const out = [];
  if (!m.live && m.model) out.push(badge("MOCK model", "mock"));
  if (m.simulated) out.push(badge("SIMULATED engine", "mock"));
  const v = m.voice || {};
  if (v.provider && /MOCK/.test(v.provider)) out.push(badge("MOCK voice", "mock"));
  els.modes.replaceChildren(...out);
}
let lastChips = [];
function renderLesson() {
  const l = state.lesson;
  els.onstage.classList.toggle("empty", !l);
  if (!l) { els.title.textContent = "Nothing yet"; els.meta.replaceChildren(); lastChips = []; return; }
  els.title.textContent = l.title;
  const info = l.info || {};
  const chip = (t, cls = "") => { const c = document.createElement("span"); c.className = `chip ${cls}`; c.textContent = t; return c; };
  let chips = [];
  if (info.kind === "circuit") chips = (info.chips || []).slice(0, 2);   // formatted by the calculation module
  if (info.kind === "scam") chips = (info.chips || []).slice(0, 3);      // FICTIONAL label, heuristic result, choice
  if (info.kind === "explainer") chips = (info.chips || []).slice(0, 2);  // "Generated (OVERRIDE)", "not verified"
  const nodes = chips.map((t, i) => chip(t, info.kind === "explainer" ? "unverified"
    : lastChips.length && lastChips[i] !== t ? "changed" : ""));
  if (info.kind === "topic") nodes.push(chip(`built by ${info.builder}`, "neutral"));
  els.meta.replaceChildren(...nodes);
  if (nodes.some((n) => n.classList.contains("changed"))) setTimeout(() => nodes.forEach((n) => n.classList.remove("changed")), 2500);
  lastChips = chips;
}

// ---------------- drawer ----------------
function setDrawer(open) {
  els.drawer.hidden = !open;
  els.status.setAttribute("aria-expanded", String(open));
}
function showTab(name) {
  for (const b of els.drawer.querySelectorAll("[role=tab]")) b.setAttribute("aria-selected", String(b.dataset.tab === name));
  for (const p of els.drawer.querySelectorAll(".tab-panel")) p.hidden = p.id !== `tab-${name}`;
  if (name === "type") els.input.focus();
  if (name === "chat") els.chat.parentElement.scrollTop = 1e9;
}
els.status.addEventListener("click", () => setDrawer(els.drawer.hidden));
for (const b of els.drawer.querySelectorAll("[role=tab]")) b.addEventListener("click", () => showTab(b.dataset.tab));
document.addEventListener("keydown", (e) => { if (e.key === "Escape" && !els.drawer.hidden) setDrawer(false); });

// ---------------- conversation (drawer) ----------------
function messageEl(m) {
  const d = document.createElement("div");
  d.className = `msg ${m.role}${m.role === "tutor" && m.ok === false ? " failed" : ""}`;
  const who = document.createElement("span");
  who.className = "who";
  who.textContent = m.role === "tutor" ? "Poyo" : m.via === "voice" ? "you (voice)" : "you (typed)";
  const tagText = { mock: "MOCK text", template: "template (model text failed the number check)", error: "error" }[m.source];
  if (m.role === "tutor" && tagText) {
    const t = document.createElement("span");
    t.className = "tag";
    t.textContent = tagText;
    who.appendChild(t);
  }
  const body = document.createElement("span");
  body.className = "body";
  body.textContent = m.text;
  d.append(who, body);
  const g = m.role === "tutor" && m.grounding ? groundingEl(m.grounding) : null;
  if (g) d.appendChild(g);
  return d;
}
// What backs an answer. Retrieval is not verification: only "verified" facts were re-computed by a calculator,
// and only numbers are machine-checked; the wording is grounded in the listed facts, not proven.
function groundingEl(g) {
  const facts = g.facts || [];
  if (!facts.length && g.numbers === "none" && !g.generated) return null;
  const det = document.createElement("details");
  det.className = "grounding";
  const sum = document.createElement("summary");
  const num = document.createElement("span");
  num.className = g.numbers === "calculated" ? "calc" : g.numbers === "cited" ? "cited"
    : g.generated ? "unverified" : "";
  num.textContent = { calculated: "✓ numbers checked against the calculation",
                      cited: "numbers from cited sources (not calculated)", none: "no numbers to check",
                      scenario: "numbers only from the lesson's fictional message and guidance",
                      unverified: "generated lesson (OVERRIDE): content not verified" }[g.numbers]
    || (g.generated ? "generated lesson (OVERRIDE): content not verified" : "");
  sum.append(num, document.createTextNode(facts.length ? ` · ${facts.length} knowledge-base fact${facts.length > 1 ? "s" : ""}` : ""));
  det.appendChild(sum);
  const ul = document.createElement("ul");
  for (const f of facts) {
    const li = document.createElement("li");
    const st = document.createElement("span");
    st.className = `st ${f.status}`;
    st.textContent = f.status;
    st.title = { verified: "re-computed by a calculator when the knowledge base was built",
                 sourced: "cited to a source, not machine-checked", assumption: "a simplification this tutor's model makes" }[f.status] || "";
    li.append(st, document.createTextNode(f.text + " "));
    const src = f.source || {};
    if (src.url && /^https:\/\//.test(src.url)) {
      const a = document.createElement("a");
      a.href = src.url; a.target = "_blank"; a.rel = "noopener noreferrer"; a.textContent = `(${src.title.split(":")[0]})`;
      a.title = src.title;
      li.appendChild(a);
    } else if (src.title) {
      const t = document.createElement("span");
      t.textContent = "(tutor's own model)";
      t.title = src.title;
      li.appendChild(t);
    }
    ul.appendChild(li);
  }
  if (facts.length) det.appendChild(ul);
  const note = document.createElement("p");
  note.className = "note";
  note.textContent = "Retrieved for this answer. Only numbers are machine-checked; the wording is grounded in these facts, not proven."
    + (g.misconception_check ? " Checked against known misconceptions." : "");
  det.appendChild(note);
  return det;
}
function renderMessages() {
  if (!state.messages.length) {
    const e = document.createElement("p");
    e.className = "empty";
    e.textContent = "No conversation yet.";
    els.chat.replaceChildren(e);
    renderedMessages = 0;
    return;
  }
  if (renderedMessages === 0 || renderedMessages > state.messages.length) { els.chat.replaceChildren(); renderedMessages = 0; }
  for (const m of state.messages.slice(renderedMessages)) els.chat.appendChild(messageEl(m));
  renderedMessages = state.messages.length;
  els.chat.parentElement.scrollTop = 1e9;
}
function renderSuggestions() {
  const kind = state.lesson && (state.lesson.info || {}).kind;
  const list = kind === "circuit" ? SUGGEST_CIRCUIT : kind === "topic" ? SUGGEST_TOPIC : kind === "scam" ? SUGGEST_SCAM
    : kind === "explainer" ? SUGGEST_EXPLAINER : SUGGEST_START;
  els.suggestions.replaceChildren(...list.map((t) => {
    const b = document.createElement("button");
    b.type = "button";
    b.textContent = t;
    b.disabled = state.busy;
    b.addEventListener("click", () => sendText(t));
    return b;
  }));
}
function setBusy(busy) {
  state.busy = busy;
  els.send.disabled = busy;
  els.input.disabled = busy;
  for (const b of els.suggestions.querySelectorAll("button")) b.disabled = busy;
  els.mic.disabled = busy && !rec;
  els.micHint.replaceChildren(...(busy ? [document.createTextNode("Poyo is working on it…")]
    : [document.createTextNode("Click or hold "), Object.assign(document.createElement("kbd"), { textContent: "Space" }),
       document.createTextNode(" and ask Poyo")]));
  if (!busy && !rec) status("Ready", "");
}

// ---------------- knowledge map (drawer, from the SQLite knowledge base) ----------------
const SVG_NS = "http://www.w3.org/2000/svg";
const DEMO_TEXT = { interactive: "live 3D demo", lesson: "3D lesson template", explanation_only: "explained only (no 3D demo yet)" };
const nodeEls = new Map();
const edgeEls = [];
let graphNames = new Map();
function svgEl(tag, attrs) {
  const e = document.createElementNS(SVG_NS, tag);
  for (const [k, v] of Object.entries(attrs || {})) e.setAttribute(k, v);
  return e;
}
function renderGraph(g) {
  nodeEls.clear(); edgeEls.length = 0;
  if (!g || !g.nodes || !g.nodes.length) { els.kmapNote.textContent = "Knowledge base unavailable."; return; }
  const W = 360, H = 232, NW = 78, NH = 22, PX = 42, PY = 16;
  const pos = new Map(g.nodes.map((n) => [n.id, [PX + n.x * (W - 2 * PX), PY + n.y * (H - 2 * PY)]]));
  graphNames = new Map(g.nodes.map((n) => [n.id, n.name]));
  const edges = svgEl("g");
  for (const e of g.edges) {
    const [x1, y1] = pos.get(e.dst), [x2, y2] = pos.get(e.src);
    let d;
    if (Math.abs(x1 - x2) < 1) {
      const x = x1 + NW / 2;
      d = `M${x} ${y1} C${x + 24} ${y1} ${x + 24} ${y2} ${x} ${y2}`;
    } else {
      const [a, b] = x1 < x2 ? [[x1 + NW / 2, y1], [x2 - NW / 2, y2]] : [[x1 - NW / 2, y1], [x2 + NW / 2, y2]];
      const mx = (a[0] + b[0]) / 2;
      d = `M${a[0]} ${a[1]} C${mx} ${a[1]} ${mx} ${b[1]} ${b[0]} ${b[1]}`;
    }
    const path = svgEl("path", { d, class: `edge ${e.rel}` });
    const t = svgEl("title");
    t.textContent = `${graphNames.get(e.src)} ${e.rel.replace("_", " ")} ${graphNames.get(e.dst)}${e.note ? ` (${e.note})` : ""}`;
    path.appendChild(t);
    edges.appendChild(path);
    edgeEls.push([path, e.src, e.dst]);
  }
  const nodes = svgEl("g");
  for (const n of g.nodes) {
    const [cx, cy] = pos.get(n.id);
    const grp = svgEl("g", { class: `node ${n.demo}`, tabindex: "0", role: "button" });
    grp.append(svgEl("rect", { x: cx - NW / 2, y: cy - NH / 2, width: NW, height: NH, rx: 11 }));
    const label = svgEl("text", { x: cx, y: cy });
    label.textContent = n.short;
    const t = svgEl("title");
    t.textContent = `${n.name}: ${n.summary}\n${DEMO_TEXT[n.demo] || n.demo} · ${n.facts} facts (${n.verified} verified)`;
    grp.append(label, t);
    const ask = () => {
      if (state.busy) return;
      els.input.value = `What is ${n.name.replace(/ \(.*\)$/, "")}?`;
      showTab("type");
    };
    grp.addEventListener("click", ask);
    grp.addEventListener("keydown", (ev) => { if (ev.key === "Enter" || ev.key === " ") { ev.preventDefault(); ask(); } });
    nodes.appendChild(grp);
    nodeEls.set(n.id, grp);
  }
  els.kmap.replaceChildren(edges, nodes);
  highlightGraph();
}
function highlightGraph() {
  const k = state.knowledge || { active: [], discussed: [] };
  const active = new Set(k.active || []), discussed = new Set(k.discussed || []);
  for (const [id, grp] of nodeEls) {
    grp.classList.toggle("active", active.has(id));
    grp.classList.toggle("discussed", discussed.has(id));
  }
  for (const [path, a, b] of edgeEls) path.classList.toggle("lit", active.has(a) && (active.has(b) || discussed.has(b)));
  if (nodeEls.size) els.kmapNote.textContent = [...active].map((id) => graphNames.get(id) || id).join(" · ") || "Concepts from the knowledge base";
}

// ---------------- agent activity (drawer) ----------------
const turnEvents = new Map();
function renderPipeline(turn) {
  const evs = turnEvents.get(turn) || [];
  const firstSeen = (stages) => { const i = evs.findIndex((e) => stages.includes(e.stage)); return i < 0 ? 1e9 : i; };
  const order = PIPELINE.map((p, i) => [p, firstSeen(p[1]), i]).sort((a, b) => a[1] - b[1] || a[2] - b[2]).map((x) => x[0]);
  els.pipeline.replaceChildren(...order.map(([name, stages]) => {
    const li = document.createElement("li");
    li.textContent = name;
    const mine = evs.filter((e) => stages.includes(e.stage));
    if (mine.length) {
      const last = mine[mine.length - 1];
      li.className = last.status === "start" ? "active" : last.status === "error" ? "error" : "ok";
    }
    return li;
  }));
}
function addEvent(ev) {
  if (!turnEvents.has(ev.turn)) turnEvents.set(ev.turn, []);
  turnEvents.get(ev.turn).push(ev);
  if (!turnStart.has(ev.turn)) turnStart.set(ev.turn, ev.t);
  if (ev.turn !== lastTurnShown) {
    lastTurnShown = ev.turn;
    const sep = document.createElement("li");
    sep.className = "turn-sep";
    sep.textContent = ev.turn ? `turn ${ev.turn}` : "session";
    els.events.appendChild(sep);
  }
  const key = `${ev.turn}|${ev.stage}|${(ev.data && ev.data.attempt) || ""}`;
  let row = ev.status !== "start" ? openRows.get(key) : null;
  if (row) openRows.delete(key);
  if (!row) { row = document.createElement("li"); els.events.appendChild(row); }
  if (ev.status === "start") openRows.set(key, row);
  row.className = `ev ${ev.status}`;
  const dot = document.createElement("span");
  dot.className = "dot";
  const main = document.createElement("div");
  const title = document.createElement("div");
  title.className = "title";
  const stage = document.createElement("span");
  stage.className = "stage";
  stage.textContent = STAGE_LABEL[ev.stage] || ev.stage;
  title.append(stage, document.createTextNode(ev.title));
  main.appendChild(title);
  if (ev.detail) {
    const det = document.createElement("div");
    det.className = "detail";
    det.textContent = ev.detail;
    main.appendChild(det);
  }
  const time = document.createElement("span");
  time.className = "time";
  time.textContent = `+${Math.max(0, ev.t - turnStart.get(ev.turn)).toFixed(1)}s`;
  row.replaceChildren(dot, main, time);
  const panel = els.events.parentElement;
  panel.scrollTop = panel.scrollHeight;
  renderPipeline(ev.turn);
}

// ---------------- Poyo's voice + stage direction ----------------
// One speech at a time. `gen` increases on barge-in / a new turn: anything older stops at its next step.
let gen = 0;
let speechChain = Promise.resolve();
let currentAudio = null;
function voiceAvailable() { return Boolean((state.mode.voice || {}).provider); }
function stopSpeaking() {
  gen += 1;
  speechChain = Promise.resolve();
  if (currentAudio) { currentAudio.pause(); currentAudio = null; }
  if (window.speechSynthesis) window.speechSynthesis.cancel();
  if (mood === "speaking") setMood("idle");
}
function enqueueSpeech(text, cues, kind) {
  const myGen = gen;
  speechChain = speechChain.then(() => (myGen === gen ? speak(text, cues, kind, myGen) : null)).catch(console.error);
  return speechChain;
}
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function ttsAudio(text) {
  const r = await fetch("/api/tts", { method: "POST", headers: { "Content-Type": "application/json" },
                                      body: JSON.stringify({ text }) });
  if (!r.ok) throw new Error(`tts ${r.status}`);
  const url = URL.createObjectURL(await r.blob());
  const audio = new Audio(url);
  audio.addEventListener("ended", () => URL.revokeObjectURL(url), { once: true });
  await Promise.race([new Promise((res) => audio.addEventListener("loadedmetadata", res, { once: true })), sleep(2500)]);
  return audio;
}
// Cues are timed by their share of the text (speech rate is close to uniform): cue i starts at
// duration * (characters before it / all characters).
async function speak(text, cues, kind, myGen) {
  cues = cues && cues.length ? cues : [{ text, focus: null, caption: null }];
  const total = cues.reduce((n, c) => n + c.text.length + 1, 0);
  const starts = [];
  cues.reduce((n, c) => { starts.push(n / total); return n + c.text.length + 1; }, 0);
  let fired = -1;
  const fireUpTo = (frac) => {
    while (fired + 1 < cues.length && starts[fired + 1] <= frac + 1e-6) { fired += 1; fireCue(cues[fired], kind); }
  };
  setMood("speaking");
  let audio = null;
  if (!muted && voiceAvailable()) {
    try { audio = await ttsAudio(text); } catch (e) { audio = null; }
  }
  if (myGen !== gen) return;
  if (audio) {
    currentAudio = audio;
    const done = new Promise((res) => { audio.addEventListener("ended", res, { once: true }); audio.addEventListener("error", res, { once: true }); });
    try { await audio.play(); } catch (e) { audio = null; }   // autoplay blocked: fall through to timed subtitles
    if (audio) {
      const dur = isFinite(audio.duration) && audio.duration > 0 ? audio.duration : text.length / 15;
      fireUpTo(0);
      while (myGen === gen && !audio.ended && !audio.paused) {
        fireUpTo(audio.currentTime / dur);
        await Promise.race([done, sleep(80)]);
      }
      currentAudio = null;
      if (myGen !== gen) return;
      fireUpTo(1);
    }
  }
  if (!audio && !muted && window.speechSynthesis && !voiceAvailable()) {
    // browser voice (no OpenAI key): one utterance per cue keeps the camera in step
    for (const c of cues) {
      if (myGen !== gen) return;
      fireCue(c, kind);
      await new Promise((res) => {
        const u = new SpeechSynthesisUtterance(c.text);
        u.rate = 1.02; u.onend = res; u.onerror = res;
        window.speechSynthesis.speak(u);
      });
    }
  } else if (!audio) {
    for (const c of cues) {   // muted or no audio: subtitles at reading speed, camera still follows
      if (myGen !== gen) return;
      fireCue(c, kind);
      await sleep(Math.max(1400, c.text.length * 55));
    }
  }
  if (myGen !== gen) return;
  setMood("success", 900);
  subtitleTimer = setTimeout(() => { if (myGen === gen && !state.busy && !rec) subtitle(""); }, 6000);
}

// Stage direction goes through a one-slot queue: at most one request in flight, later cues merge into the next.
let directBusy = false;
let directPending = null;
let lastFocus = null;
function fireCue(cue, kind) {
  subtitle(cue.text);
  if (!kind) return;
  const body = {};
  if (cue.focus && cue.focus !== lastFocus) {   // a tour / comparison cue brings its calculated measurement
    body.focus = cue.focus; lastFocus = cue.focus;
    if (cue.callout) body.callout = cue.callout;
  }
  if (cue.caption) body.caption = cue.caption;
  if (Object.keys(body).length) direct(body);
}
async function direct(body) {
  if (directBusy) { directPending = { ...(directPending || {}), ...body }; return; }
  directBusy = true;
  try {
    const r = await fetch("/api/direct", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    if (!r.ok && r.status !== 409) console.warn("direct", r.status, await r.text());
  } catch (e) { console.warn(e); }
  directBusy = false;
  if (directPending) { const next = directPending; directPending = null; direct(next); }
}

// ---------------- microphone (push-to-talk) ----------------
// Click: talk, Poyo stops listening after ~1.2 s of silence (or click again). Hold Space: talk while held.
let rec = null;
const MAX_MS = 15000, SILENCE_MS = 1200, NO_SPEECH_MS = 7000;
async function startListening(hold) {
  if (rec) return;
  if (state.busy) { subtitle("I'm still working on the last one…", { dim: true, clearAfter: 2500 }); return; }
  if (!voiceAvailable()) {
    subtitle(`Voice is off: ${(state.mode.voice || {}).error || "no speech service"}. Type in the drawer instead.`, { dim: true });
    setDrawer(true); showTab("type");
    return;
  }
  stopSpeaking();   // barge-in: talking over Poyo stops Poyo
  let stream;
  try {
    stream = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true } });
  } catch (e) {
    subtitle("I can't hear you: the microphone is blocked. Allow it in the address bar, or type instead.", { dim: true });
    setMood("error", 1100);
    return;
  }
  const mime = ["audio/webm;codecs=opus", "audio/ogg;codecs=opus", "audio/webm", "audio/ogg"].find((t) => MediaRecorder.isTypeSupported(t)) || "";
  const recorder = new MediaRecorder(stream, mime ? { mimeType: mime } : {});
  const ctx = new AudioContext();
  const analyser = ctx.createAnalyser();
  analyser.fftSize = 1024;
  ctx.createMediaStreamSource(stream).connect(analyser);
  rec = { stream, recorder, ctx, analyser, chunks: [], hold, t0: performance.now(), spoke: false, lastLoud: 0,
          floor: null, cancel: false, raf: 0 };
  recorder.ondataavailable = (e) => { if (e.data && e.data.size) rec && rec.chunks.push(e.data); };
  recorder.onstop = () => finishListening(rec);
  recorder.start(200);
  els.mic.classList.add("listening");
  els.mic.setAttribute("aria-pressed", "true");
  els.mic.disabled = false;
  setMood("listening");
  status("Listening…", "working");
  subtitle(hold ? "I'm listening… (let go of Space when you're done)"
               : "I'm listening… (click the mic again when you're done)", { dim: true });
  heard("");
  const buf = new Float32Array(analyser.fftSize);
  const tick = () => {
    if (!rec || rec.recorder.state !== "recording") return;
    analyser.getFloatTimeDomainData(buf);
    let mean = 0;
    for (const v of buf) mean += v;
    mean /= buf.length;
    let sum = 0;
    for (const v of buf) sum += (v - mean) * (v - mean);   // without the mic's constant offset (DC)
    const rms = Math.sqrt(sum / buf.length);
    const now = performance.now(), el = now - rec.t0;
    // Background level: skip the switch-on pop (first 300 ms), then follow quiet stretches down quickly and
    // louder ones up slowly, so a noisy or hot built-in mic still lets speech stand out.
    if (el > 300) {
      if (rec.floor === null || rms < rec.floor) rec.floor = rms;
      else rec.floor += (rms - rec.floor) * 0.004;
      const loud = rms > Math.max(0.01, rec.floor * 4);
      if (loud && el > 450) { rec.spoke = true; rec.lastLoud = now; }
    }
    const lvl = rec.floor === null ? 0 : (rms - rec.floor) / Math.max(0.02, rec.floor * 4);
    els.mic.style.setProperty("--level", Math.max(0, Math.min(1, lvl)).toFixed(3));
    if (el > MAX_MS) return stopListening(false);
    if (!rec.hold && rec.spoke && now - rec.lastLoud > SILENCE_MS) return stopListening(false);
    if (!rec.hold && !rec.spoke && el > NO_SPEECH_MS) return stopListening(false);
    rec.raf = requestAnimationFrame(tick);
  };
  rec.raf = requestAnimationFrame(tick);
}
function stopListening(cancel) {
  if (!rec || rec.recorder.state !== "recording") return;
  rec.cancel = cancel;
  cancelAnimationFrame(rec.raf);
  rec.recorder.stop();
}
async function finishListening(r) {
  r.stream.getTracks().forEach((t) => t.stop());
  r.ctx.close().catch(() => {});
  rec = null;
  els.mic.classList.remove("listening");
  els.mic.setAttribute("aria-pressed", "false");
  els.mic.style.setProperty("--level", 0);
  const blob = new Blob(r.chunks, { type: r.recorder.mimeType || "audio/webm" });
  // Speech detection only decides when to stop listening; whatever was recorded is sent, and speech-to-text decides
  // whether anything was said (a quiet or unusual mic must never be silently dropped here).
  if (r.cancel || performance.now() - r.t0 < 600 || blob.size < 1000) {
    subtitle(r.cancel ? "" : "That was too short. Click the microphone and ask again.", { dim: true, clearAfter: 4000 });
    setMood("idle"); status("Ready", "");
    return;
  }
  els.mic.classList.add("sending");
  els.mic.disabled = true;
  setMood("thinking");
  status("Transcribing…", "working");
  subtitle("…", { dim: true });
  let res, body;
  try {
    res = await fetch("/api/voice", { method: "POST", headers: { "Content-Type": blob.type || "audio/webm" }, body: blob });
    body = await res.json().catch(() => ({}));
  } catch (e) { res = { status: 0 }; body = { error: String(e) }; }
  els.mic.classList.remove("sending");
  if (res.status === 202) {
    heard(body.text);
    state.turn = body.turn;
    state.messages = [...state.messages, { role: "user", text: body.text, turn: body.turn, via: "voice" }];
    renderMessages();
    setBusy(true);
    const phrases = ((state.mode.voice || {}).phrases || {}).ack || [];
    if (phrases.length) enqueueSpeech(phrases[Math.floor(Math.random() * phrases.length)], null, null);
    return;
  }
  els.mic.disabled = false;
  const ph = (state.mode.voice || {}).phrases || {};
  if (res.status === 422) { enqueueSpeech(ph.unheard ? ph.unheard[0] : "Sorry, I didn't catch that.", null, null); }
  else if (res.status === 409) { enqueueSpeech(ph.busy ? ph.busy[0] : "Hang on, I'm still working on the last one.", null, null); }
  else { subtitle(`Voice failed: ${body.error || res.status}. You can type instead.`, { dim: true }); setMood("error", 1100); }
  status("Ready", "");
}
els.mic.addEventListener("click", () => (rec ? stopListening(false) : startListening(false)));
const typing = (e) => e.target instanceof HTMLInputElement || e.target instanceof HTMLTextAreaElement;
document.addEventListener("keydown", (e) => {
  if (e.code !== "Space" || e.repeat || typing(e)) return;
  e.preventDefault();
  if (!rec) startListening(true);
});
document.addEventListener("keyup", (e) => {
  if (e.code !== "Space" || typing(e)) return;
  e.preventDefault();
  if (rec && rec.hold) stopListening(false);
});
els.mute.addEventListener("click", () => {
  muted = !muted;
  els.mute.setAttribute("aria-pressed", String(muted));
  els.mute.title = muted ? "Unmute Poyo's voice" : "Mute Poyo's voice";
  try { localStorage.setItem("poyo-muted", muted ? "1" : "0"); } catch (e) { /* ignore */ }
  if (muted && currentAudio) currentAudio.pause();
});

// ---------------- events ----------------
async function onEvent(ev) {
  addEvent(ev);
  if (ev.turn !== state.turn && ev.stage !== "listen") return;
  if (ev.stage === "knowledge" && (ev.status === "ok" || ev.status === "info")) {
    state.knowledge = { ...state.knowledge, active: ((ev.data || {}).concepts || []).map((c) => c.id) };
    highlightGraph();
  }
  if (ev.stage === "turn") {
    await refresh();
    const d = ev.data || {};
    status(ev.status === "ok" ? "Done" : ev.title, ev.status === "ok" ? "" : "error");
    lastFocus = null;   // every reply re-aims the camera on its first cue (and replaces the last callout)
    if (d.reply) enqueueSpeech(d.reply, (d.stage || {}).cues, (d.stage || {}).kind);
    else if (ev.status === "error") { subtitle(ev.detail || ev.title, { dim: true }); setMood("error", 1100); }
    return;
  }
  if (ev.stage === "direct") return;   // camera moves while Poyo speaks: listed in Activity, no status change
  if (ev.status === "start") { status(`${ev.title}…`, "working"); if (mood !== "speaking") setMood("thinking"); }
  else if (ev.status === "error") { status(ev.title, "error"); if (mood !== "speaking") setMood("error", 1100); }
  else if (ev.stage !== "listen") status(ev.title, "working");
}

async function refresh() {
  const s = await (await fetch("/api/state")).json();
  state = { ...state, ...s };
  renderModes();
  renderLesson();
  renderSuggestions();
  highlightGraph();
  setBusy(s.busy);
  renderMessages();
}

async function sendText(text) {
  text = (text || "").trim();
  if (!text || state.busy) return;
  stopSpeaking();
  els.input.value = "";
  setBusy(true);
  const r = await fetch("/api/turn", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ text }) });
  if (r.status !== 202) {
    const err = await r.json().catch(() => ({}));
    subtitle(err.error || "Something went wrong", { dim: true });
    setMood("error", 1100);
    setBusy(false);
    return;
  }
  const { turn } = await r.json();
  state.turn = turn;
  state.messages = [...state.messages, { role: "user", text, turn, via: "text" }];
  renderMessages();
  heard(text);
  setMood("thinking");
  subtitle("…", { dim: true });
}

function connect() {
  const es = new EventSource(`/api/events?since=${state.last_event_id || 0}`);
  es.onopen = () => els.live.classList.add("on");
  es.onerror = () => els.live.classList.remove("on");
  es.onmessage = (m) => { try { onEvent(JSON.parse(m.data)); } catch (e) { console.error(e); } };
}

els.form.addEventListener("submit", (e) => { e.preventDefault(); sendText(els.input.value); });
els.reset.addEventListener("click", async () => {
  const r = await fetch("/api/reset", { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" });
  if (r.ok) { stopSpeaking(); renderedMessages = 0; heard(""); await refresh(); subtitle(idleHint(), { dim: true }); }
});

function applyState(s) {
  state = { ...state, ...s };
  renderGraph(s.graph);
  renderModes();
  renderLesson();
  renderSuggestions();
  setBusy(s.busy);
  renderMessages();
  els.mute.setAttribute("aria-pressed", String(muted));
  subtitle(s.busy ? "Working on it…" : idleHint(), { dim: true });
  setMood(s.busy ? "thinking" : "idle");
  status(s.busy ? "Working…" : "Ready", s.busy ? "working" : "");
}

(async function init() {
  if (window.__POYO_STATE__) applyState(window.__POYO_STATE__);   // first paint without waiting
  else {
    try { const r = await fetch("/api/knowledge"); applyState({ ...(await (await fetch("/api/state")).json()), graph: r.ok ? await r.json() : null }); }
    catch (e) { setMood("idle"); }
  }
  for (const ev of (state.events || [])) addEvent(ev);
  const tab = location.hash.slice(1);   // /#activity, /#chat, /#knowledge, /#type open the details drawer there
  if (["activity", "chat", "knowledge", "type"].includes(tab)) { setDrawer(true); showTab(tab); }
  connect();
  await loadPoyo();
})();
