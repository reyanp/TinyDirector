// Poyo Lab front end. Everything shown comes from the tutor server: /api/state and the /api/events stream of
// real execution events (director/events.py). Poyo's mood is driven by those events, never by timers that
// pretend work is happening.
"use strict";

const $ = (id) => document.getElementById(id);
const els = {
  poyo: $("poyo"), bubble: $("bubble"), chat: $("chat"), input: $("input"), send: $("send"), form: $("composer"),
  events: $("events"), pipeline: $("pipeline"), modes: $("modes"), live: $("live-dot"), reset: $("reset"),
  suggestions: $("suggestions"), title: $("lesson-title"), text: $("lesson-text"), meta: $("lesson-meta"),
  snapWrap: $("snapshot-wrap"), snap: $("snapshot"),
};

const STAGE_LABEL = {
  understand: "understand", scene: "scene", plan: "plan", calculate: "calculate", director: "director",
  builder: "builder", blender: "blender", verify: "verify", load: "load", apply: "blender", explain: "explain",
  turn: "done",
};
const PIPELINE = [
  ["understand", ["understand", "scene"]], ["plan", ["plan"]], ["calculate", ["calculate"]],
  ["build", ["director", "builder", "blender", "load", "apply"]], ["verify", ["verify"]], ["explain", ["explain"]],
];
const SUGGEST_START = ["Teach me why LEDs need resistors.", "How does a voltage divider work?",
                       "Why would someone use a BJT over a MOSFET?"];
const SUGGEST_CIRCUIT = ["What if the resistor is 1,000 Ω?", "Why did the current decrease?",
                         "Explain that in simpler terms.", "What happens if I remove the resistor?"];

let state = { mode: {}, busy: false, turn: 0, messages: [], lesson: null, last_event_id: 0 };
let renderedMessages = 0;
let openRows = new Map();      // turn|stage|attempt -> row element still "start"
let turnStart = new Map();     // turn -> first event time
let lastTurnShown = 0;
let mood = "idle";
let moodTimer = null;

// ---------------- Poyo ----------------
function setMood(next, holdMs) {
  clearTimeout(moodTimer);
  mood = next;
  els.poyo.className = `poyo ${next}`;
  if (holdMs) moodTimer = setTimeout(() => setMood(state.busy ? "thinking" : "idle"), holdMs);
}
function say(text) {
  els.bubble.textContent = text || "";
  els.bubble.classList.toggle("show", Boolean(text));
}
async function loadPoyo() {
  if (els.poyo.querySelector("svg")) return;  // inlined by the server
  try {
    const svg = await (await fetch("/poyo.svg")).text();
    els.poyo.innerHTML = svg;  // our own static asset (served by the tutor server), inlined so CSS can animate it
  } catch (e) { /* the page still works without the mascot */ }
}

// ---------------- rendering ----------------
function badge(text, cls) {
  const b = document.createElement("span");
  b.className = `badge ${cls}`;
  b.textContent = text;
  return b;
}
function renderModes() {
  const m = state.mode || {};
  els.modes.replaceChildren(
    badge(m.model || "model", m.live ? "live" : "mock"),
    badge(m.executor || "engine", m.simulated ? "mock" : "live"));
}

function messageEl(m) {
  const d = document.createElement("div");
  d.className = `msg ${m.role}${m.role === "tutor" && m.ok === false ? " failed" : ""}`;
  if (m.role === "tutor") {
    const who = document.createElement("span");
    who.className = "who";
    who.textContent = "Poyo";
    const tagText = { mock: "MOCK text", template: "template (model text failed the number check)", error: "error" }[m.source];
    if (tagText) {
      const t = document.createElement("span");
      t.className = "tag";
      t.textContent = tagText;
      who.appendChild(t);
    }
    d.appendChild(who);
  }
  const body = document.createElement("span");
  body.className = "body";
  d.appendChild(body);
  return [d, body];
}

function renderEmpty() {
  if (state.messages.length) return;
  const e = document.createElement("div");
  e.className = "empty";
  const s = document.createElement("strong");
  s.textContent = "Hi, I'm Poyo!";
  const p = document.createElement("span");
  p.textContent = "Ask me how a circuit works. I'll build it in Blender, check the numbers, and explain what you see.";
  e.append(s, p);
  els.chat.replaceChildren(e);
}

function typeOut(target, text) {
  return new Promise((resolve) => {
    const cps = Math.max(70, text.length / 6);   // at most ~6 s per reply
    let i = 0;
    const t0 = performance.now();
    function step(now) {
      const n = Math.min(text.length, Math.floor(((now - t0) / 1000) * cps));
      if (n > i) { target.textContent = text.slice(0, n); i = n; els.chat.scrollTop = els.chat.scrollHeight; }
      if (i < text.length) requestAnimationFrame(step); else resolve();
    }
    requestAnimationFrame(step);
  });
}

async function renderMessages(animateLast) {
  if (!state.messages.length) { renderEmpty(); renderedMessages = 0; return; }
  if (renderedMessages === 0) els.chat.replaceChildren();
  const fresh = state.messages.slice(renderedMessages);
  renderedMessages = state.messages.length;
  for (const [i, m] of fresh.entries()) {
    const [d, body] = messageEl(m);
    els.chat.appendChild(d);
    els.chat.scrollTop = els.chat.scrollHeight;
    const isLast = i === fresh.length - 1;
    if (animateLast && isLast && m.role === "tutor") {
      setMood("speaking");
      say("");
      await typeOut(body, m.text);
      setMood(m.ok === false ? "error" : "success", 1300);
    } else {
      body.textContent = m.text;
    }
  }
}

