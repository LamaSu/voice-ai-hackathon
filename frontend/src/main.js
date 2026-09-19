/**
 * D1 — call UI with camera and the live latency HUD.
 *
 * Connects to the bot over SmallWebRTC, shows a local camera preview, and
 * renders Contract 4 `metrics` as they arrive. Lane C attaches its face
 * tracking to the same local video track (see `startFaceTracking`), so the
 * camera is opened once and the pixels never leave the page.
 */

import { PipecatClient, RTVIEvent } from "@pipecat-ai/client-js";
import { SmallWebRTCTransport } from "@pipecat-ai/small-webrtc-transport";

import { LatencyHUD } from "./hud.js";
import { MSG_METRICS } from "./contracts.js";

const hud = new LatencyHUD(document);

const els = {
  connect: document.getElementById("connect"),
  state: document.getElementById("state"),
  cam: document.getElementById("cam"),
  mic: document.getElementById("mic"),
  camToggle: document.getElementById("camToggle"),
  log: document.getElementById("log"),
};

const client = new PipecatClient({
  transport: new SmallWebRTCTransport({
    // Vite proxies /api to the Pipecat runner, so one origin serves both.
    webrtcUrl: "/api/offer",
  }),
  enableMic: true,
  enableCam: true,
  callbacks: {
    onTransportStateChanged: (state) => setState(state),
    onBotReady: () => log("system", "bot ready"),
    onUserTranscript: (data) => {
      if (data?.final) log("you", data.text);
    },
    onBotTranscript: (data) => log("agent", data?.text ?? ""),
    onError: (err) => log("error", String(err?.message ?? err)),
  },
});

// Contract 4 arrives as an RTVI server message: {type: "metrics", payload: {...}}.
client.on(RTVIEvent.ServerMessage, (msg) => {
  const body = msg?.data ?? msg;
  if (body?.type === MSG_METRICS) hud.record(body.payload);
});

client.on(RTVIEvent.TrackStarted, (track, participant) => {
  if (participant?.local && track.kind === "video") showLocalVideo(track);
});

els.connect.addEventListener("click", async () => {
  if (client.connected) {
    await client.disconnect();
    return;
  }
  els.connect.disabled = true;
  try {
    await client.connect();
  } catch (err) {
    log("error", `connect failed: ${err?.message ?? err}`);
    setState("disconnected");
  } finally {
    els.connect.disabled = false;
  }
});

els.mic.addEventListener("change", (e) => client.enableMic(e.target.checked));
els.camToggle.addEventListener("change", (e) => {
  client.enableCam(e.target.checked);
  if (!e.target.checked) els.cam.srcObject = null;
});

function showLocalVideo(track) {
  els.cam.srcObject = new MediaStream([track]);
}

function setState(state) {
  els.state.textContent = state;
  const connected = state === "ready" || state === "connected";
  els.state.className = `pill ${connected ? "pill-live" : "pill-idle"}`;
  els.connect.textContent = client.connected ? "Disconnect" : "Connect";
}

function log(who, text) {
  if (!text) return;
  const li = document.createElement("li");
  li.className = `log-${who}`;
  li.innerHTML = `<span class="who">${who}</span><span class="what"></span>`;
  li.querySelector(".what").textContent = text;
  els.log.append(li);
  els.log.scrollTop = els.log.scrollHeight;
}

// Exposed so the D2 test harness can drive the HUD without a live bot.
window.__hud = hud;

// `?replay=1` renders a recorded trace so the HUD can be checked with no bot
// running and no Gradium credits spent.
if (new URLSearchParams(location.search).has("replay")) {
  const { replayInto } = await import("./replay.js");
  log("system", "replay mode — recorded metrics, not a live call");
  replayInto(hud);
}
