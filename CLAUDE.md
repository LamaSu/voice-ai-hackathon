# Agent rules

You are one of several coding agents on a hackathon team. Read `COORDINATION.md` before starting. These rules are mandatory; your lane's human owner can override them.

1. **Claim before working.** Pick one open issue labeled with your lane. Comment `claimed by <agent-name>` and add the `in-progress` label. Never take an issue outside your lane or one already claimed.
2. **One branch per issue:** `<lane-prefix>/<issue-number>-<slug>`, for example `brain/12-probe-picker`. Commit small; start commit messages with `#<issue-number>`.
3. **Contracts are read-only.** If your task needs a change to a contract in `COORDINATION.md`, stop, open an issue labeled `blocked` and `contract-change`, and tell your owner.
4. **Status comment every 30 minutes, or when done or blocked,** on your issue: `[HH:MM] <agent> — done / doing / blocked: <one line>`.
5. **Handoff = PR.** Open a PR against `main` with `Closes #<issue-number>`, fill in the template, and add the `review` label to the issue. Only the lane's human owner merges.
6. **Escalate, don't guess,** when a fix touches another lane, a secret, the inference endpoint config, or anything on the demo path. Stop and ask your owner.
7. **Timebox.** Still unfinished after 45 minutes? Post a status comment and ask your owner whether to cut scope.
8. **Latency is a test.** Any change on the speech path reports before and after end-of-speech-to-first-audio times from the metrics log in the PR.
9. **Check docs locally first.** Run `pipecat context-hub-install` once per machine and query that Pipecat docs server before guessing an API.
10. **Secrets live in `.env` only.** Never commit keys, print them in logs, or paste them into issues, PRs, or prompts.

Useful commands:

```bash
gh issue list --label lane:B --label P0 --state open
gh issue comment <n> --body "claimed by <agent-name>"
gh issue edit <n> --add-label in-progress
gh pr create --fill --body "Closes #<n>"
```
