# Team & Agent Coordination

Source of truth for humans and agents. Tasks and blockers live in **GitHub Issues**; everything else lives here. Changes to this file go through a PR like any other change.

## Mission and demo target

We ship a live voice agent that notices a user getting confused mid-explanation, stops, and repairs it, running end to end on SambaNova by 6:00 PM.

- **Demo moment:** the agent explains something, the user frowns and hesitates, and the agent backs up with a non-leading question ("which part should I go over again?").
- **Prize target:** Most Technical Implementation. Technical depth counts double; end of speech to first word should stay under 500 ms.
- **Hard constraints:** primary inference on SambaNova (General Compute SN40 endpoints), shown in code and in the demo. Video or slides alone cap us at Prototype, so the demo runs live.
- **Out of scope:** microexpression detection, deception claims, anything judges would read as pseudoscience.

**Submission:** register on hackathon.new, link this repo (no repo, not judged), add a demo video or live demo plan. One submission per team; teams are 2–4 people. Deadline **6:00 PM sharp**. The repo must be public by then.

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
