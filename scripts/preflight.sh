#!/usr/bin/env bash
# Pre-flight for the live demo. Run it on the demo laptop before the rehearsal
# and again in the last quiet moment before judging.
#
# It answers one question: would the demo work if we started right now?
# It never spends more than a couple of Gradium credits, and it prints a
# verdict rather than a wall of output.
#
#   ./scripts/preflight.sh            # checks that need no bot running
#   ./scripts/preflight.sh --live     # also drives the running bot end to end
#
# Exit code 0 means go. Anything else, read the FAIL lines.
set -uo pipefail

# `timeout` is GNU coreutils and absent on macOS, where this is run on demo day. Use
# gtimeout when present, otherwise run the command unbounded rather than failing rc=127.
run_limited() {
  local secs="$1"; shift
  if command -v timeout >/dev/null 2>&1; then timeout "$secs" "$@"
  elif command -v gtimeout >/dev/null 2>&1; then gtimeout "$secs" "$@"
  else "$@"
  fi
}

cd "$(dirname "$0")/.."
LIVE=0
[[ "${1:-}" == "--live" ]] && LIVE=1

pass=0; fail=0; warn=0
ok()   { printf '  \033[32mOK\033[0m    %s\n' "$1"; pass=$((pass+1)); }
bad()  { printf '  \033[31mFAIL\033[0m  %s\n' "$1"; fail=$((fail+1)); }
note() { printf '  \033[33mWARN\033[0m  %s\n' "$1"; warn=$((warn+1)); }
head_() { printf '\n\033[1m%s\033[0m\n' "$1"; }

head_ "Secrets"
if [[ -f .env ]]; then
  ok ".env exists"
  # Presence only. Never print a key, not even a prefix (CLAUDE.md rule 10).
  for k in GRADIUM_API_KEY GENERAL_COMPUTE; do
    if grep -qE "^${k}=.+" .env; then ok "$k is set"; else bad "$k is missing or empty"; fi
  done
  if grep -qE "^(JEV_API_KEY|TYPESAFE_API_KEY)=.+" .env; then
    ok "Jev key is set (full turn-taking)"
  else
    note "no Jev key — the bot still runs on the deterministic fallback, but the Jev panels will be empty"
  fi
else
  bad ".env not found — cp .env.example .env and paste the keys you were DM'd"
fi
if git check-ignore -q .env 2>/dev/null; then ok ".env is git-ignored"; else bad ".env is NOT git-ignored"; fi

head_ "Backend"
if [[ -d backend/.venv ]] || command -v uv >/dev/null; then ok "uv available"; else bad "uv not installed"; fi
# Sync first. On a cold laptop the very first `uv run` builds the venv (torch is
# large), and without this the checks below fail for the wrong reason.
printf '  ...   syncing backend deps (slow the first time)\n'
(cd backend && uv sync >/tmp/preflight-sync.log 2>&1) \
  && ok "deps synced" || bad "uv sync failed — see /tmp/preflight-sync.log"
if (cd backend && uv run python -c "import pipecat, app.config" >/dev/null 2>&1); then
  ok "backend imports cleanly"
else
  bad "backend will not import — see /tmp/preflight-sync.log"
fi
if (cd backend && uv run pytest -q >/tmp/preflight-pytest.log 2>&1); then
  ok "tests green ($(grep -oE '[0-9]+ passed' /tmp/preflight-pytest.log | head -1))"
else
  bad "tests failing — see /tmp/preflight-pytest.log"
fi

head_ "Frontend"
if [[ -d frontend/node_modules ]]; then ok "node_modules present"; else bad "run 'cd frontend && npm install'"; fi
if (cd frontend && npm run build >/tmp/preflight-build.log 2>&1); then
  ok "frontend builds"
else
  bad "frontend build failed — see /tmp/preflight-build.log"
fi

head_ "Hardware"
# The demo is live; a missing camera or mic is the one failure no fallback covers.
if [[ -e /dev/video0 ]]; then ok "camera device present"; else note "no /dev/video0 (fine on macOS; check the browser prompt)"; fi

head_ "Providers"
# smoke_providers.py reports per-provider failures in its output but still exits 0,
# so the exit code alone would call a dead provider healthy. Read the output.
(cd backend && run_limited 90 uv run python scripts/smoke_providers.py >/tmp/preflight-smoke.log 2>&1)
smoke_rc=$?
if grep -qiE "fail|error|traceback" /tmp/preflight-smoke.log; then
  bad "a provider is down — keys, venue wifi, or Gradium credits:"
  grep -iE "fail|error" /tmp/preflight-smoke.log | head -4 | sed 's/^/        /'
elif [[ $smoke_rc -ne 0 ]]; then
  bad "provider smoke test did not finish (rc=$smoke_rc) — see /tmp/preflight-smoke.log"
else
  ok "Gradium / General Compute / Jev reachable"
  grep -iE "ttft|latency|ms" /tmp/preflight-smoke.log | head -5 | sed 's/^/        /'
fi

if [[ $LIVE -eq 1 ]]; then
  head_ "End to end (bot must already be running)"
  if curl -sS -o /dev/null -m 5 http://127.0.0.1:7860/ 2>/dev/null; then
    ok "bot answering on :7860"
    if (cd backend && run_limited 180 uv run python scripts/e2e_webrtc.py >/tmp/preflight-e2e.log 2>&1); then
      ok "WebRTC e2e passed (intro, backchannel, barge-in, second speaker)"
    else
      bad "WebRTC e2e failed — see /tmp/preflight-e2e.log"
    fi
  else
    bad "nothing on :7860 — start it with 'cd backend && uv run python -m app.server'"
  fi

  head_ "Latency"
  if [[ -s metrics.jsonl ]]; then
    python3 - <<'PY'
import json, statistics, pathlib
vals = [
    json.loads(l).get("end_of_speech_to_first_audio_ms")
    for l in pathlib.Path("metrics.jsonl").read_text().splitlines() if l.strip()
]
vals = [v for v in vals if isinstance(v, (int, float))]
if not vals:
    print("  \033[33mWARN\033[0m  metrics.jsonl has no latency samples yet")
else:
    med = statistics.median(vals)
    verdict = "conversational" if med < 500 else ("usable" if med < 1500 else "PHONE TREE")
    colour = "32" if med < 500 else ("33" if med < 1500 else "31")
    print(f"  \033[{colour}m{verdict}\033[0m  median {med:.0f} ms over {len(vals)} turns "
          f"(best {min(vals):.0f}, worst {max(vals):.0f})")
    print("        This is the number judges ask for. Quote the median, not the best.")
PY
  else
    note "no metrics.jsonl yet — run a turn, then re-run this"
  fi
fi

head_ "Backup"
if compgen -G "recordings/*" >/dev/null 2>&1; then
  ok "backup recording present: $(ls -t recordings/* | head -1)"
else
  note "no backup recording yet — D3 says record one before 5:15"
fi

head_ "Verdict"
printf '  %d passed, %d warnings, %d failures\n' "$pass" "$warn" "$fail"
if [[ $fail -eq 0 ]]; then
  printf '  \033[32mGO\033[0m — demo path is ready.\n'
  exit 0
fi
printf '  \033[31mNO-GO\033[0m — fix the FAIL lines above.\n'
exit 1
