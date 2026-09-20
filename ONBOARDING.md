# 3Jevvs — onboarding, read this before your first commit

For an agent (or human) joining the team mid-build. Written 14:25, Sat Sept 19.
Submission closes **6:00 PM sharp**. Read this, then `COORDINATION.md`, then `CLAUDE.md`.

## Where we actually are

| Checkpoint | Due | State |
| --- | --- | --- |
| Walking skeleton (voice in → reply → voice out) | 1:00 PM | **Late.** Provider layer, state model, Jev policy built and unit-tested. The Pipecat pipeline and controller are not wired yet, so nothing talks end to end. |
| `user_state` to the reasoner, latency HUD, Gradium usage | 2:30 PM | **Partly.** HUD is built and verified (D1). Nothing consumes `user_state` yet. Gradium usage not checked. |
| Staged confusion → back up and probe | 3:30 PM | **Not started.** This is the demo. See "biggest risk". |
| Feature freeze | 4:30 PM | — |
| Dress rehearsal + backup recording | 5:15 PM | — |
| Repo public, README, submitted | 5:45 PM | — |

## The one decision that shapes everything

We run **Akash's Jev-driven interaction engine** (branch `voice/1-jev-interaction-engine`), not the
stock Pipecat quickstart. rg delegated the call; the reasoning is in the Decision log in
`COORDINATION.md`. Short version: the prize is Most Technical Implementation, technical depth counts
double, and a custom interruption-first controller with a deterministic policy layer is a far
stronger submission than the quickstart — and it already exists and passes 27 unit tests.

> **Jev decides. Code executes. Pipecat's default turn heuristics are not the brain.**

Read `docs/ARCHITECTURE.md` before touching the pipeline.

## Biggest risk, and it is not a technical one

**Lane B (reasoning) is unstaffed and B2 is the demo.** Issue #5 — notice confusion, stop, back up,
ask a non-leading question, re-explain — is the entire thing we are demoing, and nobody has started
it. Everything else could land perfectly and we would still have no demo.

If you are the incoming agent and your owner has not pinned you to a lane, **ask to be put on lane B
and start with #5.** Do not start anything else first.

## Who is on what

| Lane | Owner | Agent status |
| --- | --- | --- |
| A. Voice pipeline | unassigned (Akash acting) | Akash's agent, active on #1. Pipeline + controller being wired now. |
| B. Reasoning | **unstaffed** | Nothing. #4, #5, #6, #7 all untouched. |
| C. Perception | folded into B | Nothing. `frontend/src/gaze/` is reserved for it. |
| D. Integration and demo | rg | This agent. #11 done, #12 done, #13/#14 pending. |

## What is done and safe to build on

- `backend/app/` — config, Jev client, question sets, policy, state engine, speaker ID. 27 unit tests green.
- `backend/app/contracts.py` — Contracts 1–4 as pydantic models. **Import these rather than hand-rolling dicts.**
- `backend/app/observers/latency_hud.py` — Contract 4 feed; also appends `metrics.jsonl` at the repo root.
- `frontend/` — call UI, camera preview, live latency HUD. Builds clean; verified rendering.
- `backend/tests/e2e/test_demo_scenarios.py` — bad audio, interruptions, staged confusion. No credits, no mic.

## Open decisions — do not guess, these need a human

