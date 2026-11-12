"""Extract the working state that compaction must not lose.

Five kinds of item, each from a cheap, deterministic heuristic (no model calls):

* ``instruction`` - constraints the user typed ("don't touch X", "always run Y").
  Scanned across the whole transcript, because user rules outlive compactions.
* ``file`` - files Claude created or edited in the current context window.
* ``error`` - tool errors in the current window that were not followed by a
  successful retry of the same command.
* ``todo`` - open tasks from TodoWrite / TaskCreate+TaskUpdate.
* ``decision`` - sentences where Claude stated a choice ("going with X instead of Y").
"""

from __future__ import annotations

import hashlib
import os
import re
from dataclasses import asdict, dataclass, field
from typing import Any, Iterable

from . import transcript as tx

PRIORITY = {"instruction": "critical", "todo": "critical", "error": "high", "file": "high", "decision": "normal"}

EDIT_TOOLS = {"Edit", "Write", "MultiEdit", "NotebookEdit", "str_replace_based_edit_tool"}

# Sentences a user writes when they set a rule for the session.
_INSTRUCTION_RE = re.compile(
    r"\b(don'?t|do not|not|never|always|must|mustn'?t|make sure|ensure|only|avoid|keep|prefer|"
    r"instead of|rather than|stop|no longer|should(?:n'?t)?|need to|has to|have to|"
    r"remember|important|careful|not allowed|forbidden|required?)\b",
    re.I,
)
_DECISION_RE = re.compile(
    r"\b(decided|decision|going with|go with|chose|choose|picked|i'?ll use|we'?ll use|"
    r"switch(?:ed|ing)? to|instead of|rather than|settled on|opted)\b",
    re.I,
)
# Matched at the start of an output line, so prose that merely mentions "ValueError"
# (e.g. a script printing "16 cases expect ValueError") is not an error.
_ERROR_LINE_RE = re.compile(
    r"^\s*(?:E\s+)?(?:Traceback \(most recent call last\)|[A-Za-z_.]*(?:Error|Exception)\b(?::|$)|"
    r"FAILED\b|FAIL:|ERROR:|ERROR\b.*::|error(?:\[[A-Z0-9]+\])?:|fatal:|panic:|npm ERR!|"
    r".*: command not found|.*No such file or directory|.*Permission denied|Exit code [1-9])"
)
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+|\n+")
_TASK_ID_RE = re.compile(r"#\s*(\d+)")
_CODE_FENCE_RE = re.compile(r"```.*?```", re.S)

MAX_INSTRUCTIONS = 30
MAX_ERRORS = 8
RECENT_TOOL_CALLS = 40  # errors older than this many tool calls are assumed handled
MAX_DECISIONS = 10
MAX_ITEM_CHARS = 300


@dataclass
class Item:
    kind: str
    text: str
    priority: str = "normal"
    anchors: list[str] = field(default_factory=list)
    detail: str = ""

    @property
    def id(self) -> str:
        digest = hashlib.sha1(f"{self.kind}:{self.text}".encode()).hexdigest()[:10]
        return f"{self.kind}-{digest}"

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["id"] = self.id
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Item":
        return cls(
            kind=d["kind"],
            text=d["text"],
            priority=d.get("priority", "normal"),
            anchors=list(d.get("anchors", [])),
            detail=d.get("detail", ""),
        )


def _clip(text: str, n: int = MAX_ITEM_CHARS) -> str:
    text = " ".join(text.split())
    return text if len(text) <= n else text[: n - 1].rstrip() + "…"


_ABBREV = {"e.g.": "e\x00g\x00", "i.e.": "i\x00e\x00", "etc.": "etc\x00", "vs.": "vs\x00", "approx.": "approx\x00"}


def sentences(text: str) -> list[str]:
    text = _CODE_FENCE_RE.sub(" ", text)
    for k, v in _ABBREV.items():
        text = text.replace(k, v).replace(k.capitalize(), v.capitalize())
    out = []
    for s in _SENTENCE_SPLIT_RE.split(text):
        if s and s.strip():
            out.append(s.replace("\x00", ".").strip(" -*•\t"))
    return out


