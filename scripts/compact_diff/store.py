"""Per-session state on disk.

Root, in order of preference: ``$COMPACT_DIFF_HOME``, ``$CLAUDE_PLUGIN_DATA`` (set by
Claude Code for plugin hooks), then ``$XDG_STATE_HOME/compact-diff`` or
``~/.local/state/compact-diff``.
"""

from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from typing import Any


def root() -> Path:
    for var in ("COMPACT_DIFF_HOME", "CLAUDE_PLUGIN_DATA"):
        val = os.environ.get(var)
        if val:
            return Path(val)
    xdg = os.environ.get("XDG_STATE_HOME") or os.path.join(os.path.expanduser("~"), ".local", "state")
    return Path(xdg) / "compact-diff"


def _safe(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]", "-", name or "unknown")[:120]


def session_dir(session_id: str) -> Path:
    d = root() / "sessions" / _safe(session_id)
    d.mkdir(parents=True, exist_ok=True)
    return d


def flag_path(session_id: str) -> Path:
    """A non-empty ``flags/`` dir tells the cheap shell guard in hooks.json to start Python."""
    return root() / "flags" / _safe(session_id)


def set_flag(session_id: str) -> None:
    p = flag_path(session_id)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(str(time.time()), encoding="utf-8")


def clear_flag(session_id: str) -> None:
    try:
        flag_path(session_id).unlink()
    except OSError:
        pass


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


def read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def stamp() -> str:
    return time.strftime("%Y%m%dT%H%M%S", time.localtime()) + f"-{int(time.time() * 1000) % 1000:03d}"


def latest_report(cwd: str | None = None, session_id: str | None = None) -> Path | None:
    base = root() / "sessions"
    if session_id:
        candidates = list((base / _safe(session_id)).glob("report-*.json"))
    else:
        candidates = list(base.glob("*/report-*.json"))
    best: tuple[float, Path] | None = None
    for p in candidates:
        if cwd:
            data = read_json(p) or {}
            if os.path.realpath(data.get("meta", {}).get("cwd", "")) != os.path.realpath(cwd):
                continue
        m = p.stat().st_mtime
        if best is None or m > best[0]:
            best = (m, p)
    return best[1] if best else None


def log_error(msg: str) -> None:
    try:
        p = root() / "errors.log"
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "a", encoding="utf-8") as fh:
            fh.write(f"{stamp()} {msg}\n")
    except OSError:
        pass