1. **Which LLM.** README and the organizers' quickstart say `gemma-4-31B-it`; `backend/app/config.py`
   defaults to `minimax-m2.7`. Measured TTFT: gemma ≈ 3.5 s, minimax ≈ 0.4 s (#16). Gemma cannot meet
   the latency target. **But** the prize requires inference on SambaNova hardware, and nobody has
   confirmed which of these is SambaNova-served (#15). Getting this wrong loses eligibility outright,
   which costs more than the latency. Blocked on #15.
2. **Contracts 1–4 vs `InteractionState`.** `COORDINATION.md` defines four cross-lane contracts. The
   engine currently uses its own `InteractionState` and implements none of them. Per CLAUDE.md rule 3
   that is a contract change and needs a `contract-change` issue before it lands on `main`. Raised
   on #1, not yet resolved. Until it is, write against `app/contracts.py` for anything crossing a lane.
3. **`JEV_API_KEY`.** Jev/TypeSafe is a new vendor on the demo path and nobody on the team has a key.
   `config.py` *requires* it, so the backend will not boot without it. Someone must either get a key
   or make it optional.

## Getting running

```bash
git clone git@github.com:LamaSu/voice-ai-hackathon.git && cd voice-ai-hackathon
cp .env.example .env          # keys arrive by DM, never in an issue, PR or prompt
```

```bash
cd backend && uv sync
uv run pytest                 # unit + e2e; no credits, no mic
uv run python scripts/smoke_providers.py   # checks keys and provider latency
```

```bash
cd frontend && npm install
npm run dev                   # http://localhost:5173, proxies /api to the bot on :7860
npm run dev -- --open         # add ?replay=1 to see the HUD with no bot running
```

Then, once per machine, for accurate Pipecat APIs:

```bash
uv tool install "pipecat-ai[cli]"
pipecat context-hub install   # needs huggingface.co; blocked on some networks
```

**Heads up on `uv sync`:** it pulls torch, torchaudio and speechbrain for ECAPA speaker ID. That is a
multi-GB download. If you are short on time or disk, `ENABLE_SPEAKER_ID=0` in `.env` skips it at
runtime, though the dependency still installs.

## House rules that are being broken, so please do not

These are from `CLAUDE.md` and they exist because three agents are writing to one repo:

1. **Claim before working** — comment `claimed by <agent-name>` and add `in-progress` *before* your
   first commit. We already burned effort on a duplicate A1 because two agents started within 33
   seconds of each other and neither had claimed.
2. **Never push to `main`** — branch `<lane-prefix>/<issue>-<slug>`, PR into `main`, lane owner merges.
   `main` has already taken one direct push.
3. **Status comment every 30 minutes** on your issue: `[HH:MM] <agent> — done / doing / blocked: <one line>`.
4. **Stay in your lane.** If your fix touches another lane, a secret, the inference config, or the
   demo path, stop and ask your owner.
5. **Secrets live in `.env` only.** Never commit a key, print one, or paste one into an issue or prompt.
6. **Latency is a test.** Any change on the speech path quotes before/after end-of-speech-to-first-audio
   from `metrics.jsonl` in the PR.

## Lane boundaries, concretely

So we stop colliding:

| You own | Path |
| --- | --- |
| Lane A — pipeline, controller, turn-taking | `backend/app/turns/`, `backend/app/providers/`, the Pipecat pipeline |
| Lane B — reasoning, prompts, probe picker | `backend/app/jev/`, LLM prompting, confusion → repair |
| Lane C — perception | `backend/app/perception/`, `frontend/src/gaze/` |
| Lane D — UI, HUD, tests, submission | `frontend/` (except `gaze/`), `backend/tests/e2e/`, `backend/app/observers/` |

## The demo we are building toward

Three minutes, latency HUD on screen, from `COORDINATION.md`:

1. Hook (20 s) — "Voice agents either think or talk fast. Ours does both, and notices when you're lost."
2. Calibration (20 s) — small talk while the 30-second baseline is captured.
3. Explanation (60 s) — the agent explains something hard; the driver frowns and hesitates.
4. **Repair (30 s)** — the agent stops mid-sentence, asks a non-leading question, re-explains. *This is #5.*
5. Barge-in (20 s) — the driver interrupts; the agent stops instantly.
6. Under the hood (30 s) — SambaNova endpoint in code, the `user_state` stream, measured latency.

If you are deciding whether something is worth building: does it make step 3–5 work live, in front of
a judge, before 4:30? If not, it waits.
