"""Turn the listener's live face signals into a line the reasoner can act on.

The face could already stop the agent (`decide_probe`) and tell Jev whether it
was being addressed — but the model *writing the reply* had never seen a single
face signal. So the agent could notice you were lost and could not explain
differently because of it, which is the whole idea.

Two rules shape what this emits:

**Never let the agent mention the face.** Saying "you look confused" is leading
— it invites a reflexive "no, I'm fine" — and it reads as the emotion-detection
claim COORDINATION.md explicitly disowns. The signal changes *how* the agent
explains, never what it claims to know about you.

**Say what to do, not just what was seen.** "brow_lower 0.42" is not actionable
for a model mid-sentence; "they may be losing the thread, so slow down and use a
concrete example" is.

Kept to one short line, because every prompt token is time-to-first-token and
latency is the thing we are judged on.
"""

from __future__ import annotations

# Below this the prior is too weak to act on — acting on noise makes the agent
# twitchy, which is worse than ignoring the channel.
CONFUSION_NUDGE = 0.45
CONFUSION_STRONG = 0.70
STALE_AFTER_S = 2.0


def listener_note(vision, now: float | None = None) -> str | None:
    """A one-line instruction from the listener's state, or None to say nothing.

    Args:
        vision: the engine's `VisionState`.
        now: current time, for staleness. Omit to skip the staleness check.
    """
    if not getattr(vision, "enabled", False) or not getattr(vision, "face_present", False):
        return None

    # A frozen reading is worse than none: it describes a moment that has passed.
    updated = getattr(vision, "updated_at", None)
    if now is not None and updated is not None and now - updated > STALE_AFTER_S:
        return None

    confusion = float(getattr(vision, "confusion_p", 0.0) or 0.0)
    observations: list[str] = []
    guidance: list[str] = []

    if confusion >= CONFUSION_STRONG:
        observations.append("is showing strong signs of being lost")
        guidance.append(
            "back up to the last point they clearly had, re-explain it a different way with a "
            "concrete example, and keep it to two sentences"
        )
    elif confusion >= CONFUSION_NUDGE:
        observations.append("may be starting to lose the thread")
        guidance.append("slow down, simplify, and avoid introducing new terms")

    if not getattr(vision, "looking_at_agent", True):
        observations.append("has looked away")
        if confusion < CONFUSION_NUDGE:
            guidance.append("be brief and check they still want this")

    if getattr(vision, "nod", 0) > 0 and confusion < CONFUSION_NUDGE:
        observations.append("is nodding along")
        guidance.append("keep going at this pace")

    if getattr(vision, "wants_turn", False):
        observations.append("looks like they are about to speak")
        guidance.append("finish your current thought quickly and hand back")

    if not observations:
        return None

    return (
        f"Live read on the listener: the person you are talking to {_join(observations)}. "
        f"Therefore: {_join(guidance)}. "
        "Do not mention their face, expression or body language, and do not ask whether they are "
        "confused — just adjust how you explain."
    )


def _join(items: list[str]) -> str:
    if len(items) == 1:
        return items[0]
    return ", ".join(items[:-1]) + " and " + items[-1]
