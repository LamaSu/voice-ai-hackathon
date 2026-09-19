# Architecture: Jev-driven, interruption-first multimodal voice agent

> Status: **in progress**. The provider layer, state model, Jev question sets and policy are built and tested. The Pipecat pipeline, controller, speaker ID and frontend are being wired now; see [Build status](#build-status).

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
| ASR | Gradium `wss://api.gradium.ai/api/speech/asr` via `pipecat.services.gradium.stt.GradiumSTTService` | Needs a language (`en`). Interim text streams continuously. The final transcript arrives after a VAD-stop flush. |
| TTS | Gradium `wss://api.gradium.ai/api/speech/tts` via `GradiumTTSService` | 48 kHz PCM with word timestamps. First audio in about 0.4–1.0 s. |
| LLM | General Compute `https://api.generalcompute.com/v1` via Pipecat `OpenAILLMService(base_url=...)` | Time to first token: **minimax-m2.7 ≈ 0.4 s**, gpt-oss-120b 1.6–6.4 s, gemma-4-31B-it ≈ 3.5 s. Default: `minimax-m2.7`, overridable with `LLM_MODEL`. |
| Interaction decisions | Jev `POST https://api.typesafe.ai/v1/systemone`, model `jev-latest` (currently jev-1.13.0), via `typesafe-sdk` | p50 ≈ 145 ms for a 2–4 question fan-out (range 90–470 ms). Hard timeout of 600 ms, then deterministic fallback. |

## Pipeline (Pipecat 1.11)

```
transport.input()            SmallWebRTC; the mic stays open while the bot talks (full duplex); browser AEC
 → RTVIProcessor             (added by PipelineWorker) client messages {type:"gaze"} → VisionState
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

**Interruption** = `ProposedUserStartedSpeakingFrame`. The External start strategy broadcasts an `InterruptionFrame`, which cancels the in-flight LLM request, drops the TTS audio context, and drains the output transport's audio queue. These are the three things that must stop.

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
- `vision` (only when the camera is on): face_present, looking_at_agent, gaze_confidence, head_yaw_deg, head_pitch_deg

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
- If Jev times out or errors, the policy falls back to rules on word count and speech duration.
- An overlap longer than 1.5 s with 3 or more words interrupts, unless Jev is confident it's a backchannel.
- After 2.0 s of silence, the agent responds even if Jev said HOLD.
- Decisions made on stale state are discarded.

## Speaker ID and memory

- **Diarization:** SpeechBrain ECAPA (`speechbrain/spkrec-ecapa-voxceleb`, 192-dim, CPU) plus WhoSpeaksLive's `SpeakerMemory` online clustering. The module is copied verbatim to `backend/app/perception/whospeaks/`.
  - Live: `score_existing` every 0.4 s during speech.
  - Final: `classify` on each whole utterance, which creates and updates the S1, S2, ... profiles.
- **People memory:** when Jev's `introducing_self` is at least 0.6, General Compute extracts the name as JSON and binds it to the active speaker profile. Profiles, names and facts are saved to `backend/data/memory.json`, so people are recognized in later sessions.
- **Conversation memory:** after each exchange, General Compute updates a rolling summary plus facts per person in the background. That memory goes into the LLM system prompt.

## UI event contract

The server sends events to the browser through RTVI server messages (`rtvi.send_server_message`). Payloads are JSON with a `type` field:

| type | When | Payload |
| --- | --- | --- |
| `state` | about 10 Hz | `state`: phase, bot_speaking, bot_sentence, user_vad, user_speech_ms, user_energy, partial, speaker {label, name, confidence, probabilities}, vision |
| `jev` | every Jev call | `set` (overlap / end_of_turn / turn_start), `answers` {nouls, choices with probabilities}, `latency_ms`, `decision` {action, reason}, `text` |
| `turn` | turn events | `event`: user_turn_start / user_turn_end / interrupt / backchannel / drop / hold; plus `text` and `speaker` |
| `transcript` | final user or bot text | `role`, `text`, `speaker` |
| `memory` | when memory changes | `people`: [{label, name, speech_seconds, facts}], `summary` |

Client to server: `client.sendClientMessage("gaze", {face_present, looking_at_agent, gaze_confidence, gaze_x, gaze_y, head_yaw, head_pitch, head_roll})` at about 10 Hz.

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
- [x] Policy, with 27 unit tests and 12 live Jev tests
- [ ] Pipecat pipeline, server and InteractionController
- [ ] ECAPA speaker ID, people memory and conversation memory
- [ ] Frontend: speaker panel, Jev probability bars, turn timeline, memory panel, MediaPipe gaze
- [ ] Headless end-to-end interruption test using Gradium-generated audio fixtures
