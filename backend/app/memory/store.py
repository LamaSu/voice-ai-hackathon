"""People + conversation memory, persisted to backend/data/memory.json.

- people:   ECAPA speaker profiles (WhoSpeaksLive SpeakerMemory export) + bound name + facts
- summary:  rolling conversation summary maintained by the General Compute LLM
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from loguru import logger
from openai import AsyncOpenAI

from app.config import BACKEND_DIR

DEFAULT_PATH = BACKEND_DIR / "data" / "memory.json"


@dataclass
class Person:
    label: str
    name: str | None = None
    facts: list[str] = field(default_factory=list)
    speech_seconds: float = 0.0
    utterances: int = 0
    first_seen: float = field(default_factory=time.time)
    last_seen: float = field(default_factory=time.time)


class MemoryStore:
    def __init__(self, path: Path = DEFAULT_PATH):
        self.path = path
        self.people: dict[str, Person] = {}
        self.profiles: list[dict[str, Any]] = []  # SpeakerMemory.export_profiles()
        self.summary: str = ""
        self.pending_name: str | None = None  # introduced before their voice profile existed
        self.load()

    # ---------- persistence ----------
    def load(self) -> None:
        if not self.path.exists():
            return
        try:
            data = json.loads(self.path.read_text())
        except Exception as e:  # noqa: BLE001
            logger.warning(f"memory load failed: {e}")
            return
        self.profiles = data.get("profiles", [])
        self.summary = data.get("summary", "")
        self.people = {p["label"]: Person(**p) for p in data.get("people", [])}
        logger.info(f"memory: loaded {len(self.people)} people, {len(self.profiles)} voice profiles")

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(
            json.dumps(
                {
                    "people": [asdict(p) for p in self.people.values()],
                    "profiles": self.profiles,
                    "summary": self.summary,
                },
                indent=1,
            )
        )
        tmp.replace(self.path)

    def clear(self) -> None:
        """Forget everyone: names, facts, voice profiles and the conversation summary."""
        self.people.clear()
        self.profiles = []
        self.summary = ""
        self.pending_name = None
        self.save()

    # ---------- people ----------
    def person(self, label: str) -> Person:
        if label not in self.people:
            self.people[label] = Person(label=label)
        return self.people[label]

    def display_name(self, label: str | None) -> str | None:
        if not label:
            return None
        p = self.people.get(label)
        return (p.name if p and p.name else None) or label

    def note_utterance(self, label: str, seconds: float) -> None:
        p = self.person(label)
        p.utterances += 1
        p.speech_seconds += seconds
        p.last_seen = time.time()

    def set_name(self, label: str, name: str) -> None:
        self.person(label).name = name
        self.save()

    def to_ui(self) -> dict[str, Any]:
        return {
            "people": [
                {
                    "label": p.label,
                    "name": p.name,
                    "facts": p.facts[-8:],
                    "speech_seconds": round(p.speech_seconds, 1),
                    "utterances": p.utterances,
                }
                for p in sorted(self.people.values(), key=lambda x: x.label)
            ],
            "summary": self.summary,
        }

    def prompt_block(self) -> str:
        lines = []
        known = [p for p in self.people.values() if p.name or p.facts]
        if known:
            lines.append("People you know (recognized by voice):")
            for p in known:
                facts = "; ".join(p.facts[-6:])
                lines.append(f"- {p.name or p.label} ({p.label}){': ' + facts if facts else ''}")
        if self.summary:
            lines.append(f"Conversation so far: {self.summary}")
        return "\n".join(lines)


_NAME_PATTERNS = [
    r"\bmy name is ([A-Z][\w'-]+)",
    r"\bmy name's ([A-Z][\w'-]+)",
    r"\bi am ([A-Z][\w'-]+)",
    r"\bi'm ([A-Z][\w'-]+)",
    r"\bthis is ([A-Z][\w'-]+)",
    r"\bcall me ([A-Z][\w'-]+)",
]
_NOT_NAMES = {"Sorry", "Fine", "Good", "Okay", "Ok", "Here", "Just", "Not", "So", "Really", "Going", "Trying"}


def regex_name(text: str) -> str | None:
    for pat in _NAME_PATTERNS:
        m = re.search(pat, text, flags=re.IGNORECASE)
        if m:
            cand = m.group(1).strip(".,!?")
            cand = cand[:1].upper() + cand[1:]
            if cand not in _NOT_NAMES:
                return cand
    return None


def _parse_json(text: str) -> dict[str, Any]:
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()
    m = re.search(r"\{.*\}", text, flags=re.DOTALL)
    return json.loads(m.group(0)) if m else {}


class MemoryLLM:
    """Small General Compute calls for name extraction and summary updates (off the hot path)."""

    def __init__(self, api_key: str, base_url: str, model: str):
        self._client = AsyncOpenAI(api_key=api_key, base_url=base_url)
        self._model = model
        self._lock = asyncio.Lock()

    async def _json(self, system: str, user: str, max_tokens: int = 400) -> dict[str, Any]:
        resp = await self._client.chat.completions.create(
            model=self._model,
            messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
            response_format={"type": "json_object"},
            max_tokens=max_tokens,
            temperature=0.1,
        )
        return _parse_json(resp.choices[0].message.content or "")

    async def extract_name(self, utterance: str) -> str | None:
        guess = regex_name(utterance)
        try:
            data = await self._json(
                'Extract the speaker\'s own first name if they introduce themselves. Reply JSON {"name": string|null}.',
                utterance,
                max_tokens=60,
            )
            name = (data.get("name") or "").strip()
            name = name[:1].upper() + name[1:] if name else ""
            return name or guess
        except Exception as e:  # noqa: BLE001
            logger.warning(f"name extraction failed, using regex: {e}")
            return guess

    async def update(
        self, store: MemoryStore, speaker_label: str | None, user_text: str, bot_text: str
    ) -> bool:
        """Update the rolling summary + per-person facts. Returns True if memory changed."""
        if self._lock.locked():
            return False  # a summary update is already running; next exchange will catch up
        async with self._lock:
            who = store.display_name(speaker_label) or "User"
            people = {p.label: {"name": p.name, "facts": p.facts[-6:]} for p in store.people.values()}
            prompt = json.dumps(
                {
                    "previous_summary": store.summary,
                    "people": people,
                    "new_exchange": {"speaker_label": speaker_label, "speaker": who, "user": user_text, "assistant": bot_text},
                }
            )
            try:
                data = await self._json(
                    "You maintain memory for a voice assistant. Given the previous summary, known people and "
                    "the newest exchange, return JSON with keys: "
                    '"summary" (<= 80 words, third person, keep important details from before; refer to people '
                    "by name or they/them only: never use he, she, him, her, his, hers, himself, herself), "
                    '"new_facts" (object mapping speaker_label -> list of NEW short durable facts about that '
                    "person, e.g. preferences, plans, relationships; empty if none). Facts start with the "
                    "person's name (never he/she/him/her/his/hers) and must be about the person's life, not about this "
                    "conversation, the assistant, name spelling, or transcription. Do not repeat known facts.",
                    prompt,
                )
            except Exception as e:  # noqa: BLE001
                logger.warning(f"memory update failed: {e}")
                return False
            store.summary = str(data.get("summary") or store.summary)[:800]
            for label, facts in (data.get("new_facts") or {}).items():
                if label in store.people and isinstance(facts, list):
                    p = store.people[label]
                    for f in facts:
                        f = str(f).strip()
                        if f and f not in p.facts:
                            p.facts.append(f)
            store.save()
            return True
