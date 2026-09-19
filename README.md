# Confusion-aware voice agent

A live voice agent that notices when a listener gets lost mid-explanation, stops, asks a non-leading question, and re-explains — with frontier-speed reasoning on SambaNova hardware.

Built at the Voice AI Hackathon (AGI House SF, Sept 19, 2026).

## Stack

- **Inference:** gemma-4-31B-it on General Compute (SambaNova SN40 endpoints), base URL `https://api.generalcompute.com/v1`
- **Voice:** Pipecat, Gradium STT/TTS, Smart Turn, SmallWebRTC transport
- **Perception:** MediaPipe face blendshapes in the browser, fused with vocal prosody into a `user_state` stream

## Run

```bash
cp .env.example .env   # add GENERAL_COMPUTE_API_KEY and GRADIUM_API_KEY
# run instructions land here with task A1
```

## Jev interaction engine (lane A)

Turn-taking, interruptions, backchannels and end-of-turn are decided by **Jev** (TypeSafe AI) over a shared `InteractionState` fed by Gradium ASR, Silero VAD, ECAPA speaker ID and MediaPipe gaze. Deterministic code executes the decisions as Pipecat frames. See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

```bash
cd backend && uv sync
uv run python scripts/smoke_providers.py   # verifies GRADIUM / GENERAL_COMPUTE / JEV keys + latency
uv run pytest                              # unit tests;  uv run pytest -m live tests/live -s  for real APIs
```

## Team docs

- [COORDINATION.md](COORDINATION.md) — mission, lanes, contracts, timeline, decisions
- [CLAUDE.md](CLAUDE.md) — rules every coding agent follows
- Tasks live in GitHub Issues