function renderLesson() {
  const l = state.lesson;
  if (!l) {
    els.title.textContent = "No lesson yet";
    els.text.textContent = "Ask Poyo a question and the lesson appears in Blender.";
    els.meta.replaceChildren();
    els.snapWrap.hidden = true;
    return;
  }
  els.title.textContent = l.title;
  els.text.textContent = l.explanation || "";
  const chips = [];
  const info = l.info || {};
  const chip = (t, cls = "") => { const c = document.createElement("span"); c.className = `chip ${cls}`; c.textContent = t; return c; };
  if (info.kind === "circuit") {
    // strings formatted by the calculation module on the server; the UI never does circuit math
    for (const t of (info.chips || []).slice(0, 2)) chips.push(chip(t));
  } else if (info.kind === "topic") {
    chips.push(chip(`built by ${info.builder}`, "neutral"));
    if (info.cached) chips.push(chip("cached, verified", "neutral"));
  }
  els.meta.replaceChildren(...chips);
  if (l.snapshot) {
    els.snap.src = `/api/snapshot/${encodeURIComponent(l.snapshot)}`;
    els.snapWrap.hidden = false;
  } else {
    els.snapWrap.hidden = true;
  }
}

function renderSuggestions() {
  const list = state.lesson && (state.lesson.info || {}).kind === "circuit" ? SUGGEST_CIRCUIT : SUGGEST_START;
  els.suggestions.replaceChildren(...list.map((t) => {
    const b = document.createElement("button");
    b.type = "button";
    b.textContent = t;
    b.disabled = state.busy;
    b.addEventListener("click", () => send(t));
    return b;
  }));
}

function setBusy(busy) {
  state.busy = busy;
  els.send.disabled = busy;
  els.input.disabled = busy;
  for (const b of els.suggestions.querySelectorAll("button")) b.disabled = busy;
  if (!busy) els.input.focus();
}

// ---------------- agent activity ----------------
function renderPipeline(turn) {
  const evs = turnEvents.get(turn) || [];
  // stages in the order they ACTUALLY ran this turn (paths differ); stages not reached yet keep the default order
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

const turnEvents = new Map();
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
  if (!row) {
    row = document.createElement("li");
    els.events.appendChild(row);
  }
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
  els.events.scrollTop = els.events.scrollHeight;
  renderPipeline(ev.turn);
}

async function onEvent(ev) {
  addEvent(ev);
  if (ev.turn !== state.turn) return;
  if (ev.stage === "turn") {
    await refresh(true);
    return;
  }
  if (ev.status === "start") { setMood("thinking"); say(`${ev.title}…`); }
  else if (ev.status === "error") { setMood("error", 1100); say(ev.title); }
  else if (state.busy && mood !== "error") setMood("thinking");
}

// ---------------- server ----------------
async function refresh(animate) {
  const r = await fetch("/api/state");
  const s = await r.json();
  const wasBusy = state.busy;
  state = { ...state, ...s };
  renderModes();
  renderLesson();
  renderSuggestions();
  setBusy(s.busy);
  await renderMessages(animate && wasBusy);
  if (!s.busy && mood !== "success" && mood !== "error" && mood !== "speaking") { setMood("idle"); }
  if (!s.busy) say(state.lesson ? "" : "Ask me anything about circuits!");
}

async function send(text) {
  text = (text || "").trim();
  if (!text || state.busy) return;
  els.input.value = "";
  setBusy(true);
  const r = await fetch("/api/turn", { method: "POST", headers: { "Content-Type": "application/json" },
                                        body: JSON.stringify({ text }) });
  if (r.status !== 202) {
    const err = await r.json().catch(() => ({}));
    say(err.error || "Something went wrong");
    setMood("error", 1100);
    setBusy(false);
    return;
  }
  const { turn } = await r.json();
  state.turn = turn;
  state.messages = [...state.messages, { role: "user", text, turn }];
  await renderMessages(false);
  setMood("thinking");
  say("Thinking…");
}

function connect() {
  const es = new EventSource(`/api/events?since=${state.last_event_id || 0}`);
  es.onopen = () => els.live.classList.add("on");
  es.onerror = () => els.live.classList.remove("on");
  es.onmessage = (m) => { try { onEvent(JSON.parse(m.data)); } catch (e) { console.error(e); } };
}

els.form.addEventListener("submit", (e) => { e.preventDefault(); send(els.input.value); });
els.input.addEventListener("input", () => {
  if (!state.busy) setMood(els.input.value.trim() ? "listening" : "idle");
});
els.reset.addEventListener("click", async () => {
  const r = await fetch("/api/reset", { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" });
  if (r.ok) { renderedMessages = 0; await refresh(false); }
});

function applyState(s) {
  state = { ...state, ...s };
  renderModes();
  renderLesson();
  renderSuggestions();
  setBusy(s.busy);
  renderMessages(false);
  say(s.busy ? "Working on it…" : state.lesson ? "" : "Ask me anything about circuits!");
  setMood(s.busy ? "thinking" : "idle");
}

(async function init() {
  if (window.__POYO_STATE__) applyState(window.__POYO_STATE__);   // first paint without waiting
  else { setMood("idle"); await refresh(false); }
  for (const ev of (state.events || [])) addEvent(ev);
  connect();
  await loadPoyo();
})();
