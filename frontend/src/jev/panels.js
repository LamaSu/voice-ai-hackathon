/**
 * Lane A panels: who is speaking (ECAPA), Jev's typed decisions with their probabilities,
 * the turn-taking timeline, and people/conversation memory.
 *
 * Self-contained: mountJevPanels(client, root) subscribes to the bot's RTVI server messages
 * (types `state`, `jev`, `interaction`, `vad`, `memory`, `transcript` — see docs/ARCHITECTURE.md)
 * and renders into `root`. It does not touch the lane D shell or HUD.
 */

import { RTVIEvent } from "@pipecat-ai/client-js";

// Policy thresholds (backend/app/turns/policy.py) drawn as tick marks on the bars.
const THRESHOLDS = {
  wants_floor: 0.75,
  correcting_agent: 0.7,
  addressed_to_agent: 0.3,
  turn_complete: 0.6,
  introducing_self: 0.6,
};

const ACTION_CLASS = {
  interrupt: "act-interrupt",
  respond: "act-respond",
  continue: "act-continue",
  hold: "act-hold",
  wait: "act-hold",
  drop: "act-drop",
};

const TIMELINE_S = 24;

function el(tag, cls, text) {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (text != null) n.textContent = text;
  return n;
}

function bar(label, p, { threshold, highlight } = {}) {
  const row = el("div", `pbar${highlight ? " pbar-hi" : ""}`);
  row.append(el("span", "pbar-label", label));
  const track = el("span", "pbar-track");
  const fill = el("span", "pbar-fill");
  fill.style.width = `${Math.round(Math.max(0, Math.min(1, p)) * 100)}%`;
  track.append(fill);
  if (threshold != null) {
    const tick = el("span", "pbar-tick");
    tick.style.left = `${threshold * 100}%`;
    tick.title = `policy threshold ${threshold}`;
    track.append(tick);
  }
  row.append(track, el("span", "pbar-val", p.toFixed(2)));
  return row;
}

