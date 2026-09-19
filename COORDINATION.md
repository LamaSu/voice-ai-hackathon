# Team & Agent Coordination

Source of truth for humans and agents. Tasks and blockers live in **GitHub Issues**; everything else lives here. Changes to this file go through a PR like any other change.

## Mission and demo target

We ship a live voice agent that notices a user getting confused mid-explanation, stops, and repairs it, running end to end on SambaNova by 6:00 PM.

- **Demo moment:** the agent explains something, the user frowns and hesitates, and the agent backs up with a non-leading question ("which part should I go over again?").
- **Prize target:** Most Technical Implementation. Technical depth counts double; end of speech to first word should stay under 500 ms.
- **Hard constraints:** primary inference on SambaNova (General Compute SN40 endpoints), shown in code and in the demo. Video or slides alone cap us at Prototype, so the demo runs live.
- **Out of scope:** microexpression detection, deception claims, anything judges would read as pseudoscience.

**Submission:** register on hackathon.new, link this repo (no repo, not judged), add a demo video or live demo plan. One submission per team; teams are 2–4 people. Deadline **6:00 PM sharp**. The repo must be public by then.

## Status board — 16:05, Sat Sept 19

Updated by the lane D agent. Replace this block wholesale at each standup; do not let it go stale.

**One-line state:** everything is built and merged; `main` is green and carries the whole demo path.
The one thing standing between us and the prize we are targeting is **latency: ~3 s live against a
1.5 s "phone tree" line.** Eligibility (#15) is still unverified.

| Checkpoint | Due | State |
| --- | --- | --- |
| Walking skeleton | 1:00 PM | ✅ done (late) |
| `user_state` flowing, HUD, Gradium usage | 2:30 PM | ✅ HUD live; `user_state` reaching the reasoner. Gradium credit usage still unchecked. |
| Staged confusion → back up and probe | 3:30 PM | 🟡 trigger + picker merged (#5, #6). Needs a live run to confirm the repair fires on stage. |
| Feature freeze | 4:30 PM | **now** |
| Dress rehearsal + backup recording | 5:15 PM | ⬜ see `docs/DEMO_RUNBOOK.md` |
| Repo public, README, submitted | 5:45 PM | ⬜ **ask rg before making the repo public** |

### Latency — the one number that matters now

Live is **~3 s** end of speech to first audio. Findings, so nobody re-derives them:

- **`delay_in_frames=7` is already the floor** (Gradium allows 7, 8, 10, 12…; default 12). That is a
  **560 ms inherent ASR lookahead** we cannot configure away. `VADParams(stop_secs=0.3)` is likewise
  already tight. **There is no quick transcription win.**
- **The LLM is not the long pole.** minimax TTFT ≈ 0.4 s of ~3 s. Inference going to zero still leaves
  ~2.5 s. Time is in speech synthesis, text aggregation, and that ASR floor.
- **Therefore SambaNova will not fix this.** We already run on whatever hardware General Compute uses;
  #15 is an *eligibility* fact, not a performance lever. Answering it changes no number.
- `scripts/where_time_goes.py` ranks the stages from `metrics.jsonl`. **Run it before changing
  anything** — it takes 30 s and stops us optimising the wrong row.
- Structural win, already half-built and still open: **#7 / B4, reason on partial transcripts.**
  `InteractionController._early_eot()` already responds on interim text, but only when Jev returns
  RESPOND; otherwise we wait `EOT_FINAL_WAIT_S = 1.0` for Gradium's final.
- Not for today: Gradium STT has **native turn detection** (`enable_turn_detection`) we don't use,
  running Silero VAD + Jev + a 1 s wait instead. Collapsing those is the right post-event answer.

### Known limits, so we describe them accurately at the table

- **One face only.** `numFaces: 1` in `frontend/src/gaze/index.ts`, and only `faceBlendshapes[0]` is
  read. Contract 1 has no multi-face representation either, so this is a contract change, not a flag.
- **Second speakers need time.** `min_new_speaker_seconds=1.5`, `late_new_speaker_min_seconds=2.5`.
  A short interjection from a second voice gets absorbed into the first speaker rather than getting
  its own profile.

### Open, and needing a human rather than an agent

1. **#15 — is `minimax-m2.7` served on SN40/SN50?** Unanswered by everyone asked; the API does not say.
   Someone at the General Compute table has to answer it in writing. Fast and ineligible scores zero.
2. **Gradium credits.** 145,000 on a Free plan with overages off — service simply stops when they run
   out. Nobody has checked the balance since kickoff, and the rehearsal will spend some.

## Getting started

New here? Read [ONBOARDING.md](ONBOARDING.md) first — it has the live status, the open decisions and
the lane boundaries. Then, in order:

1. **Accept your repo invite.** The repo is private until submission; the invite is at
   https://github.com/LamaSu/voice-ai-hackathon/invitations. Nothing works until you accept.
2. **Take a lane.** Three people, so lane C folds into lane B: one person on A, one on B + C, rg on D.
   Pick one open issue with your lane's label, comment `claimed by <name>`, add `in-progress`, and only
   then start. Two agents already duplicated work by skipping this.
3. **Get the keys.** `GENERAL_COMPUTE_API_KEY` and `GRADIUM_API_KEY` come **by DM only** — never in an
   issue, a PR, a commit or an agent prompt. They live in `.env`, which is git-ignored, and nowhere else.
4. **Set up.**
   ```bash
   cp .env.example .env            # paste the keys you were DM'd
   cd backend && uv sync && uv run pytest
   cd ../frontend && npm install && npm run dev
   ```
5. **Install the docs server once per machine**, and query it before guessing a Pipecat API:
   ```bash
   uv tool install "pipecat-ai[cli]"
   pipecat context-hub install
   ```
6. **A1 comes first.** Until the walking skeleton runs end to end, every other lane is writing against
   something it cannot test. If A1 is blocked, help unblock it before starting your own task.

Gradium is on rg's account: 145,000 credits, Free plan, **overages off**, so the service simply stops
when the credits run out. Keep automated tests on short clips, and use `frontend` replay mode
(`?replay=1`) rather than a live call when you only need to see the HUD.

## Lanes

Each lane has one human owner who directs its agents and merges their PRs. Agents never merge to `main` or work outside their lane.

| Lane | Label | Human owner | Owns | Branch prefix |
| --- | --- | --- | --- | --- |
| A. Voice pipeline | `lane:A` | TBD | Pipecat pipeline, VAD, turn detection, interruptions, TTS | `voice/` |
| B. Reasoning | `lane:B` | TBD | SambaNova calls, prompts, confusion hypotheses, probe-question picker | `brain/` |
| C. Perception | `lane:C` | TBD | Browser face tracking, prosody, feature fusion | `sense/` |
| D. Integration and demo | `lane:D` | rg | Frontend, latency HUD, end-to-end tests, demo script, submission | `demo/` |

With three people, merge C into B.

## Architecture and contracts

Lanes talk only through these contracts. Changing one needs a Decision log entry and a heads-up to every lane owner.

```mermaid
flowchart LR
  Mic[Mic audio] --> STT[STT + VAD<br/>Lane A]
  Cam[Webcam] --> Face[Face features<br/>Lane C, client]
  Mic --> Pros[Prosody<br/>Lane C]
  STT --> Brain[Reasoner on SambaNova<br/>Lane B]
  Face --> Fuse[State fusion<br/>Lane C]
  Pros --> Fuse
  Fuse --> Brain
  Brain --> TTS[TTS<br/>Lane A]
  TTS --> Spk[Speaker]
```

Face tracking runs in the browser and sends numbers, not video.

**Contract 1: `user_state` (C → B), ~10 Hz.** Values are deltas from the user's own baseline, captured in the first 30 seconds.

```json
{"t_ms": 1234567, "au": {"brow_lower": 0.42, "lip_press": 0.1}, "gaze_away": false, "nod": 0, "wants_turn": false, "prosody_delta": {"pitch": 0.3, "rate": -0.2, "pause_ms": 800}, "confusion_p": 0.61}
```

**Contract 2: `turn` (A → B)** — `partial` or `final` transcript text, `t_speech_end_ms`, and `interrupted: true` when the user speaks over the agent.

**Contract 3: `speak` (B → A)** — streamed text chunks, a `style` hint for TTS, and `cancel` when re-planning.

**Contract 4: `metrics` (all → D)** — stage timestamps so the latency HUD shows end of speech to first audio.

## Timeline and checkpoints

Features freeze at 4:30 PM. After that, only fixes and rehearsal.

| Time | Checkpoint | Exit criteria |
| --- | --- | --- |
| 11:30 AM | Kickoff done | Lanes staffed, contracts agreed, agents loaded with CLAUDE.md |
| 1:00 PM | Walking skeleton | Voice in, SambaNova reply, voice out, end to end |
| 2:30 PM | Signals flowing | `user_state` reaches the reasoner; latency HUD shows real numbers; Gradium usage checked |
| 3:30 PM | Repair loop works | Staged confusion triggers a back-up and a probe question |
| 4:30 PM | Feature freeze | All P0 issues closed; everything else cut or behind a flag |
| 5:15 PM | Dress rehearsal | Two clean live runs; backup recording saved |
| 5:45 PM | Submit | Repo public, README done, 15 minutes of buffer |
| 7:30 PM | Judging | Demo driver and a technical explainer at the table |

Each checkpoint is a 5-minute human standup: each lane owner reports green, yellow, or red.

## Decision log

Newest first. Any change to a contract, lane scope, or the demo script goes here before code.

| When | Decision | Why | By |
| --- | --- | --- | --- |
| Sept 19, 16:05 | **Proposed, not built: pre-rendered filler audio to mask latency.** rg's idea — the agent says "Hmm, let me think" the instant a turn is accepted, while the real answer streams behind it. **The catch that decides whether it works:** a filler routed through normal TTS buys nothing, because Gradium synthesis is our top cost — it would arrive as late as the real answer. It only works pre-rendered to WAV at startup and pushed as `OutputAudioRawFrame`, bypassing TTS; `TTSSpeakFrame` re-enters TTS and does not help. Optional second Jev call picks a category (acknowledging / weighing / reframing / hedging) at ~145 ms p50; reframing fillers are strongest for us because they double as the repair behaviour. | It masks rather than reduces latency, which is legitimate and standard for voice agents — but if we ship it we must tell judges the first audio is a filler, or the metric means something other than it appears to. Needs a threshold so a fast reply isn't slowed, and a wrong-toned filler reads worse than silence. Est. 45 min including testing, i.e. past the 4:30 freeze and on the demo path. | rg (proposed); build deferred |
| Sept 19, 14:20 | Build on the Jev-driven interaction engine (`voice/1-jev-interaction-engine`), not the stock Pipecat quickstart | Most Technical Implementation counts depth double. A custom interruption-first controller with a deterministic policy layer, typed fan-out decisions and graceful Jev-unavailable fallbacks is a far stronger submission than the quickstart, and it already exists with 27 green unit tests. Rebuilding a simpler path would cost hours we do not have before the 4:30 freeze. | rg (delegated to lane D agent) |
| Sept 19, 14:20 | **Open:** Contracts 1–4 vs the engine's `InteractionState` | The engine implements none of the four cross-lane contracts. Per rule 3 this needs a `contract-change` issue and every lane owner's sign-off before it lands on `main`. Until then, anything crossing a lane uses `backend/app/contracts.py`. | pending rg |
| Sept 19, 14:20 | **Open:** which LLM | Measured TTFT is gemma-4-31B-it ≈ 3.5 s vs minimax-m2.7 ≈ 0.4 s (#16), so the model named in the quickstart cannot meet the latency target. Blocked on #15: whichever we pick must be confirmed as SambaNova-served, or we lose eligibility, which costs more than the latency. | pending rg |
| Sept 19 | GitHub repo is the coordination hub; issues are the task board | Everyone and every agent already has access | rg |
| Pre-event | Face features via MediaPipe on the client; no microexpressions | Webcams are too slow for them and the science is weak | rg |
| Pre-event | Emotion signals are a prior, confirmed by probe questions | Readings are noisy; mismatches are the signal | rg |

## Demo script (~3 min, latency HUD on screen)

1. **Hook (20 s):** "Voice agents either think or talk fast. Ours does both, and notices when you're lost."
2. **Calibration (20 s):** small talk while the baseline is captured.
3. **Explanation (60 s):** the agent explains a hard concept; the driver frowns and hesitates.
4. **Repair (30 s):** the agent stops mid-sentence, asks a non-leading question, re-explains.
5. **Barge-in (20 s):** the driver interrupts; the agent stops instantly and adapts.
6. **Under the hood (30 s):** SambaNova endpoint in code, the `user_state` stream, measured latency.

Before judging:

- [ ] SambaNova endpoint visible in code and on screen
- [ ] Median end of speech to first audio measured; under 500 ms, or the honest number with a reason
- [ ] Survives bad audio and interruption in the live run
- [ ] Backup recording ready, used only if the live run fails
- [ ] One-line answer for "is emotion detection reliable?": it's a prior we confirm by asking
