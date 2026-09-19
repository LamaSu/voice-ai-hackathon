# Confusion-aware voice agent

A live voice agent that notices when a listener gets lost mid-explanation, stops, asks a non-leading question, and re-explains — with frontier-speed reasoning on SambaNova hardware.

Built at the Voice AI Hackathon (AGI House SF, Sept 19, 2026).

## Stack

- **Inference:** General Compute (SambaNova SN40 endpoints), base URL `https://api.generalcompute.com/v1`. Model is still being settled — see the Decision log in [COORDINATION.md](COORDINATION.md)
- **Voice:** Pipecat, Gradium STT/TTS, Smart Turn, SmallWebRTC transport
- **Perception:** MediaPipe face blendshapes in the browser, fused with vocal prosody into a `user_state` stream

## Run

```bash
cp .env.example .env   # add GENERAL_COMPUTE_API_KEY and GRADIUM_API_KEY
```

**Backend** (the bot; serves SmallWebRTC signalling on `:7860`):

```bash
cd backend && uv sync
uv run pytest                              # unit + e2e, no mic and no credits spent
uv run python scripts/smoke_providers.py   # checks keys and provider latency
```

**Frontend** (call UI, camera preview, live latency HUD) in a second terminal:

```bash
cd frontend && npm install
npm run dev                                # http://localhost:5173
```

The dev server proxies `/api` to the bot, so the browser stays on one origin and
`getUserMedia` works without a second certificate. Point it elsewhere with
`BOT_URL=http://host:port npm run dev`.

To see the HUD with no bot running and no Gradium credits spent, open
<http://localhost:5173/?replay=1> — it renders a recorded metrics trace.

Every turn appends a Contract 4 sample to `metrics.jsonl` at the repo root, which is
where the before/after latency numbers in a speech-path PR come from.

## Jev interaction engine (lane A)

Turn-taking, interruptions, backchannels and end-of-turn are decided by **Jev** (TypeSafe AI) over a shared `InteractionState` fed by Gradium ASR, Silero VAD, ECAPA speaker ID and MediaPipe gaze. Deterministic code executes the decisions as Pipecat frames. See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

```bash
cd backend && uv sync
uv run python scripts/smoke_providers.py   # verifies GRADIUM / GENERAL_COMPUTE / JEV keys + latency
uv run pytest                              # unit tests;  uv run pytest -m live tests/live -s  for real APIs
```

## Team docs

- [COORDINATION.md](COORDINATION.md) — mission, lanes, contracts, timeline, decisions
- [ONBOARDING.md](ONBOARDING.md) — start here if you just joined: live status, open decisions, lane boundaries
- [CLAUDE.md](CLAUDE.md) — rules every coding agent follows
- Tasks live in GitHub Issues
