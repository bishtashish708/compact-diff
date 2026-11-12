"""Hook handlers. Each takes the hook's stdin payload and returns (stdout, exit_code).

Lifecycle of one compaction:

1. ``PreCompact``  - extract working state from the transcript, save it as
   ``pending.json``, and print a steering note that Claude Code appends to the
   summariser's instructions.
2. ``PostCompact`` - receives ``compact_summary``; diff the saved state against it and
   write ``report-*.json`` / ``report-*.md``. PostCompact cannot add context.
3. Delivery - re-inject the dropped critical items as ``additionalContext`` and show a
   one-line verdict to the user. ``SessionStart`` (source ``compact``) is the natural
   place, but in practice it fires *before* PostCompact and before the summary reaches
   the transcript (observed in Claude Code 2.1.252). So delivery is retried from the
   next ``UserPromptSubmit`` or ``PostToolUse`` hook, whichever comes first. A shell
   guard in hooks.json skips starting Python for those unless a flag file exists.
   If PostCompact never runs (older Claude Code), the summary is read from the
   transcript once it appears there.

The handlers never block compaction and never fail loudly: any exception is logged
to ``errors.log`` in the state directory and the hook exits 0.
"""

from __future__ import annotations

import json
import os
import time
from typing import Any

from . import extract, match, render, store
from . import transcript as tx

STEER_ENV = "COMPACT_DIFF_STEER"  # "0" disables the PreCompact steering note
INJECT_ENV = "COMPACT_DIFF_INJECT"  # "0" disables re-injection (report only)


def _enabled(var: str) -> bool:
    return os.environ.get(var, "1").strip().lower() not in ("0", "false", "no", "off")


def pre_compact(payload: dict[str, Any]) -> tuple[str, int]:
    path = payload.get("transcript_path") or ""
    entries = tx.load(path)
    segs = tx.segments(entries)
    cwd = payload.get("cwd")
    items = extract.extract_state(entries, segs[-1].entries, cwd)
    steer = render.steering(items) if _enabled(STEER_ENV) else ""
    sdir = store.session_dir(payload.get("session_id", "unknown"))
    store.write_json(
        sdir / "pending.json",
        {
            "created": time.time(),
            "trigger": payload.get("trigger"),
            "custom_instructions": payload.get("custom_instructions"),
            "cwd": cwd,
            "transcript_path": path,
            "compactions_before": len(segs) - 1,
            "steered": bool(steer),
            "session_id": payload.get("session_id"),
            "items": [i.to_dict() for i in items],
        },
    )
    store.set_flag(payload.get("session_id", "unknown"))
    return steer, 0


def _finish(payload: dict[str, Any], summary: str, source: str) -> dict[str, Any] | None:
    """Diff pending state against ``summary`` and persist the report. Returns the report."""
    sdir = store.session_dir(payload.get("session_id", "unknown"))
    pending = store.read_json(sdir / "pending.json")
    if not pending:
        return None
    items = [extract.Item.from_dict(d) for d in pending.get("items", [])]
    verdicts = match.audit(items, summary)
    inject_text, n = render.reinjection(verdicts)
    stamp = store.stamp()
    meta = {
        "trigger": pending.get("trigger"),
        "when": time.strftime("%Y-%m-%d %H:%M:%S"),
        "cwd": pending.get("cwd"),
        "session_id": payload.get("session_id"),
        "steered": pending.get("steered"),
        "summary_source": source,
        "reinjected": n if _enabled(INJECT_ENV) else 0,
    }
    report = {
        "meta": meta,
        "stats": render.stats(verdicts),
        "verdicts": [v.to_dict() for v in verdicts],
        "one_line": render.one_line(verdicts),
        "reinjection": inject_text,
        "injected": False,
        "summary_chars": len(summary),
    }
    md = render.report_markdown(verdicts, meta)
    report["markdown_path"] = str(sdir / f"report-{stamp}.md")
    (sdir / f"report-{stamp}.md").write_text(md, encoding="utf-8")
    store.write_json(sdir / f"report-{stamp}.json", report)
    store.write_json(sdir / "current.json", {"report": f"report-{stamp}.json"})
    os.replace(sdir / "pending.json", sdir / f"pending-{stamp}.done.json")
    return report


def post_compact(payload: dict[str, Any]) -> tuple[str, int]:
    summary = payload.get("compact_summary")
    if isinstance(summary, str) and summary.strip():
        _finish(payload, summary, "PostCompact.compact_summary")
    return "", 0


GIVE_UP_AFTER_S = 15 * 60


def _summary_from_transcript(path: str, compactions_before: int) -> str | None:
    """The summary of the compaction that ``pending.json`` was taken for, if written yet."""
    segs = tx.segments(tx.load(path))
    if len(segs) - 1 > compactions_before and segs[-2].summary:
        return segs[-2].summary
    return None


def deliver(payload: dict[str, Any], event: str) -> tuple[str, int]:
    """Inject the latest undelivered report into context, once."""
    sid = payload.get("session_id", "unknown")
    sdir = store.session_dir(sid)
    pending = store.read_json(sdir / "pending.json")
    if pending:
        summary = _summary_from_transcript(
            payload.get("transcript_path") or pending.get("transcript_path") or "",
            int(pending.get("compactions_before") or 0),
        )
        if summary:
            _finish(payload, summary, "transcript")
        else:
            if time.time() - float(pending.get("created") or 0) > GIVE_UP_AFTER_S:
                store.clear_flag(sid)  # compaction was probably blocked or never finished
            return "", 0  # PostCompact hasn't run yet; try again on the next hook
    current = store.read_json(sdir / "current.json") or {}
    rpath = sdir / current.get("report", "-")
    report = store.read_json(rpath)
    store.clear_flag(sid)
    if not report or report.get("injected"):
        return "", 0
    report["injected"] = True
    report["meta"]["delivered_via"] = event
    store.write_json(rpath, report)
    message = report["one_line"] + f" Report: {report.get('markdown_path')}"
    out: dict[str, Any] = {"systemMessage": message}
    if _enabled(INJECT_ENV) and report.get("reinjection"):
        out["hookSpecificOutput"] = {"hookEventName": event, "additionalContext": report["reinjection"]}
    return json.dumps(out), 0


def session_start(payload: dict[str, Any]) -> tuple[str, int]:
    if payload.get("source") != "compact":
        return "", 0
    return deliver(payload, "SessionStart")


def deliver_later(payload: dict[str, Any]) -> tuple[str, int]:
    event = payload.get("hook_event_name")
    if event not in ("UserPromptSubmit", "PostToolUse"):
        return "", 0
    return deliver(payload, event)


HANDLERS = {
    "PreCompact": pre_compact,
    "PostCompact": post_compact,
    "SessionStart": session_start,
    "Deliver": deliver_later,
}


def run(event: str, raw: str) -> tuple[str, int]:
    try:
        payload = json.loads(raw) if raw.strip() else {}
        if not isinstance(payload, dict):
            payload = {}
        handler = HANDLERS.get(event or payload.get("hook_event_name", ""))
        if handler is None:
            return "", 0
        return handler(payload)
    except Exception as exc:  # never break the user's session
        store.log_error(f"{event}: {type(exc).__name__}: {exc}")
        return "", 0