export function mountJevPanels(client, root) {
  root.innerHTML = "";
  root.classList.add("jev-grid");

  // ---- speaker panel ---------------------------------------------------------
  const speaker = el("div", "panel jev-speaker");
  speaker.append(el("h2", null, "Who's speaking (ECAPA)"));
  const spHead = el("div", "sp-head");
  const spDot = el("span", "vad-dot");
  const spName = el("span", "sp-name", "—");
  const spPhase = el("span", "pill pill-idle", "idle");
  spHead.append(spDot, spName, spPhase);
  const spMeta = el("div", "sp-meta", "no speaker yet");
  const spEnergy = el("div", "energy");
  const spEnergyFill = el("span");
  spEnergy.append(spEnergyFill);
  const spProbs = el("div", "probs");
  const spPartial = el("div", "partial");
  const spBot = el("div", "bot-line");
  const spVision = el("div", "vision-line", "vision → Jev: waiting for camera…");
  speaker.append(spHead, spMeta, spEnergy, spProbs, spPartial, spBot, spVision);

  // ---- Jev panels ------------------------------------------------------------
  const overlap = el("div", "panel jev-decision");
  overlap.append(el("h2", null, "Jev · barge-in (user talks over bot)"));
  const overlapBody = el("div", "dec-body", "waiting for overlapping speech…");
  overlap.append(overlapBody);

  const eot = el("div", "panel jev-decision");
  eot.append(el("h2", null, "Jev · end of turn"));
  const eotBody = el("div", "dec-body", "waiting for a pause…");
  eot.append(eotBody);

  // ---- timeline --------------------------------------------------------------
  const tl = el("div", "panel jev-timeline");
  tl.append(el("h2", null, `Turn-taking timeline (last ${TIMELINE_S}s)`));
  const canvas = el("canvas", "tl-canvas");
  canvas.width = 1060;
  canvas.height = 150;
  const legend = el("div", "tl-legend");
  for (const [cls, label] of [
    ["lg-user", "user speech (VAD)"],
    ["lg-bot", "bot speaking"],
    ["act-interrupt", "interrupt"],
    ["act-continue", "backchannel / continue"],
    ["act-respond", "respond"],
    ["act-hold", "hold"],
    ["act-drop", "drop"],
  ]) {
    const item = el("span", "lg");
    item.append(el("i", cls), document.createTextNode(label));
    legend.append(item);
  }
  const tlLog = el("ul", "tl-log");
  tl.append(canvas, legend, tlLog);

  // ---- memory ----------------------------------------------------------------
  const mem = el("div", "panel jev-memory");
  const memHead = el("div", "mem-head");
  memHead.append(el("h2", null, "Memory · people & conversation"));
  const forget = el("button", "ghost", "Forget everyone");
  forget.addEventListener("click", () => clearAllMemory());
  memHead.append(forget);

  // Prominent header button: wipe every person (names, voice profiles, facts) and the summary.
  // Uses the REST endpoint, so it also works when no call is connected.
  const headerClear = el("button", "danger", "Clear all people & memory");
  headerClear.title = "Forget all names, voice profiles, facts and the conversation summary";
  headerClear.addEventListener("click", () => clearAllMemory());
  document.querySelector(".bar-right")?.prepend(headerClear);

  async function clearAllMemory() {
    if (!confirm("Forget everyone? This deletes all names, voice profiles, facts and the conversation summary.")) return;
    headerClear.disabled = forget.disabled = true;
    try {
      const res = await fetch("/api/memory/reset", { method: "POST" });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      renderMemory(await res.json());
      spName.textContent = "—";
      spMeta.textContent = "memory cleared · 0 voice profiles";
      spProbs.innerHTML = "";
      logLine("memory cleared — everyone forgotten", "act-drop");
    } catch (e) {
      alert(`Could not clear memory: ${e.message ?? e}`);
    } finally {
      headerClear.disabled = forget.disabled = false;
    }
  }
  const people = el("ul", "people");
  people.append(el("li", "empty", "Nobody introduced yet. Say “Hi, I'm …”"));
  const summary = el("p", "summary", "");
  mem.append(memHead, people, el("h3", null, "Conversation summary"), summary);

  root.append(speaker, overlap, eot, tl, mem);
  fetch("/api/memory").then((r) => (r.ok ? r.json() : null)).then((m) => m && renderMemory(m)).catch(() => {});

  // ---- state for the timeline -----------------------------------------------
  let serverNow = 0; // latest server monotonic time seen
  let localAtServerNow = performance.now();
  const userSegs = []; // [start, end|null]
  const botSegs = [];
  const marks = []; // {t, action, label}
  let botSpeaking = false;

  const toServerNow = () => serverNow + (performance.now() - localAtServerNow) / 1000;

  function segPush(segs, on, t) {
    const last = segs[segs.length - 1];
    if (on && (!last || last[1] != null)) segs.push([t, null]);
    if (!on && last && last[1] == null) last[1] = t;
  }

  function onState(s, t) {
    spPhase.textContent = s.phase.replace("_", " ");
    spPhase.className = `pill ${s.phase === "idle" ? "pill-idle" : "pill-live"} phase-${s.phase}`;
    spDot.classList.toggle("on", !!s.user_vad);
    spEnergyFill.style.width = `${Math.min(100, Math.round((s.user_energy || 0) * 100))}%`;
    const sp = s.speaker || {};
    spName.textContent = sp.name || sp.label || "unknown";
    spMeta.textContent = sp.label
      ? `${sp.label} · ${Math.round((sp.confidence || 0) * 100)}% · ${sp.source} decision · ${sp.known_speakers} voice profile${sp.known_speakers === 1 ? "" : "s"}`
      : `${sp.known_speakers || 0} voice profiles · speak ≥1s to be recognized`;
    spProbs.innerHTML = "";
    const entries = Object.entries(sp.probabilities || {}).sort((a, b) => b[1] - a[1]).slice(0, 5);
    for (const [k, p] of entries) spProbs.append(bar(k, p, { highlight: k === sp.label }));
    spPartial.textContent = s.partial ? `“${s.partial}”` : "";
    spBot.textContent = s.bot_speaking && s.bot_sentence ? `bot: ${s.bot_sentence}` : "";
    const v = s.vision || {};
    spVision.textContent = v.enabled
      ? `vision → Jev (server): ${v.looking_at_agent ? "looking at agent" : "looking away"} · confusion ${Number(v.confusion_p || 0).toFixed(2)} · wants turn ${v.wants_turn ? "yes" : "no"} · nod ${v.nod || 0}`
      : "vision → Jev: no user_state received yet";
    spVision.classList.toggle("on", !!v.enabled);
    if (s.bot_speaking !== botSpeaking) {
      botSpeaking = s.bot_speaking;
      segPush(botSegs, botSpeaking, t);
    }
  }

  function renderDecision(body, ev) {
    body.innerHTML = "";
    const d = ev.decision || {};
    const head = el("div", "dec-head");
    head.append(el("span", `chip ${ACTION_CLASS[d.action] || ""}`, d.action || "?"));
    head.append(el("span", "dec-reason", d.reason || ""));
    const lat = ev.answers?.latency_ms;
    head.append(el("span", "dec-lat", ev.answers ? (ev.answers.ok ? `${lat} ms` : ev.answers.error) : "rule"));
    body.append(head);
    if (ev.text) body.append(el("div", "dec-text", `“${ev.text}”`));
    const a = ev.answers;
    if (!a || !a.ok) {
      body.append(el("div", "muted", "decided by deterministic policy (no Jev answer)"));
      return;
    }
    for (const [name, c] of Object.entries(a.choices || {})) {
      body.append(el("div", "dec-q", `${name} → ${c.choice} (${Math.round(c.confidence * 100)}%)`));
      const probs = Object.entries(c.probabilities || {}).sort((x, y) => y[1] - x[1]);
      for (const [label, p] of probs) body.append(bar(label, p, { highlight: label === c.choice }));
    }
    const nouls = Object.entries(a.nouls || {});
    if (nouls.length) body.append(el("div", "dec-q", "yes/no questions"));
    for (const [name, p] of nouls) body.append(bar(name, p, { threshold: THRESHOLDS[name] }));
  }

  function renderMemory(m) {
    people.innerHTML = "";
    const list = m.people || [];
    if (!list.length) people.append(el("li", "empty", "Nobody introduced yet. Say “Hi, I'm …”"));
    for (const p of list) {
      const li = el("li");
      const top = el("div", "person-top");
      top.append(el("span", "person-name", p.name || "(unnamed)"), el("span", "person-label", p.label));
      top.append(el("span", "person-meta", `${p.speech_seconds}s · ${p.utterances} utt`));
      li.append(top);
      if (p.facts?.length) {
        const facts = el("ul", "facts");
        for (const f of p.facts) facts.append(el("li", null, f));
        li.append(facts);
      }
      people.append(li);
    }
    summary.textContent = m.summary || "—";
  }

  function logLine(text, cls) {
    const li = el("li", cls, text);
    tlLog.prepend(li);
    while (tlLog.children.length > 6) tlLog.lastChild.remove();
  }

  client.on(RTVIEvent.ServerMessage, (msg) => {
    const ev = msg?.data ?? msg;
    if (!ev || typeof ev !== "object") return;
    if (typeof ev.t === "number" && ev.t > serverNow) {
      serverNow = ev.t;
      localAtServerNow = performance.now();
    }
    switch (ev.type) {
      case "state":
        onState(ev.state, ev.t);
        break;
      case "vad":
        segPush(userSegs, ev.speaking, ev.t);
        break;
      case "jev": {
        const target = ev.set?.startsWith("overlap") ? overlapBody : eotBody;
        renderDecision(target, ev);
        const a = ev.decision?.action;
        if (a && a !== "wait") marks.push({ t: ev.t, action: a, label: ev.decision.reason });
        break;
      }
      case "interaction":
        if (ev.event === "filler") {
          logLine(`filler [${ev.category}] “${ev.text}” (${ev.duration_s}s, while the LLM thinks)`, "act-continue");
          break;
        }
        if (["interrupt", "backchannel", "drop", "introduction"].includes(ev.event)) {
          const who = ev.speaker ? `${ev.speaker}: ` : "";
          const txt = ev.event === "introduction" ? `${ev.name} introduced (${ev.speaker || "?"})` : `${ev.event} — ${who}${ev.text || ""}`;
          logLine(txt, ACTION_CLASS[ev.event === "backchannel" ? "continue" : ev.event] || "");
        }
        break;
      case "memory":
        renderMemory(ev);
        break;
      default:
        break;
    }
  });

  // ---- timeline drawing -------------------------------------------------------
  const ctx = canvas.getContext("2d");
  const css = getComputedStyle(document.documentElement);
  const color = (v, fb) => css.getPropertyValue(v).trim() || fb;
  const COLORS = {
    user: color("--good", "#37d67a"),
    bot: "#2f81f7",
    interrupt: color("--poor", "#e5484d"),
    continue: color("--fair", "#f5a623"),
    respond: color("--good", "#37d67a"),
    hold: "#8b949e",
    drop: "#a371f7",
    line: color("--line", "#262d36"),
    muted: color("--muted", "#8b949e"),
  };

  function draw() {
    const W = canvas.width;
    const H = canvas.height;
    const now = toServerNow();
    const x = (t) => W - ((now - t) / TIMELINE_S) * (W - 70) ;
    ctx.clearRect(0, 0, W, H);
    ctx.font = "12px ui-sans-serif, system-ui";
    const lanes = [
      ["user", 18, userSegs],
      ["bot", 56, botSegs],
    ];
    for (const [name, y, segs] of lanes) {
      ctx.fillStyle = COLORS.muted;
      ctx.fillText(name, 4, y + 14);
      ctx.fillStyle = COLORS.line;
      ctx.fillRect(60, y + 9, W - 60, 1);
      ctx.fillStyle = COLORS[name];
      for (const [a, b] of segs) {
        const x0 = Math.max(60, x(a));
        const x1 = x(b ?? now);
        if (x1 > 60) ctx.fillRect(x0, y, Math.max(2, x1 - x0), 20);
      }
    }
    ctx.fillStyle = COLORS.muted;
    ctx.fillText("jev", 4, 112);
    for (const m of marks) {
      const mx = x(m.t);
      if (mx < 60) continue;
      ctx.strokeStyle = COLORS[m.action] || COLORS.muted;
      ctx.lineWidth = 2;
      ctx.beginPath();
      ctx.moveTo(mx, 10);
      ctx.lineTo(mx, 120);
      ctx.stroke();
      ctx.fillStyle = COLORS[m.action] || COLORS.muted;
      ctx.beginPath();
      ctx.arc(mx, 108, 5, 0, Math.PI * 2);
      ctx.fill();
    }
    // seconds grid
    ctx.fillStyle = COLORS.muted;
    for (let s = 0; s <= TIMELINE_S; s += 4) {
      const gx = W - (s / TIMELINE_S) * (W - 70);
      ctx.fillText(s === 0 ? "now" : `-${s}s`, gx - 12, H - 6);
    }
    // prune
    const cutoff = now - TIMELINE_S - 2;
    for (const segs of [userSegs, botSegs]) while (segs.length && segs[0][1] != null && segs[0][1] < cutoff) segs.shift();
    while (marks.length && marks[0].t < cutoff) marks.shift();
    requestAnimationFrame(draw);
  }
  requestAnimationFrame(draw);

  return { renderMemory, renderDecision };
}
