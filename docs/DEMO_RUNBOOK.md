# Demo runbook — D3

Two clean rehearsal runs by 5:15, a backup recording saved, and a plan for when
something breaks in front of a judge. Keep this open on a second screen.

## Roles

Three jobs. One person can hold two, but **not** driver and operator.

| Role | Who | Does |
| --- | --- | --- |
| **Driver** | the person on camera | Talks to the agent. Gets visibly confused on cue. Interrupts on cue. |
| **Operator** | laptop owner | Starts the stack, watches the HUD and logs, restarts if needed. Never speaks during a run. |
| **Explainer** | anyone | Answers the judges. Owns the three answers at the bottom of this page. |

## Before anything (5 min)

```bash
./scripts/preflight.sh          # keys, deps, tests, build, providers
```

Then, with the bot running:

```bash
cd backend && uv run python -m app.server     # serves UI + API on :7860
./scripts/preflight.sh --live                 # WebRTC e2e + latency verdict
```

**NO-GO means do not start a rehearsal run.** Fix the FAIL lines first; a run on
a broken stack teaches nothing and burns Gradium credits.

Physical checks the script cannot do:

- [ ] **Headphones on the driver.** Without them the bot hears itself and barges in on its own voice. This is the single most common way this demo fails.
- [ ] Camera sees the driver's face, evenly lit, no strong backlight
- [ ] Browser has granted mic **and** camera; the camera preview is live
- [ ] You can hear the bot — if the transcript moves and there is no sound, look for the **"click to enable sound"** button; browsers block autoplay until a gesture
- [ ] Phone on silent, notifications off, second monitor mirrored for judges

## The run (~3 min)

From the demo script in `COORDINATION.md`. The driver should rehearse the two
cues until they feel natural — a forced frown reads as acting.

| # | Beat | Time | Driver does | What to watch |
| --- | --- | --- | --- | --- |
| 1 | Hook | 20 s | "Voice agents either think or talk fast. Ours does both, and notices when you're lost." | — |
| 2 | Calibration | 20 s | Small talk. Sit still, face the camera. | Baseline captured; `user_state` ticking in the panels |
| 3 | Explanation | 60 s | Ask for something hard. Listen. Then **frown, look away, go quiet.** | `confusion_p` climbing |
| 4 | **Repair** | 30 s | Stay quiet. Let it work. | **Bot stops mid-sentence and asks a non-leading question.** This is the moment. |
| 5 | Barge-in | 20 s | Interrupt mid-sentence: "wait — go back to the first part" | Bot audio stops fast; HUD shows the turn |
| 6 | Under the hood | 30 s | Hand to the explainer | SambaNova endpoint in code, `user_state` stream, HUD median |

**Beat 4 is the demo.** If beats 3–4 work and nothing else does, we still have a
submission. If everything else works and beat 4 doesn't, we don't.

## When it breaks

Decide in **five seconds**, out loud, then act. Do not debug in front of a judge.

| Symptom | Almost certainly | Do this |
| --- | --- | --- |
| Transcript moves, no sound | Browser blocked autoplay | Click **"enable sound"**. Still nothing: reload, reconnect. |
| Bot interrupts itself constantly | Driver has no headphones | Headphones on. Restart the run. |
| Bot talks over the driver | Turn-taking, or Jev timed out | Let it finish, carry on. Mention the deterministic fallback — it's a feature. |
| No repair at beat 4 | `confusion_p` never crossed | Hold the frown ~3 s longer. Second failure: say "let me show you the signal" and demo the panels instead. |
| Face not detected | Lighting, or MediaPipe assets failed | Check the panel readout. If tracking is down, say so and run the voice-only demo. |
| Long silence after speaking | Provider slow or wedged | Wait 3 s. Then reconnect. Then restart the server. |
| Total wedge | — | **Switch to the backup recording.** Say "here's the run we recorded earlier" and keep talking. |

**Kill criteria:** two failed live attempts, or any single hang over ~20 seconds
→ go to the recording. A confident cut to video beats a third attempt.

## Backup recording

Record this **before 5:15**, from a NO-GO-free preflight. It is insurance, not
the submission — a recording alone caps us at Prototype.

1. Full screen, browser with the call UI, HUD visible throughout.
2. Screen record **with system audio plus mic** (macOS: QuickTime → New Screen Recording, pick the mic; check audio afterwards).
3. Do the full 6-beat run. **Do not stop for small mistakes** — one continuous honest take beats a polished edit.
4. Save to `recordings/` (git-ignored) and **check it plays back with sound.**
5. Record a second take if the first has no audible repair moment.

Have it open in a background tab before judging, cued to the start.

## The three answers

Agree these now so nobody improvises at the table.

**"What's your latency?"**
Quote the **median from the HUD**, not the best sample. As of the last
measurement it's about **1.6 s** end of speech to first audio, with barge-in
around 0.9–1.1 s. That is over the 500 ms conversational target and we should
say so plainly, then say where it goes: endpointing wait, transcription, LLM,
synthesis — the HUD breaks it down live on screen. Being able to show the
breakdown is worth more than a flattering number.

**"Is emotion detection reliable?"**
No, and we don't treat it as reliable. `confusion_p` is a **prior, not a
verdict** — it decides *when to ask*, never what the user is feeling. The agent
confirms by asking a non-leading question. That's the whole design: mismatches
between the reading and the answer are the signal. We do not do
microexpressions or deception inference; the science is weak and we left it out
deliberately.

**"Where's SambaNova?"**
Show the endpoint in `backend/app/config.py` and the model in use. ⚠️ **As of
writing, nobody has confirmed which General Compute models run on SN40/SN50
(#15).** If that's still open at judging, say exactly that — we run on General
Compute's endpoints and understand those to be SambaNova-served. Do not claim
more than we've verified; an overclaim a judge catches costs more than the
uncertainty.

## After

- [ ] Backup recording saved and plays with sound
- [ ] `metrics.jsonl` has real turns; median noted for the README
- [ ] Repo public (**ask rg first**), README done, submitted on hackathon.new before 6:00
