#!/usr/bin/env bash
# Creates the GitHub repo, pushes this folder, invites teammates, and creates labels + issues.
# Usage: ./scripts/bootstrap.sh <repo-name> [teammate-github-username ...]
# Needs: gh (logged in: `gh auth login`), git.
set -euo pipefail

REPO_NAME="${1:?usage: $0 <repo-name> [teammates...]}"; shift || true
OWNER="$(gh api user -q .login)"
FULL="$OWNER/$REPO_NAME"

cd "$(dirname "$0")/.."

echo "==> Creating $FULL (private until submission)"
git init -q -b main 2>/dev/null || true
git add -A
git commit -qm "Initial coordination scaffold" || true
gh repo create "$FULL" --private --source=. --push

for u in "$@"; do
  echo "==> Inviting $u (write access)"
  gh api -X PUT "repos/$FULL/collaborators/$u" -f permission=push >/dev/null
done

label() { gh label create "$1" --color "$2" --description "$3" --repo "$FULL" --force >/dev/null; }
echo "==> Labels"
label "lane:A" "1d76db" "Voice pipeline"
label "lane:B" "5319e7" "Reasoning"
label "lane:C" "0e8a16" "Perception"
label "lane:D" "fbca04" "Integration and demo"
label "P0" "b60205" "Demo path; done by 4:30 PM"
label "P1" "d93f0b" "Only after lane P0s are in review"
label "in-progress" "c5def5" "Claimed and being worked"
label "review" "bfd4f2" "PR open, awaiting owner"
label "blocked" "000000" "Needs a human"
label "contract-change" "e99695" "Touches a lane contract"

issue() { # title lane priority body
  gh issue create --repo "$FULL" --title "$1" --label "lane:$2" --label "$3" --body "$4" >/dev/null
  echo "    + [$2/$3] $1"
}
echo "==> Issues"
issue "A1: Run the voice agent quickstart" A P0 "Follow https://gist.github.com/kwindla/63fa9139e3bcece8404692f26367f6a2: Gradium STT/TTS, Pipecat Smart Turn, SmallWebRTC, gemma-4-31B-it on General Compute (\`https://api.generalcompute.com/v1\`). Scaffold with \`pipecat init\` inside this repo. Done when voice in → reply → voice out works locally and run steps are in the README."
issue "A2: Interruption handling" A P0 "Barge-in cancels TTS immediately and emits a \`turn\` event with \`interrupted: true\` (Contract 2)."
issue "A3: Tune VAD and turn detection" A P1 "Minimize end-of-speech detection time without cutting users off. Report before/after latency."
issue "B1: Reasoner with streamed speak output" B P0 "Prompt with capped thinking; stream \`speak\` chunks (Contract 3) so TTS starts on the first sentence."
issue "B2: Confusion hypotheses and repair behavior" B P0 "Consume \`user_state\` (Contract 1). When confusion is likely, stop, back up, and re-explain."
issue "B3: Probe-question picker" B P1 "Generate candidate non-leading questions, score how well each separates the confusion hypotheses, ask the best. At most one probe per 3 turns."
issue "B4: Speculative reasoning on partial transcripts" B P1 "Start reasoning on \`partial\` transcripts; commit or re-plan on \`final\` or interruption."
issue "C1: Browser face features → user_state" C P0 "MediaPipe face blendshapes in the browser, emitting \`user_state\` at ~10 Hz (Contract 1). Send numbers, never video."
issue "C2: Baseline calibration" C P0 "Capture a 30-second per-user baseline; emit deltas from it."
issue "C3: Prosody fused into confusion_p" C P1 "Optional. Fuse a vocal prosody signal with face features into \`confusion_p\`. Decide on Hume vs. derived-from-audio first."
issue "D1: Frontend with live latency HUD" D P0 "Call UI with camera, plus a HUD showing end of speech → first audio from \`metrics\` (Contract 4)."
issue "D2: End-to-end test script" D P0 "Scripted runs covering bad audio, interruptions, and a staged confusion moment."
issue "D3: Demo rehearsal and backup recording" D P0 "Two clean live runs of the demo script in COORDINATION.md; save a backup recording."
issue "D4: Submission" D P0 "Make the repo public, finish the README (show the SambaNova endpoint), submit on hackathon.new before 6:00 PM."
issue "Blocker: General Compute key and SambaNova eligibility" D P0 "Create the General Compute API key. Confirm with General Compute that their endpoints count as SambaNova hardware for the Most Technical prize."
issue "Blocker: Measure time to first token" B P0 "Compare gemma-4-31B-it on General Compute against the SN50 MiniMax endpoint, if SambaNova exposes one. Post numbers here."

echo
echo "Done: https://github.com/$FULL"
if [ $# -gt 0 ]; then echo "Teammates must accept their invites: https://github.com/$FULL/invitations"; fi
