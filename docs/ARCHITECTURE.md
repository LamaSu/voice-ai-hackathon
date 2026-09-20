# Architecture: Jev-driven, interruption-first multimodal voice agent

> Status: **working end to end**: voice in, a Jev turn decision, General Compute, Gradium TTS, voice out, over real WebRTC. It's verified by `backend/scripts/e2e_webrtc.py` and in a browser; see [Build status](#build-status).

## Core idea

ASR, VAD, speaker ID, gaze and bot playback continuously produce signals. Those signals feed one **InteractionState**. **Jev** (TypeSafe AI) makes typed interaction decisions, and deterministic code turns them into Pipecat frames.

> Jev decides. Code executes. Pipecat's default turn heuristics are **not** the brain.

| Layer | What | Where |
| --- | --- | --- |
| Perception (fast, deterministic) | Silero VAD, RMS, Gradium streaming ASR (interim and final), ECAPA speaker embeddings, MediaPipe gaze and head pose (in the browser), bot playback state | `backend/app/perception/`, `frontend/src/gaze/` |
| State | `InteractionState` (pydantic), mutated through `StateEngine`, published to the UI at about 10 Hz | `backend/app/state/` |
| Cognition: interaction | Jev fan-out question sets: interrupt, backchannel, end of turn, addressed to agent, self-introduction | `backend/app/jev/` |
| Cognition: content | General Compute LLM (OpenAI-compatible), with the conversation summary and people memory in the system prompt | `backend/app/services.py`, `backend/app/memory/` |
| Policy (deterministic) | Jev probabilities plus timing become an `Action`: INTERRUPT, CONTINUE, WAIT, RESPOND, HOLD or DROP. Every threshold is in one place. | `backend/app/turns/policy.py` |
| Action | Pipecat frames: `ProposedUserStarted/StoppedSpeakingFrame`, `InterruptionFrame`, which flushes the LLM, TTS and the output audio queue | `backend/app/turns/controller.py` |

## Providers

Every key is read from the repo-root `.env`:

| Key | Also accepted as | Used for |
| --- | --- | --- |
| `GRADIUM_API_KEY` | | Gradium STT and TTS |
| `GENERAL_COMPUTE` | `GENERAL_COMPUTE_API_KEY` | General Compute LLM |
| `JEV_API_KEY` | `TYPESAFE_API_KEY` | Jev |

| Role | Provider | Notes (measured 2026-09-19 from `backend/scripts/smoke_providers.py`) |
| --- | --- | --- |
| ASR | Gradium `wss://api.gradium.ai/api/speech/asr` via `pipecat.services.gradium.stt.GradiumSTTService` | Needs a language (`en`), `delay_in_frames=7` (its minimum). Interim text streams continuously but runs ~0.9 s behind the speaker; the final transcript arrives after a VAD-stop flush. That lag is why barge-in needs a duration backstop (below). |
| TTS | Gradium `wss://api.gradium.ai/api/speech/tts` via `GradiumTTSService` | 48 kHz PCM with word timestamps. First audio in about 0.4–1.0 s. |
| LLM | **`app/llm_general_compute.py`** wraps Pipecat's OpenAI service: `reasoning_effort="low"` (the API rejects `"none"`) and the `analysis` channel is filtered out of the stream, because gpt-oss is a reasoning model and the provider occasionally flushes its chain-of-thought into `content`, which Pipecat then speaks. A `ReasoningFilter` processor between the LLM and TTS drops any response that still opens like private notes ("We need to respond…", "The user says…"). | General Compute `https://api.generalcompute.com/v1` via Pipecat `OpenAILLMService(base_url=...)` | Time to first token on a demo-shaped prompt (`scripts/latency_check.py`, 5 trials): **gpt-oss-120b 559 ms**, gemma-4-31B-it 1138 ms, minimax-m2.7 1457 ms. Default `gpt-oss-120b`, overridable with `LLM_MODEL`. These move with provider load: minimax measured fastest earlier in the day, so re-run the check before relying on it. |
| Interaction decisions | Jev `POST https://api.typesafe.ai/v1/systemone`, model `jev-latest` (currently jev-1.13.0), via `typesafe-sdk` | p50 ≈ 145 ms for a 2–4 question fan-out (range 90–470 ms). Hard timeout of 600 ms, then deterministic fallback. |