def _dedupe(items: Iterable[Item]) -> list[Item]:
    seen: set[str] = set()
    out: list[Item] = []
    for it in items:
        key = it.kind + ":" + it.text.lower()
        if key not in seen:
            seen.add(key)
            out.append(it)
    return out


# Unambiguous rule words. Weaker cues ("not", "only", "keep") also appear in narrative
# and spec prose, so the filters below only drop a sentence when none of these is present.
_STRONG_RULE_RE = re.compile(
    r"\b(don'?t|do not|never|always|must|mustn'?t|make sure|ensure|avoid|prefer|instead of|rather than)\b", re.I
)
_PAREN_RE = re.compile(r"\([^()]*\)")
_TERMINAL_RE = re.compile(r"[.!?][\"'”’)\]]*$")
_QUOTE_START_RE = re.compile(r"""^\s*["“'‘]""")


def looks_like_rule(sentence: str) -> bool:
    """Heuristic: is this sentence a rule the user set, rather than spec prose or narrative?

    Long prompts often paste specs, prompt templates and background notes. Measured on
    labelled compactions these filters raise instruction precision from 82% to 89%
    (see launch/EVALUATION.md). Rejected:

    * quoted text: UI copy or prompt templates (``"Reply with valid YAML only"``)
    * headings: short, no sentence-ending punctuation, and the rule word only appears
      inside parentheses (``Pricing notes (only for reference)``)
    * past-tense narrative: ``Tried the cache first, it was not faster.``
    """
    if not _INSTRUCTION_RE.search(sentence):
        return False
    strong = bool(_STRONG_RULE_RE.search(sentence))
    if _QUOTE_START_RE.match(sentence):
        return False
    stripped = sentence.rstrip()
    if (
        not strong
        and len(stripped) < 120
        and not _TERMINAL_RE.search(stripped)
        and not _INSTRUCTION_RE.search(_PAREN_RE.sub(" ", stripped))
    ):
        return False
    first = re.match(r"^([A-Z][a-z]+)\s", sentence)
    if not strong and first and first.group(1).endswith("ed") and first.group(1) not in ("Need", "Used"):
        return False
    return True


def extract_instructions(entries: list[dict[str, Any]]) -> list[Item]:
    items: list[Item] = []
    for entry in entries:
        if not tx.is_human_prompt(entry):
            continue
        text = tx.strip_reminders(tx.message_text(entry))
        for s in sentences(text):
            if len(s) < 12 or len(s) > 500:
                continue
            if looks_like_rule(s):
                items.append(Item("instruction", _clip(s), PRIORITY["instruction"]))
    return _dedupe(items)[-MAX_INSTRUCTIONS:]


def _rel(path: str, cwd: str | None) -> str:
    if cwd and path.startswith(cwd.rstrip("/") + "/"):
        return path[len(cwd.rstrip("/")) + 1 :]
    return path


def extract_files(entries: list[dict[str, Any]], cwd: str | None = None) -> list[Item]:
    counts: dict[str, int] = {}
    for _entry, block in tx.iter_tool_uses(entries):
        if block.get("name") not in EDIT_TOOLS:
            continue
        inp = block.get("input") or {}
        path = inp.get("file_path") or inp.get("notebook_path") or inp.get("path")
        if isinstance(path, str) and path:
            counts[path] = counts.get(path, 0) + 1
    items = []
    for path, n in counts.items():
        rel = _rel(path, cwd)
        items.append(
            Item("file", rel, PRIORITY["file"], anchors=[os.path.basename(path)], detail=f"{n} edit(s)")
        )
    return items


def _error_rank(line: str) -> int:
    if re.search(r"\b[A-Za-z_.]*(?:Error|Exception)\b", line) and not line.startswith("Traceback"):
        return 3  # the exception itself is the most specific line
    if re.match(r"\s*(FAIL|FAILED|ERROR|error|fatal|panic|npm ERR)", line):
        return 2
    if line.startswith("Exit code"):
        return 0
    return 1


def _error_line(text: str) -> str | None:
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    hits = [ln for ln in lines if _ERROR_LINE_RE.search(ln)]
    if not hits:
        return None
    best = max(_error_rank(h) for h in hits)
    return [h for h in hits if _error_rank(h) == best][-1]


