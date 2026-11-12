"""Replay the compact-diff hook lifecycle on the synthetic demo session.

    python3 launch/demo/demo.py            # writes launch/demo/cast.json and demo.txt
    swift launch/demo/render.swift         # renders cast.json -> demo.gif (macOS)

This does NOT run Claude Code. It pipes real hook payloads into the real
``scripts/cdiff.py`` (PreCompact -> SessionStart -> PostCompact -> UserPromptSubmit
-> report) using the invented session from ``tests/fixtures/build.py``. The
conversation lines are styled to look like a Claude Code terminal; the compact-diff
lines are the hook code's actual output. State goes to a throwaway temp dir, and
its path is displayed as ``~/.local/state/compact-diff``.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
import textwrap

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.join(ROOT, "tests", "fixtures"))
import build  # noqa: E402

COLS, ROWS = 100, 30
SHOWN_STATE = "~/.local/state/compact-diff"


class Screen:
    """A scrolling terminal buffer that records frames for the renderer."""

    def __init__(self) -> None:
        self.lines: list[list[list[str]]] = []  # line = [[text, color], ...]
        self.frames: list[dict] = []
        self.plain: list[str] = []

    def snap(self, delay: float) -> None:
        self.frames.append({"lines": [list(map(list, ln)) for ln in self.lines[-ROWS:]], "delay": delay})

    def add(self, segs: list[tuple[str, str]], delay: float = 0.0, record: bool = True) -> None:
        # Wrap on the joined text; keep colour of the first segment for continuation lines.
        text = "".join(t for t, _ in segs)
        if record:
            self.plain.append(text)
        if len(text) <= COLS:
            self.lines.append([list(s) for s in segs])
        else:
            wrapped = textwrap.wrap(text, COLS, subsequent_indent="    ", drop_whitespace=False) or [""]
            color = segs[-1][1]
            first = wrapped[0]
            # keep the leading segment colour (prompt marker) on the first line
            lead_t, lead_c = segs[0]
            if first.startswith(lead_t) and lead_t:
                self.lines.append([[lead_t, lead_c], [first[len(lead_t):], color]])
            else:
                self.lines.append([[first, color]])
            for w in wrapped[1:]:
                self.lines.append([[w, color]])
        if delay:
            self.snap(delay)

    def blank(self, delay: float = 0.0) -> None:
        self.add([("", "fg")], delay)

    def type_prompt(self, text: str, chunk: int = 9, step: float = 0.06, hold: float = 0.5) -> None:
        """Type a user prompt in chunks, then hold."""
        self.plain.append("> " + text)
        start = len(self.lines)
        for i in range(chunk, len(text) + chunk, chunk):
            del self.lines[start:]
            self.add([("> ", "prompt"), (text[:i], "bright")], record=False)
            self.snap(step)
        self.frames[-1]["delay"] = hold


def run_hook(event: str, payload: dict, env: dict) -> str:
    r = subprocess.run(
        [sys.executable, os.path.join(ROOT, "scripts", "cdiff.py"), "hook", event],
        input=json.dumps(payload), capture_output=True, text=True, env=env, check=True,
    )
    return r.stdout


def main() -> None:
    tmp = tempfile.mkdtemp(prefix="compact-diff-demo-")
    state = os.path.join(tmp, "state")
    env = {**os.environ, "COMPACT_DIFF_HOME": state, "COMPACT_DIFF_STEER": "0"}
    env.pop("CLAUDE_PLUGIN_DATA", None)
    pre = os.path.join(tmp, "session.jsonl")
    with open(pre, "w", encoding="utf-8") as fh:
        for e in build.pre_compact_entries():
            fh.write(json.dumps(e) + "\n")
    base = {"session_id": "demo-session", "transcript_path": pre, "cwd": build.CWD}

    def clean(s: str) -> str:
        s = s.replace(state, SHOWN_STATE).replace(tmp, "/tmp/demo")
        return re.sub(r"report-\d{8}T\d{6}-\d{3}", "report-<time>", s)

    sc = Screen()
    sc.add([("replay of the real compact-diff hook code on a synthetic session · not a screen capture", "dim")])
    sc.add([("╭─ claude ", "accent"), ("~/webapp", "dim"), ("   plugin: compact-diff", "dim")], 1.2)
    sc.blank()

    u1 = build.entries()[0]["message"]["content"]
    sc.type_prompt(u1, chunk=12, hold=0.8)
    sc.add([("⏺ ", "accent"), ("Going with PKCE flow instead of implicit grant, since this is a public client.", "fg")], 0.7)
    sc.add([("⏺ ", "accent"), ("Write ", "bright"), ("src/auth.ts", "fg"), ("  ·  Edit ", "bright"), ("src/login.tsx", "fg"), ("  ·  Edit ", "bright"), ("src/middleware/session.ts", "fg")], 0.7)
    sc.add([("⏺ ", "accent"), ("Bash ", "bright"), ("pnpm lint", "fg"), ("  ✗ unused var → fixed → ", "dim"), ("✓", "green")], 0.7)
    sc.blank()
    u2 = next(e for e in build.entries() if e.get("uuid") == "u2")["message"]["content"]
    sc.type_prompt(u2, chunk=12, hold=0.6)
    sc.add([("⏺ ", "accent"), ("Write ", "bright"), ("tests/auth.test.ts", "fg"), ("  ·  Bash ", "bright"), ("pnpm test tests/auth.test.ts", "fg")], 0.4)
    sc.add([("  ⎿ ", "dim"), ("FAIL  TypeError: Cannot read properties of undefined (reading 'refreshToken')", "red")], 0.9)
    sc.blank()

    sc.type_prompt("/compact", chunk=8, hold=0.4)
    run_hook("PreCompact", {**base, "hook_event_name": "PreCompact", "trigger": "manual", "custom_instructions": None}, env)
    pending = json.load(open(os.path.join(state, "sessions", "demo-session", "pending.json")))
    sc.add([("  ⎿ ", "dim"), ("PreCompact [compact-diff]: ", "dim"), (f"snapshot of {len(pending['items'])} working-state items saved", "fg")], 0.6)
    run_hook("SessionStart", {**base, "hook_event_name": "SessionStart", "source": "compact"}, env)
    run_hook("PostCompact", {**base, "hook_event_name": "PostCompact", "trigger": "manual", "compact_summary": build.SUMMARY}, env)
    sc.add([("  ⎿ ", "dim"), ("Compacted (182k → 9k tokens)", "fg")], 1.0)
    sc.blank()

    sc.type_prompt("ok continue", chunk=11, hold=0.3)
    out = json.loads(run_hook("Deliver", {**base, "hook_event_name": "UserPromptSubmit", "prompt": "ok continue"}, env))
    msg = clean(out["systemMessage"])
    verdict, _, report_path = msg.partition(" Report: ")
    sc.add([("  ⎿ ", "dim"), (verdict, "yellow")])
    sc.add([("    Report: ", "dim"), (report_path, "dim")], 0.4)
    n_injected = out["hookSpecificOutput"]["additionalContext"].count("\n- ")
    sc.add([("  ⎿ ", "dim"), (f"UserPromptSubmit [compact-diff]: re-injected {n_injected} dropped items as context", "green")], 3.2)
    sc.blank()

    sc.type_prompt("/compact-diff:report", chunk=10, hold=0.3)
    r = subprocess.run(
        [sys.executable, os.path.join(ROOT, "scripts", "cdiff.py"), "report", "--cwd", build.CWD, "--show-injected"],
        capture_output=True, text=True, env=env, check=True,
    )
    report = clean(r.stdout)
    # Show the report the way the slash command prints it, but skip the "Kept" and
    # "Partially kept" detail so the recording stays short. demo.txt has it all.
    shown, skipping = [], False
    for ln in report.splitlines():
        if ln.startswith("## "):
            skipping = ln in ("## Kept", "## Partially kept")
        if skipping or ln.startswith("- When:"):
            continue
        shown.append(ln)
    # Markdown table -> aligned columns, as a terminal markdown renderer would show it.
    rows = [ln for ln in shown if ln.startswith("|") and not ln.startswith("| :-")]
    if rows:
        cells = [[c.strip() for c in r.strip("|").split("|")] for r in rows]
        widths = [max(len(c[i]) for c in cells) for i in range(len(cells[0]))]
        table = {r: "  ".join(c.ljust(w) if i == 0 else c.rjust(w) for i, (c, w) in enumerate(zip(cl, widths)))
                 for r, cl in zip(rows, cells)}
        shown = [table.get(ln, ln) for ln in shown if not ln.startswith("| :-")]
    section = ""
    for ln in shown:
        if ln.startswith("## "):
            section = ln
        color = "fg"
        if ln.startswith("#"):
            color = "accent"
        elif ln.startswith("**"):
            color = "bright"
        elif ln.startswith("- ") and section == "## Re-injected context":
            color = "green"
        elif ln.startswith("- ") and section == "## Dropped":
            color = "red"
        sc.add([("  " + ln.replace("**", "").replace("`", "").replace(" — _", " — ").rstrip("_ "), color)], 0.07)
    sc.frames[-1]["delay"] = 5.0
    sc.plain[-len(shown):] = ["  " + ln for ln in report.splitlines()]

    with open(os.path.join(HERE, "cast.json"), "w", encoding="utf-8") as fh:
        json.dump({"cols": COLS, "rows": ROWS, "frames": sc.frames}, fh)
    with open(os.path.join(HERE, "demo.txt"), "w", encoding="utf-8") as fh:
        fh.write("\n".join(sc.plain) + "\n")
    total = sum(f["delay"] for f in sc.frames)
    print(f"{len(sc.frames)} frames, {total:.1f}s -> {HERE}/cast.json, demo.txt")


if __name__ == "__main__":
    main()
