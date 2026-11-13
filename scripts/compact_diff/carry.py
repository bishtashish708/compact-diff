"""Carry re-injected items forward across compactions.

Rules and open tasks are re-extracted from the whole transcript at every compaction, so
they are always rechecked. Errors, edited files and decisions come only from the
current context window. Once compact-diff re-injects one of those after compaction N,
it lives only in hook context and summary N, and the next snapshot would not see it.

The ledger (``carried.json`` in the session's state directory) fixes that:

* After each compaction, every error, file or decision that was re-injected is recorded,
  keyed by kind plus a hash of its normalised text, with the compaction it came from.
  Items the summary kept are removed: from then on the summary carries them.
* At the next PreCompact, ledger items are merged into the snapshot, marked "carried
  from compaction N", unless:
    - fresh extraction already has the same item (deduped by key), or the same failing
      call for an error;
    - the error is resolved: the same command later ran cleanly in the new window;
    - the decision is superseded: a newer decision covers the same subject;
    - the item has already been carried ``MAX_CARRIES`` times.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any

from . import extract, store
from .extract import Item
from .match import Verdict, tokens

LEDGER = "carried.json"
CARRY_KINDS = ("error", "file", "decision")
MAX_CARRIES = 3

_DECISION_WORDS = {"decided", "decision", "going", "chose", "choose", "picked", "switch", "switched", "switching",
                   "instead", "rather", "settled", "opted", "i'll", "we'll"}


def key(item: Item) -> str:
    norm = re.sub(r"\s+", " ", item.text.lower()).strip(" .;:!?")
    return f"{item.kind}:{hashlib.sha1(norm.encode()).hexdigest()[:12]}"


def load(sdir: Path) -> dict[str, dict[str, Any]]:
    data = store.read_json(sdir / LEDGER)
    return data.get("items", {}) if isinstance(data, dict) else {}


def save(sdir: Path, items: dict[str, dict[str, Any]]) -> None:
    store.write_json(sdir / LEDGER, {"items": items})


def _same_subject(old: Item, new: Item) -> bool:
    a = {t for t in tokens(old.text) if t not in _DECISION_WORDS}
    b = {t for t in tokens(new.text) if t not in _DECISION_WORDS}
    return len(a & b) >= 2 or (bool(a) and a <= b)


def merge(
    sdir: Path, fresh: list[Item], window: list[dict[str, Any]], compaction: int
) -> tuple[list[Item], dict[str, Any]]:
    """Add still-relevant carried items to a fresh snapshot. Returns (items, carry info)."""
    ledger = load(sdir)
    if not ledger:
        return fresh, {"carried": 0, "retired": []}
    fresh_keys = {key(i) for i in fresh}
    fresh_calls = {i.source for i in fresh if i.kind == "error" and i.source}
    fresh_decisions = [i for i in fresh if i.kind == "decision"]
    succeeded = extract.succeeded_calls(window)
    out = list(fresh)
    kept: dict[str, dict[str, Any]] = {}
    retired: list[dict[str, str]] = []
    for k, rec in ledger.items():
        item = Item.from_dict(rec.get("item", {}))
        if k in fresh_keys or (item.kind == "error" and item.source and item.source in fresh_calls):
            continue  # fresh extraction covers it; it re-enters the ledger if dropped again
        reason = None
        if item.kind == "error" and item.source and item.source in succeeded:
            reason = "resolved: the same command later ran cleanly"
        elif item.kind == "decision" and any(_same_subject(item, d) for d in fresh_decisions):
            reason = "superseded by a newer decision"
        elif int(rec.get("carries", 0)) >= MAX_CARRIES:
            reason = f"carried {MAX_CARRIES} times already"
        if reason:
            retired.append({"kind": item.kind, "text": item.text, "reason": reason})
            continue
        rec = {**rec, "carries": int(rec.get("carries", 0)) + 1}
        item.carried_from = int(rec.get("since") or compaction - 1)
        out.append(item)
        kept[k] = rec
    save(sdir, kept)
    return out, {"carried": len(kept), "retired": retired}


def update(sdir: Path, verdicts: list[Verdict], compaction: int, reinjected: set[str]) -> None:
    """Record re-injected errors, files and decisions; forget the ones the summary kept.

    ``reinjected`` holds the ids of the items that went into the re-injection block.
    """
    ledger = load(sdir)
    for v in verdicts:
        if v.item.kind not in CARRY_KINDS:
            continue
        k = key(v.item)
        if v.item.id in reinjected:
            if k not in ledger:
                item = Item.from_dict(v.item.to_dict())
                item.carried_from = 0
                ledger[k] = {"item": item.to_dict(), "since": v.item.carried_from or compaction, "carries": 0}
        else:
            ledger.pop(k, None)  # kept (or not worth re-injecting): the summary carries it now
    save(sdir, ledger)
