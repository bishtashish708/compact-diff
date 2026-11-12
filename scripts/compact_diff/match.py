"""Decide whether a compaction summary still carries each extracted item.

Scoring is lexical and deterministic: for each item we find the window of summary
sentences that best covers the item's significant words. Anchors (file basenames,
exception names) are checked by exact substring first, because a summary that
mentions ``auth.py`` has kept that file regardless of wording.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass

from .extract import Item, sentences

KEPT = "kept"
PARTIAL = "partial"
DROPPED = "dropped"

KEPT_AT = 0.7
PARTIAL_AT = 0.4
WINDOW = 3

_WORD_RE = re.compile(r"[a-z0-9][a-z0-9_.\-/]*[a-z0-9]|[a-z0-9]")
_STOP = set(
    """a an the and or but if then else of to in on at by for with from into onto as is are was were be been
    being it its this that these those there here we you i me my our your they them he she his her do does did
    done doing have has had having can could would will shall may might just so also very too not no yes all any
    some each every than more most less much many such own same other about over under again further once only
    what which who whom when where why how let lets ok okay please thanks thank use using used make made sure
    get got go going want need like now one two first second new file files""".split()
)
# Words that flip meaning: if the item has one, the matched window must too.
_NEGATIONS = {"not", "no", "never", "don't", "dont", "do not", "avoid", "without", "stop", "instead"}


def _stem(w: str) -> str:
    for suf in ("ing", "ed", "es", "s"):
        if len(w) > len(suf) + 3 and w.endswith(suf):
            return w[: -len(suf)]
    return w


def tokens(text: str) -> set[str]:
    out = set()
    for w in _WORD_RE.findall(text.lower()):
        w = w.strip("./-")
        if len(w) < 3 or w in _STOP:
            continue
        out.add(_stem(w))
        # Paths: also index the last component so "src/a/auth.py" matches "auth.py".
        if "/" in w:
            out.add(_stem(w.rsplit("/", 1)[-1]))
    return out


_GENERIC_STEMS = {
    "index", "main", "utils", "util", "readme", "init", "__init__", "config", "setup", "test", "tests",
    "app", "core", "base", "common", "types", "helpers", "constants", "settings", "license", "package",
    "conftest", "makefile", "dockerfile", "changelog", "server", "client", "models", "views",
}


def _stem_mentioned(basename: str, text: str) -> bool:
    """True if a distinctive, code-like file stem (``test_cli`` for ``test_cli.py``) appears as a whole word."""
    stem = os.path.splitext(basename)[0]
    if len(stem) < 6 or stem.lower() in _GENERIC_STEMS:
        return False
    # Only code-like stems: a plain word ("session", "billing") also appears in ordinary prose.
    if not re.search(r"[_\-0-9]|[a-z][A-Z]", stem):
        return False
    return re.search(r"(?<![\w.-])" + re.escape(stem) + r"(?![\w-])", text, re.I) is not None


def _has_negation(text: str) -> bool:
    low = " " + text.lower().replace("’", "'") + " "
    return any(f" {n} " in low or f" {n}," in low for n in _NEGATIONS) or "n't " in low


@dataclass
class Verdict:
    item: Item
    status: str
    score: float
    evidence: str = ""

    def to_dict(self) -> dict:
        return {"item": self.item.to_dict(), "status": self.status, "score": round(self.score, 3), "evidence": self.evidence}


class Summary:
    def __init__(self, text: str):
        self.text = text or ""
        self.lower = self.text.lower()
        sents = sentences(self.text) or [self.text]
        self.windows: list[tuple[str, set[str]]] = []
        for i in range(len(sents)):
            chunk = " ".join(sents[i : i + WINDOW])
            self.windows.append((chunk, tokens(chunk)))

    def best_window(self, toks: set[str]) -> tuple[float, str]:
        if not toks:
            return 0.0, ""
        best, best_text = 0.0, ""
        for chunk, wt in self.windows:
            score = len(toks & wt) / len(toks)
            if score > best:
                best, best_text = score, chunk
        return best, best_text


def judge(item: Item, summary: Summary) -> Verdict:
    anchors = [a for a in item.anchors if a]
    if anchors and all(a.lower() in summary.lower for a in anchors):
        if item.kind in ("file", "error"):
            return Verdict(item, KEPT, 1.0, f"mentions {', '.join(anchors)}")
    if item.kind == "file" and anchors and all(_stem_mentioned(a, summary.text) for a in anchors):
        # Summaries often list files without extensions: "tests: test_cli, test_pricing".
        return Verdict(item, KEPT, 0.9, "mentions " + ", ".join(os.path.splitext(a)[0] for a in anchors))
    toks = tokens(item.text)
    score, evidence = summary.best_window(toks)
    if item.kind == "file":
        # A file the summary never names is dropped, however many path words overlap.
        return Verdict(item, DROPPED, score, "")
    if anchors and item.kind == "error":
        hit = sum(a.lower() in summary.lower for a in anchors) / len(anchors)
        score = max(score, hit * 0.8)
    if score >= KEPT_AT and item.kind == "instruction" and _has_negation(item.text) and not _has_negation(evidence):
        # "Never push to main" summarised as "pushed to main" is not a keep.
        return Verdict(item, PARTIAL, score, _short(evidence))
    status = KEPT if score >= KEPT_AT else PARTIAL if score >= PARTIAL_AT else DROPPED
    return Verdict(item, status, score, _short(evidence) if status != DROPPED else "")


def _short(s: str, n: int = 200) -> str:
    s = " ".join(s.split())
    return s if len(s) <= n else s[: n - 1] + "…"


def audit(items: list[Item], summary_text: str) -> list[Verdict]:
    summary = Summary(summary_text)
    return [judge(it, summary) for it in items]