def _error_anchors(line: str) -> list[str]:
    anchors = re.findall(r"\b[A-Z][A-Za-z]+(?:Error|Exception)\b", line)
    anchors += [os.path.basename(p) for p in re.findall(r"[\w./-]+\.[a-z]{1,5}\b", line)]
    return sorted(set(anchors))[:4]


def extract_errors(entries: list[dict[str, Any]]) -> list[Item]:
    results = tx.tool_results(entries)
    seen_keys: dict[str, Item] = {}
    order: list[str] = []
    uses = list(tx.iter_tool_uses(entries))
    for _entry, block in uses[-RECENT_TOOL_CALLS:]:
        name = block.get("name", "")
        inp = block.get("input") or {}
        key = name + ":" + str(inp.get("command") or inp.get("file_path") or inp.get("url") or "")
        res = results.get(str(block.get("id")))
        if res is None:
            continue
        line = _error_line(res["text"]) if (res["is_error"] or name == "Bash") else None
        if res["is_error"] and line is None:
            line = _clip(res["text"].strip().splitlines()[0] if res["text"].strip() else "tool error", 200)
        if line is None:
            # Success: a clean re-run of the same command resolves its earlier error.
            if key in seen_keys:
                del seen_keys[key]
            continue
        where = inp.get("command") or inp.get("file_path") or name
        item = Item(
            "error",
            _clip(line, 200),
            PRIORITY["error"],
            anchors=_error_anchors(line),
            detail=f"{name}: {_clip(str(where), 120)}",
        )
        seen_keys[key] = item
        order.append(key)
    unresolved = [seen_keys[k] for k in dict.fromkeys(order) if k in seen_keys]
    return _dedupe(unresolved)[-MAX_ERRORS:]


def extract_todos(entries: list[dict[str, Any]]) -> list[Item]:
    results = tx.tool_results(entries)
    todo_write: list[dict[str, Any]] | None = None
    tasks: dict[str, dict[str, str]] = {}
    seq = 0
    for _entry, block in tx.iter_tool_uses(entries):
        name = block.get("name")
        inp = block.get("input") or {}
        if name == "TodoWrite" and isinstance(inp.get("todos"), list):
            todo_write = [t for t in inp["todos"] if isinstance(t, dict)]
        elif name == "TaskCreate":
            seq += 1
            res = results.get(str(block.get("id")), {}).get("text", "")
            m = _TASK_ID_RE.search(res)
            tid = m.group(1) if m else str(seq)
            tasks[tid] = {"subject": str(inp.get("subject") or inp.get("description") or ""), "status": "pending"}
        elif name == "TaskUpdate":
            tid = str(inp.get("taskId", ""))
            if tid in tasks and inp.get("status"):
                tasks[tid]["status"] = str(inp["status"])
            if tid in tasks and inp.get("subject"):
                tasks[tid]["subject"] = str(inp["subject"])
    items: list[Item] = []
    for t in todo_write or []:
        if t.get("status") != "completed" and t.get("content"):
            items.append(Item("todo", _clip(str(t["content"])), PRIORITY["todo"], detail=str(t.get("status", ""))))
    for t in tasks.values():
        if t["status"] not in ("completed", "deleted") and t["subject"]:
            items.append(Item("todo", _clip(t["subject"]), PRIORITY["todo"], detail=t["status"]))
    return _dedupe(items)


def extract_decisions(entries: list[dict[str, Any]]) -> list[Item]:
    items: list[Item] = []
    for entry in entries:
        if entry.get("type") != "assistant":
            continue
        for block in tx.content_blocks(entry):
            if block.get("type") != "text":
                continue
            for s in sentences(block.get("text", "")):
                if 20 <= len(s) <= 400 and _DECISION_RE.search(s):
                    items.append(Item("decision", _clip(s), PRIORITY["decision"]))
    return _dedupe(items)[-MAX_DECISIONS:]


def extract_state(
    all_entries: list[dict[str, Any]], window: list[dict[str, Any]], cwd: str | None = None
) -> list[Item]:
    """Working state to check against a summary.

    ``all_entries`` is the whole transcript (for durable user instructions);
    ``window`` is the context window being compacted.
    """
    return (
        extract_instructions(all_entries)
        + extract_todos(all_entries)
        + extract_errors(window)
        + extract_files(window, cwd)
        + extract_decisions(window)
    )
