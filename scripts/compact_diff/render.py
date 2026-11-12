"""Turn items and verdicts into text: the pre-compact steering note, the report, and
the post-compact re-injection block."""

from __future__ import annotations

from collections import Counter
from typing import Iterable

from .extract import Item
from .match import DROPPED, KEPT, PARTIAL, Verdict

KIND_TITLES = {
    "instruction": "User instructions",
    "todo": "Open tasks",
    "error": "Unresolved errors",
    "file": "Files edited",
    "decision": "Decisions",
}
KIND_ORDER = ["instruction", "todo", "error", "file", "decision"]
STEER_LIMIT = 6000
INJECT_LIMIT = 8000


def _by_kind(items: Iterable[Item]) -> dict[str, list[Item]]:
    out: dict[str, list[Item]] = {k: [] for k in KIND_ORDER}
    for it in items:
        out.setdefault(it.kind, []).append(it)
    return out


def _bullets(items: list[Item], with_detail: bool = False) -> list[str]:
    lines = []
    for it in items:
        extra = f" ({it.detail})" if with_detail and it.detail else ""
        lines.append(f"- {it.text}{extra}")
    return lines


def _fit(lines: list[str], limit: int) -> str:
    out, size = [], 0
    for ln in lines:
        if size + len(ln) + 1 > limit:
            out.append("- … (truncated by compact-diff)")
            break
        out.append(ln)
        size += len(ln) + 1
    return "\n".join(out)


def steering(items: list[Item]) -> str:
    """Stdout of the PreCompact hook. Claude Code appends it to the summariser's instructions."""
    groups = _by_kind(i for i in items if i.kind != "decision")
    if not any(groups.values()):
        return ""
    lines = [
        "The summary must carry the following working state forward explicitly. "
        "Quote the user instructions verbatim; they still apply after compaction.",
    ]
    for kind in KIND_ORDER:
        if kind == "decision" or not groups.get(kind):
            continue
        lines.append(f"{KIND_TITLES[kind]}:")
        lines.extend(_bullets(groups[kind]))
    return _fit(lines, STEER_LIMIT)


def stats(verdicts: list[Verdict]) -> dict[str, dict[str, int]]:
    table: dict[str, Counter] = {}
    for v in verdicts:
        table.setdefault(v.item.kind, Counter())[v.status] += 1
    return {k: dict(c) for k, c in table.items()}


def one_line(verdicts: list[Verdict]) -> str:
    c = Counter(v.status for v in verdicts)
    lost = [v for v in verdicts if v.status != KEPT]
    kinds = Counter(v.item.kind for v in lost)
    detail = ", ".join(f"{n} {KIND_TITLES.get(k, k).lower()}" for k, n in kinds.most_common())
    base = f"compact-diff: summary kept {c[KEPT]}/{len(verdicts)} tracked items"
    if not lost:
        return base + "."
    return f"{base}; {c[DROPPED]} dropped, {c[PARTIAL]} partial ({detail})."


def report_markdown(verdicts: list[Verdict], meta: dict) -> str:
    c = Counter(v.status for v in verdicts)
    lines = [
        "# compact-diff report",
        "",
        f"- Trigger: `{meta.get('trigger', '?')}`  ",
        f"- When: {meta.get('when', '?')}  ",
        f"- Project: `{meta.get('cwd', '?')}`  ",
        f"- Summary steered by PreCompact hook: {'yes' if meta.get('steered') else 'no'}  ",
        f"- Re-injected after compaction: {meta.get('reinjected', 0)} item(s)",
    ]
    if str(meta.get("snapshot", "")).startswith("late"):
        lines.append(f"- Snapshot: {meta['snapshot']}")
    lines += [
        "",
        f"**Kept {c[KEPT]} · Partial {c[PARTIAL]} · Dropped {c[DROPPED]}** of {len(verdicts)} tracked items.",
        "",
        "| Kind | Kept | Partial | Dropped |",
        "| :- | -: | -: | -: |",
    ]
    st = stats(verdicts)
    for kind in KIND_ORDER:
        if kind in st:
            s = st[kind]
            lines.append(f"| {KIND_TITLES[kind]} | {s.get(KEPT, 0)} | {s.get(PARTIAL, 0)} | {s.get(DROPPED, 0)} |")
    for status, title in ((DROPPED, "Dropped"), (PARTIAL, "Partially kept"), (KEPT, "Kept")):
        group = [v for v in verdicts if v.status == status]
        if not group:
            continue
        lines += ["", f"## {title}", ""]
        for kind in KIND_ORDER:
            sub = [v for v in group if v.item.kind == kind]
            if not sub:
                continue
            lines.append(f"**{KIND_TITLES[kind]}**")
            lines.append("")
            for v in sub:
                extra = f" — _{v.item.detail}_" if v.item.detail else ""
                lines.append(f"- {v.item.text}{extra}")
                if status == PARTIAL and v.evidence:
                    lines.append(f"  - closest in summary ({v.score:.0%}): “{v.evidence}”")
            lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def should_reinject(v: Verdict) -> bool:
    if v.status == KEPT:
        return False
    if v.item.priority == "critical":
        return True  # instructions and open todos: re-inject even if only partly kept
    if v.item.priority == "high":
        return v.status == DROPPED
    return v.status == DROPPED and v.item.kind == "decision"


def reinjection(verdicts: list[Verdict]) -> tuple[str, int]:
    picked = [v.item for v in verdicts if should_reinject(v)]
    if not picked:
        return "", 0
    groups = _by_kind(picked)
    lines = [
        "compact-diff: the items below were part of this session's working state before the last "
        "compaction, and the compaction summary does not carry them (or carries them only in part). "
        "They are restored here verbatim from the transcript.",
    ]
    notes = {
        "instruction": "User instructions given earlier in this session (still in force unless the user changed them later):",
        "todo": "Tasks that were still open:",
        "error": "Errors that had not been resolved:",
        "file": "Files edited before compaction:",
        "decision": "Decisions stated before compaction:",
    }
    for kind in KIND_ORDER:
        if groups.get(kind):
            lines.append(notes[kind])
            lines.extend(_bullets(groups[kind], with_detail=kind in ("error", "file")))
    return _fit(lines, INJECT_LIMIT), len(picked)
