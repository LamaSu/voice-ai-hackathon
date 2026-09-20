# 3Jeffs — team & agent coordination

Source of truth for humans and agents. Tasks and blockers live in **GitHub Issues**; everything else lives here. Changes to this file go through a PR like any other change.

## Mission and demo target

We ship a live voice agent that notices a user getting confused mid-explanation, stops, and repairs it, running end to end on SambaNova by 6:00 PM.

- **Demo moment:** the agent explains something, the user frowns and hesitates, and the agent backs up with a non-leading question ("which part should I go over again?").
- **Prize target:** Most Technical Implementation. Technical depth counts double; end of speech to first word should stay under 500 ms.
- **Hard constraints:** primary inference on SambaNova (General Compute SN40 endpoints), shown in code and in the demo. Video or slides alone cap us at Prototype, so the demo runs live.
- **Out of scope:** microexpression detection, deception claims, anything judges would read as pseudoscience.

**Submission:** register on hackathon.new, link this repo (no repo, not judged), add a demo video or live demo plan. One submission per team; teams are 2–4 people. Deadline **6:00 PM sharp**. The repo must be public by then.

## Closing the loop — 18:40

Everything merged: #22, #24, #25, #26. `main` is `665aff4`, **154 passing**, and
`preflight --live` reported **GO** on the demo machine. The repo is public.

### Last gap, now fixed (PR #27): the face never reached the reasoner

The face could already **stop** the agent (`decide_probe`) and tell Jev whether it was being
addressed. But the model *writing the reply* had never seen a single face signal — the LLM system
prompt was built from the memory block, who is speaking, and a background-job note, and nothing
else. So the agent could notice you were lost and then re-explain **identically**. Half the idea.

`app/listener_note.py` turns the live `VisionState` into one short line appended to the system
prompt. What the model actually receives:

> Live read on the listener: the person you are talking to is showing strong signs of being lost
> and has looked away. Therefore: back up to the last point they clearly had, re-explain it a
> different way with a concrete example, and keep it to two sentences. Do not mention their face,
> expression or body language, and do not ask whether they are confused — just adjust how you
> explain.

Three decisions worth knowing:

- **The agent may never mention the face.** "You look confused" is leading, invites a reflexive
  "no, I'm fine", and is exactly the emotion-detection claim we disowned in §2 of the architecture.
  The signal changes *how* it explains, never what it claims to know about you.
- **Guidance, not telemetry.** `brow_lower 0.42` is useless to a model mid-sentence; "slow down and
  use a concrete example" is actionable.
- **Silence when there is nothing to say.** A calm listener produces no note, and a reading older
  than 2 s is dropped — a frozen value describes a moment that has passed. Prompt tokens are
  time-to-first-token.

`ENABLE_LISTENER_NOTE=0` turns it off if it misbehaves in a run.

### Still open, and no agent can close it

**#15 — is the model we are running served on SN40/SN50?** Three model changes today, still
unconfirmed. This was the eligibility gate all along.

## Status board — 16:55, Sat Sept 19

Updated by the lane D agent. Replace this block wholesale at each standup.

**One-line state:** `main` finally carries the whole demo path and is green. Latency is fixed
(1830 ms → ~500 ms). Two live bugs remain, one fixed and unmerged. **Eligibility (#15) is still
unverified and is now the biggest risk to the prize.**

| Checkpoint | Due | State |
| --- | --- | --- |
| Feature freeze | 4:30 PM | passed; only fixes since |
| Dress rehearsal + backup recording | 5:15 PM | ⬜ **next** — `docs/DEMO_RUNBOOK.md`, gate on `./scripts/preflight.sh --live` |
| Repo public, README, submitted | 5:45 PM | ⬜ **ask rg before making the repo public** |

### Merged into `main` (9a84b57), verified green: 105 backend, 25 frontend

- **Latency 1830 ms → ~500 ms median** (best 361 ms): spoken fillers, Jev hold timeout 2.0→1.1 s,
  end-of-turn budget 0.6→1.2 s, `gpt-oss-120b` for `minimax-m2.7`, shorter prompt.
- Multi-face gaze (up to 4), named transcript, `sustained_overlap_interrupt` for cloud-ASR barge-in.
- Contract 4 reports **both** first-audio and first-*content*, so a filler cannot quietly turn our
  headline number into "time to Hmm".
- Session telemetry + `scripts/diagnose.py`, `scripts/where_time_goes.py`, `scripts/preflight.sh`,
  `docs/DEMO_RUNBOOK.md`.

### Open bug, fix ready and unmerged

**The agent speaks its own reasoning.** `gpt-oss-120b` streams chain of thought inline in `<think>`
tags — Pipecat's Groq service documents exactly this for the GPT-OSS family — and nothing stripped
it, so it reached TTS. `ReasoningFilter` on the TTS service removes it. It is stateful on purpose:
streaming splits `<think>` across chunks and a per-chunk regex leaks the very text it removes.
Deliberately **not** done: passing `reasoning_effort` to the API. It cannot be tested from here and
an unsupported parameter would 400 every request and take the demo down.

### Known limits — describe these accurately rather than be caught out

- **The answer floor is ~0.9 s** (TTFT + TTS + endpointing). Below that the honest answer is a
  cached filler, not a faster model. Quote **both** HUD numbers.
- **TTFT moves with provider load.** Our two measurements disagree about which model is faster
  (#16 had minimax ahead, #21 has gpt-oss ahead). Re-run `scripts/latency_check.py --trials 9`
  close to judging rather than quoting an hour-old figure.
- **Confusion reaches Jev but no Jev question asks about it.** `confusion_p` is in the state block;
  the repair trigger is deterministic (`decide_probe`/`ConfusionTracker`). Accurate answer: the
  model reasons about *whether you are addressing it* from gaze; the confusion trigger is code.
- Second speakers need 1.5–2.5 s of speech before getting their own voice profile.

### Still needing a human, not an agent

1. **#15 — is any General Compute model served on SN40/SN50?** We have now shipped on three
   different models and confirmed nothing. This is the eligibility gate: fast and ineligible scores
   zero, slow and eligible still places. **Highest-value hour of human time left.**
2. **Gradium credits.** 145,000, Free plan, overages off — service stops dead when they run out, and
   the rehearsal will spend some. Nobody has checked the balance since kickoff.

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
| Sept 19, 15:35 | C3: derive prosody (pitch/rate/pause) on-device from the mic track already open, not Hume | No new vendor, no new key, no extra network call on the speech path — which matters with median end-of-speech-to-first-audio already over the 1.5s budget. Accuracy is lower than a dedicated emotion-from-voice API, but it's a prior confirmed by probe questions either way (see the confusion_p entry below), so the lower bar is acceptable. | claude-sense (lane C, #10) |
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
