"""Read Claude Code session transcripts (JSONL) and split them at compaction boundaries.

The transcript format is undocumented and changes between Claude Code versions, so
everything here is defensive: unknown entry types are ignored, and missing fields
never raise.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Iterable, Iterator

SUMMARY_PREFIX = "This session is being continued from a previous conversation"
_REMINDER_RE = re.compile(r"<system-reminder>.*?</system-reminder>", re.S)


def load(path: str) -> list[dict[str, Any]]:
    """Load a JSONL transcript, skipping lines that are not valid JSON objects."""
    entries: list[dict[str, Any]] = []
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(obj, dict):
                    entries.append(obj)
    except OSError:
        return []
    return entries


def is_boundary(entry: dict[str, Any]) -> bool:
    return entry.get("type") == "system" and entry.get("subtype") == "compact_boundary"


def is_summary(entry: dict[str, Any]) -> bool:
    if entry.get("type") != "user":
        return False
    if entry.get("isCompactSummary"):
        return True
    text = message_text(entry)
    return text.startswith(SUMMARY_PREFIX)


def content_blocks(entry: dict[str, Any]) -> list[dict[str, Any]]:
    msg = entry.get("message")
    if not isinstance(msg, dict):
        return []
    content = msg.get("content")
    if isinstance(content, str):
        return [{"type": "text", "text": content}]
    if isinstance(content, list):
        return [b for b in content if isinstance(b, dict)]
    return []


def message_text(entry: dict[str, Any]) -> str:
    parts = [b.get("text", "") for b in content_blocks(entry) if b.get("type") == "text"]
    return "\n".join(p for p in parts if isinstance(p, str))


def strip_reminders(text: str) -> str:
    return _REMINDER_RE.sub("", text).strip()


def is_human_prompt(entry: dict[str, Any]) -> bool:
    """True for messages a person typed (not tool results, hook output or meta)."""
    if entry.get("type") != "user" or entry.get("isMeta") or entry.get("isSidechain"):
        return False
    if is_summary(entry):
        return False
    blocks = content_blocks(entry)
    if not blocks or any(b.get("type") == "tool_result" for b in blocks):
        return False
    text = strip_reminders(message_text(entry))
    if not text:
        return False
    # Slash-command echoes, local command output, background task notifications.
    if text.startswith(("<command-", "<local-command", "<task-notification", "<bash-", "Caveat:")):
        return False
    return True


@dataclass
class Segment:
    """Entries between two compaction boundaries, plus the summary that replaced them."""

    index: int
    entries: list[dict[str, Any]] = field(default_factory=list)
    summary: str | None = None
    boundary: dict[str, Any] | None = None  # the boundary that ENDED this segment


def segments(entries: Iterable[dict[str, Any]]) -> list[Segment]:
    """Split a transcript into context windows.

    Segment ``i`` holds the live conversation that compaction ``i`` summarised.
    ``segment.summary`` is the summary text injected after that compaction, when the
    transcript contains it. The last segment is the current (uncompacted) window.
    """
    segs: list[Segment] = [Segment(index=0)]
    awaiting_summary: Segment | None = None
    for entry in entries:
        if is_boundary(entry):
            segs[-1].boundary = entry
            awaiting_summary = segs[-1]
            segs.append(Segment(index=len(segs)))
            continue
        if awaiting_summary is not None and is_summary(entry):
            awaiting_summary.summary = strip_reminders(message_text(entry))
            awaiting_summary = None
            continue
        segs[-1].entries.append(entry)
    return segs


def iter_tool_uses(entries: Iterable[dict[str, Any]]) -> Iterator[tuple[dict[str, Any], dict[str, Any]]]:
    for entry in entries:
        if entry.get("type") != "assistant":
            continue
        for block in content_blocks(entry):
            if block.get("type") == "tool_use":
                yield entry, block


def tool_results(entries: Iterable[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Map tool_use_id -> {'text': str, 'is_error': bool}."""
    out: dict[str, dict[str, Any]] = {}
    for entry in entries:
        if entry.get("type") != "user":
            continue
        for block in content_blocks(entry):
            if block.get("type") != "tool_result":
                continue
            content = block.get("content")
            if isinstance(content, list):
                text = "\n".join(
                    c.get("text", "") for c in content if isinstance(c, dict) and c.get("type") == "text"
                )
            else:
                text = str(content or "")
            out[str(block.get("tool_use_id"))] = {"text": text, "is_error": bool(block.get("is_error"))}
    return out