## Pipeline (Pipecat 1.11)

```
transport.input()            SmallWebRTC; the mic stays open while the bot talks (full duplex); browser AEC
 → RTVIProcessor             (added by PipelineWorker) client message `user_state` (Contract 1, lane C) → VisionState
 → VADProcessor(Silero)      VAD frames are SIGNALS ONLY; they also make Gradium STT flush for final text
 → SpeakerIdProcessor        ECAPA every 0.4 s on the rolling 1.5 s window → live speaker; full-utterance classify at VAD stop
 → GradiumSTTService         always streaming, including during bot speech
 → InteractionController     the ONLY component that opens or closes user turns or interrupts (uses Jev + policy)
 → LLMUserAggregator         ExternalUserTurnStrategies: turns come only from the controller's Proposed* frames
 → OpenAILLMService          General Compute
 → GradiumTTSService
 → transport.output()
 → BotTap                    spoken words, synced to playback → bot.current_sentence
 → LLMAssistantAggregator
```

**Why the controller holds transcripts.** Pipecat's user aggregator appends *every* `TranscriptionFrame` to the next user message. The controller buffers the final transcripts of the current utterance and forwards them only when that speech has become a user turn. Backchannels, echo and side talk are dropped and never reach the LLM context. This is also why the user's first words aren't lost at an interruption: the speech was transcribed while Jev was deciding, and the whole utterance is released once Jev says INTERRUPT.

**Interruption.** The controller pushes an `InterruptionFrame` **downstream only**. That cancels the in-flight LLM request, drops the TTS audio context and drains the output transport's audio queue, which are the three things that must stop. The STT's pending transcripts upstream are untouched. The controller then pushes `ProposedUserStartedSpeakingFrame`; the aggregator uses `ExternalUserTurnStrategies(enable_interruptions=False)`, so opening a turn never interrupts by itself.

**Early end of turn.** Gradium's final transcript arrives about 0.9 s after the VAD-stop flush. While the user is silent, the controller asks Jev (set B) on each new partial transcript and responds as soon as Jev says the turn is complete. The late final is then dropped, and any tail of it that leaks into the next utterance is stripped.

## Interaction state machine

```
IDLE ──first words──▶ LISTENING ──Jev B: RESPOND──▶ THINKING ──BotStartedSpeaking──▶ BOT_SPEAKING
  ▲                      │  ▲                                                         │
  │                      │  └── Jev B: HOLD (≤2.0 s silence, then respond anyway)     │ VAD start
  │                      └── Jev B: DROP (not addressed) ──▶ IDLE                    ▼
  └──────────── BotStoppedSpeaking ◀──────────────────────────────────────────── OVERLAP
                                                   Jev A: CONTINUE (backchannel/echo/noise/side talk) → back to BOT_SPEAKING
                                                   Jev A: INTERRUPT → flush → LISTENING
                                                   Jev A: WAIT → re-ask on the next partial (≥150 ms apart); cap 1.5 s
```

## Jev question sets

Defined in `backend/app/jev/questions.py`. Each set is sent as one request (fan-out); every question sees the same state snapshot.

**A. Overlap** (the user talks while the bot talks):
- `intent`: Choice of {interrupt, backchannel, side_talk, echo, noise}
- `wants_floor`: Noul
- `correcting_agent`: Noul
- `addressed_to_agent`: Noul

**B. End of turn** (the user goes silent while their turn is open):
- `turn_complete`: Noul
- `addressed_to_agent`: Noul
- `next`: Choice of {respond_now, wait_for_more, ignore}
- `introducing_self`: Noul. It drives the people memory.

**C. Turn start** (only gates multi-person turns):
- `addressed_to_agent`: Noul

The Jev state is a compact JSON snapshot built by `to_jev_state()`:
- `bot`: speaking, current_sentence, spoken_so_far, speaking_ms
- `user`: speaker, speaker_confidence, speaking, speech_ms, silence_ms, partial_transcript, word_count, energy
- `audio.overlap_ms`
- `conversation`: last_user_turn, last_bot_turn
- `vision` (only once lane C's `user_state` arrives): face_present, looking_at_agent (= !gaze_away), wants_turn, confusion_p, nod, face_action_units

Validated live in `backend/tests/live/test_jev_live.py`:

| Utterance | Expected | Jev's answer |
| --- | --- | --- |
| "yeah" / "mm-hm" while the bot talks | CONTINUE | backchannel, confidence 1.0 |
| "no no, I asked about Saturday" | INTERRUPT | interrupt 1.0, correcting 0.92 |
| "honey did you feed the dog" (not looking at the agent) | CONTINUE | side_talk 0.87, addressed 0.11 |
| Bot echo "on Sunday you can expect clear skies" | CONTINUE | echo 0.95 |
| "I want to book a table for" | HOLD | turn_complete 0.13 |
| "Hi, my name is Priya." | RESPOND and bind the name | introducing_self 0.95 |

## Policy

The deterministic rules on top of Jev live in `backend/app/turns/policy.py`:
- Hard-stop phrases ("stop", "wait", "hold on") interrupt without waiting for Jev.
- **Sustained overlap interrupts on duration alone** (`sustained_overlap_interrupt`): speech that keeps
  going past 0.8 s while the bot talks is longer than any backchannel ("yeah", "mm-hm", "right" are all
  shorter), so the bot stops before the transcript arrives. Suppressed if Jev already called this
  utterance a backchannel, and gated on input energy so the AEC residue of the bot's own voice can't
  trigger it. This is what keeps barge-in usable on a cloud ASR that runs ~0.9 s behind.
- If Jev times out or errors, the policy falls back to rules on word count and speech duration.
- An overlap longer than 1.5 s with 3 or more words interrupts, unless Jev is confident it's a backchannel.
- After 2.0 s of silence, the agent responds even if Jev said HOLD.
- Decisions made on stale state are discarded.

## Spoken fillers (perceived latency)

The LLM plus TTS take about 1.2 s to produce the first word of an answer. Instead of silence,
the agent plays a short cached reaction — "Hmm.", "Got it.", "Give me a sec." — in its own voice.

- **Which one:** Jev picks the category (or `none`) as part of the end-of-turn question set, so it
  costs no extra round trip. The deterministic gate is p(none): Jev spreads probability across
  styles because any of them would do, so a top-choice confidence test would almost always say
  "none". See `choose_filler` in `turns/policy.py`.
- **How often:** the gate is deliberately generous (`filler_none_max = 0.6`) — hearing something at ~0.1 s
  beats hearing nothing for ~1 s, so silence is chosen only when Jev is fairly sure of it.
- **Clips:** 49 utterances in 6 categories, generated once by `scripts/make_fillers.py` (Gradium TTS,
  the bot's voice) into `backend/assets/fillers/`, trimmed of lead-in silence on load.
- **Playback:** pushed as output audio **before** the frame that triggers the LLM. Pushed after, the
  clip would queue behind the answer, since the TTS pauses other frames while it generates.
- **Stopping:** no special case. A barge-in pushes `InterruptionFrame`, which drains the output
  queue, so the filler stops with everything else.
- **Measured** (same question, 3 runs each, `scripts/bench_fillers.py`):

  | | First sound the user hears | Answer starts |
  |---|---|---|
  | Fillers on | **0.08 s** | 0.92 s |
  | Fillers off | 1.16 s | 1.16 s |

  Set `ENABLE_FILLERS=0` to turn them off.

## Gaze gating: speech nobody aimed at the agent

With a camera running, the agent stays out of conversations that aren't with it.

- **Rule** (`gaze_blocks_turn`): if faces are in frame and **none** of them are looking at the agent,
  the turn is dropped instead of answered, and an overlap does not interrupt the bot.
- **Not knowing is not a reason to ignore someone.** The gate is off whenever we can't tell: no
  camera, no face in frame, or telemetry older than 2 s. A frozen "looking away" must never deafen
  the agent.
- **Hard-stop words still work.** "Stop" interrupts whether or not anyone is looking.
- **The UI shows what was ignored**, greyed out with the reason, rather than hiding it — otherwise a
  gated agent is indistinguishable from a broken one.

Verified end to end in `scripts/e2e_gaze.py`, which sends the same `faces` telemetry the browser does:
speech ignored while everyone looks away, the same question answered when someone looks, and room
talk failing to interrupt the bot.

## Background agents (telemetry widget)

"Set a timer for ten seconds", "what's Apple trading at", "what was the score" — work the
conversation shouldn't wait for. Jev decides, an agent runs off the conversation path, and the
answer is spoken when it's ready.

- **Deciding:** the `task` question in the end-of-turn fan-out — `none | timer | stock | sports |
  lookup` — so it costs no extra round trip. Live: "Set a timer for ten seconds" → timer (1.0),
  "What was the score in the Lakers game?" → sports (1.0), "Tell me a story" → none (1.0).
  `choose_task` needs 0.55 confidence: spinning up a visible agent on a guess is worse than not.
- **Receipt:** a task turn always plays a cached acknowledging clip immediately (0.38 s), and the
  LLM is told to say only that it's on it, never to invent the answer.
- **Running:** `app/tasks/runner.py` spawns the agent and publishes a `tasks` event on every state
  change. Sources need no API keys: Yahoo Finance for quotes, ESPN (`site.web.api.espn.com`) for
  scores, the General Compute LLM for lookups. A failed agent reports and never breaks the call.
- **Answering:** the result is spoken with `TTSSpeakFrame` **only at a gap** — nobody speaking, no
  turn open, no answer on its way (`_conversation_is_idle`). Until then it waits. It appends to the
  LLM context, so the agent knows what it said.
- **Widget:** the UI lists each agent with kind, title, status, a locally-ticking elapsed clock and
  the result.

Measured end to end (`scripts/e2e_agents.py`): receipt 0.38 s, the conversation answers an unrelated
question while the timer runs, the timer fires at 10.0 s and is spoken, and a stock agent returns a
real price in about 2 s.

## Speaker ID and memory

- **Diarization:** SpeechBrain ECAPA (`speechbrain/spkrec-ecapa-voxceleb`, 192-dim, CPU) plus WhoSpeaksLive's `SpeakerMemory` online clustering. The module is copied verbatim to `backend/app/perception/whospeaks/`.
  - **Capped at 3 voice profiles**: every extra profile is another centroid to score on every live
    window, and a roomful of half-heard voices costs latency on the turn-taking path.
  - Live: `score_existing` every 0.4 s during speech.
  - Final: `classify` on each whole utterance, which creates and updates the S1, S2, ... profiles.
- **Names from context:** the memory pass after each exchange also returns `speaker_name` — the name
  of the person speaking, when the exchange makes it clear. That covers a self-introduction, the
  agent addressing them ("Great, Simone — I'll book it"), or someone in the room using their name.
  A third person merely mentioned ("I met Sarah yesterday") is not taken. An explicit "call me X"
  overwrites an existing name; otherwise names are only filled in when unknown.
- **People memory:** when Jev's `introducing_self` is at least 0.6, General Compute extracts the name as JSON and binds it to the active speaker profile. Profiles, names and facts are saved to `backend/data/memory.json`, so people are recognized in later sessions.
- **Conversation memory:** after each exchange, General Compute updates a rolling summary plus facts per person in the background. That memory goes into the LLM system prompt.

## UI event contract

The server sends events to the browser through RTVI server messages (`rtvi.send_server_message`). Payloads are JSON with a `type` field:

| type | When | Payload |
| --- | --- | --- |
| `state` | about 10 Hz | `state`: phase, bot_speaking, bot_sentence, user_vad, user_speech_ms, user_energy, partial, speaker {label, name, confidence, probabilities}, vision |
| `jev` | every Jev call | `set` (overlap / end_of_turn / turn_start), `answers` {nouls, choices with probabilities}, `latency_ms`, `decision` {action, reason}, `text` |
| `interaction` | turn-taking events | `event`: user_turn_start / user_turn_end / interrupt (with `interrupted: true`) / backchannel / noise / drop / hold / introduction; plus `text`, `reason` and `speaker` |
| `vad` | VAD edges | `speaking`: true or false |
| `turn` | **Contract 2** | `{type: "turn", payload: {kind: partial or final, text, t_speech_end_ms, interrupted}}` |
| `metrics` | **Contract 4** | `{type: "metrics", payload: {...}}` from lane D's `LatencyHUD` |
| `transcript` | final user or bot text | `role`, `text`, `speaker` |
| `memory` | when memory changes | `people`: [{label, name, speech_seconds, facts}], `summary` |
| `tasks` | background agents change | `tasks`: [{id, kind, title, status, started_at, elapsed_s, result, error}] |

Client to server:
- `client.sendClientMessage("user_state", <Contract 1>)` at about 10 Hz (lane C)
- `client.sendClientMessage("reset_memory", {})`, which the memory panel's "Forget everyone" button sends

## Layout

```
backend/                 uv project (Python 3.12, pipecat-ai 1.11)
  app/config.py          .env → Settings
  app/state/             InteractionState, StateEngine
  app/jev/               JevClient, question sets, serializer
  app/turns/             policy.py (pure), controller.py (Pipecat processor)
  app/perception/        speaker_id.py (ECAPA + WhoSpeaksLive SpeakerMemory), bot_tap.py, vision
  app/memory/            people + conversation memory (General Compute)
  app/providers/         raw Gradium websocket clients (tests and fixtures)
  app/bot.py, server.py  pipeline + SmallWebRTC server
  scripts/               smoke_providers.py, e2e_headless.py
  tests/unit             pure tests (no network)
  tests/live             real Jev / Gradium / General Compute (pytest -m live)
frontend/                Vite + React + @pipecat-ai/client-js + MediaPipe Face Landmarker
```

## Running

```bash
cd backend
uv sync
uv run python scripts/smoke_providers.py       # checks every key + latency
uv run pytest                                   # unit tests
uv run pytest -m live tests/live -s             # real-provider tests
uv run python -m app.server                     # backend on :7860 (once the pipeline lands)
cd ../frontend && npm install && npm run dev    # web app (once it lands)
```

## Build status

- [x] Provider smoke tests: Jev, Gradium TTS/STT, General Compute
- [x] InteractionState, StateEngine, Jev question sets and serializer
- [x] Policy (pure, table-tested) plus the InteractionController (Pipecat processor) with a fake-Jev test suite
- [x] Pipecat 1.11 pipeline and SmallWebRTC server (`app/bot.py`, `app/server.py`)
- [x] Speaker ID: ECAPA plus WhoSpeaksLive SpeakerMemory, with live and final decisions and stitching of unknown utterances
- [x] People memory, with names bound from Jev's `introducing_self`, and a rolling summary from General Compute. Voice profiles persist across sessions.
- [x] Contracts at the edges: Contract 2 `turn` (partial and final, `interrupted`) and Contract 4 `metrics`, through lane D's `LatencyHUD` and `UserBotLatencyObserver`. Contract 1 `user_state` is consumed into the Jev state.
- [x] Jev key is optional: `NullJev` means the deterministic policy fallbacks decide.
- [x] Web panels (`frontend/src/jev/`): who is speaking with per-speaker probabilities, Jev probability bars with policy thresholds, a turn-taking timeline, and people/summary memory
- [x] MediaPipe face features (lane C's module, ported): mesh, irises and a gaze arrow drawn for **every**
      face in frame (up to 4). Contract 1 `user_state` still describes one person (the largest face); a
      separate `faces` message carries per-face gaze, and Jev's state gets `people_in_frame` and
      `people_looking_at_agent` — the signal that separates "talking to me" from "talking to each other".
- [x] Transcript credits the recognized speaker and the agent by name, not "YOU"/"AGENT"

### Latency budget (end of speech → first audio, from the live HUD)

| | Before | After |
|---|---|---|
| Median over a session | 1830 ms (82 turns) | **516 ms** (8 of 18 turns under 500 ms, best 361 ms) |

What changed, largest first:
- **Spoken fillers** are the first sound on most turns, so the wait for the LLM stops being silence.
- **Jev's end-of-turn budget 0.6 s → 1.2 s.** A timed-out ask cost ~2 s, because the turn then fell back
  to waiting for the final transcript. Barge-in asks keep the short budget: they are re-issued on the next word.
- **Jev's hold timeout 2.0 s → 1.1 s.** 14 of 82 turns had been paying the full 2 s after Jev judged a turn incomplete.
- **Endpointing 300 ms → 200 ms** (Silero `stop_secs`), with Jev's end-of-turn judgement as the safety net.
- **Shorter system prompt and capped memory block**, so fewer prompt tokens precede the first token.
- **`gpt-oss-120b`** replaced `minimax-m2.7` (559 ms vs 1457 ms TTFT on a demo-shaped prompt).

The floor for a *generated* answer is about 0.9 s: LLM time to first token (~0.56 s) plus TTS (~0.25 s) plus
endpointing. Getting the first *sound* under 200 ms is what the cached fillers do.

### Measured, with the WebRTC end-to-end test (aiortc client, Gradium-voiced user)

| What | Result |
| --- | --- |
| Jev p50 | 140–270 ms |
| Barge-in | decision **~0.85 s after the user's first syllable**, bot audio stops ~0.15 s later. The floor is Gradium's ~0.9 s transcript lag, which the duration backstop sidesteps. |
| First sound after a question (filler) | **0.08–0.16 s** |
| Backchannel ("Yeah.") while the bot talks | Classified `backchannel`, and the bot keeps talking |
| Barge-in ("Actually, can you make it about a cat instead?") | Bot audio stops **0.86–1.1 s after the user starts speaking**. About 0.9 s of that is Gradium's first-word latency (delay_in_frames=7); Jev adds about 0.25 s. Hard-stop words skip Jev. |
| End of speech to first bot audio | 1.2–2.5 s. The stages are VAD 0.3 s, ASR catch-up about 0.4 s, Jev about 0.2 s on the partial transcript (early end of turn), LLM about 0.5 s and TTS about 0.35 s. |
| Two different voices | Separated into S1 and S2 (ECAPA same-voice cosine 0.67–0.83, different voices 0.19–0.33). Both names were bound. |

### Tests

```bash
cd backend
uv run pytest                                            # 60 unit + e2e-scenario tests (fake Jev, no network)
uv run pytest -m live tests/live -s                      # 12 real-Jev decision tests
uv run python scripts/make_fixtures.py                   # Gradium-voiced user clips (once)
uv run python scripts/make_fillers.py                    # cached filler clips (once; committed already)
DEV_FIXTURES=1 uv run python -m app.server &             # server + built frontend on :7860
uv run python scripts/e2e_webrtc.py                      # real WebRTC end to end: intro, backchannel, barge-in, 2 speakers
```
