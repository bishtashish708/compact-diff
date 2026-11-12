"""Command line: hook entry point plus offline tools.

    cdiff.py hook <PreCompact|PostCompact|SessionStart>   (reads the payload on stdin)
    cdiff.py audit <transcript.jsonl> [--json]            what each past compaction dropped
    cdiff.py scan [DIR] [--limit N] [--json]              aggregate over many transcripts
    cdiff.py report [--cwd DIR] [--session ID]            print the latest live report
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import sys
from collections import Counter, defaultdict

from . import extract, hooks, match, render, store
from . import transcript as tx


def audit_transcript(path: str) -> list[dict]:
    """Audit every compaction recorded in a transcript. Read-only."""
    entries = tx.load(path)
    segs = tx.segments(entries)
    out = []
    seen: list[dict] = []
    cwd = next((e.get("cwd") for e in entries if e.get("cwd")), None)
    for seg in segs[:-1]:
        seen.extend(seg.entries)
        if not seg.summary:
            continue
        items = extract.extract_state(seen, seg.entries, cwd)
        verdicts = match.audit(items, seg.summary)
        meta = (seg.boundary or {}).get("compactMetadata") or {}
        out.append(
            {
                "transcript": path,
                "compaction": seg.index + 1,
                "trigger": meta.get("trigger"),
                "pre_tokens": meta.get("preTokens"),
                "post_tokens": meta.get("postTokens"),
                "verdicts": verdicts,
            }
        )
    return out


def _cmd_audit(args: argparse.Namespace) -> int:
    results = audit_transcript(args.transcript)
    if args.json:
        print(json.dumps([{**r, "verdicts": [v.to_dict() for v in r["verdicts"]]} for r in results], indent=2))
        return 0
    if not results:
        print("No compactions with a recorded summary in this transcript.")
        return 0
    for r in results:
        meta = {
            "trigger": r["trigger"],
            "when": f"compaction #{r['compaction']} ({r['pre_tokens']} → {r['post_tokens']} tokens)",
            "cwd": os.path.basename(os.path.dirname(r["transcript"])),
            "steered": False,
            "reinjected": 0,
        }
        print(render.report_markdown(r["verdicts"], meta))
    return 0


def _cmd_scan(args: argparse.Namespace) -> int:
    base = os.path.expanduser(args.dir)
    files = sorted(glob.glob(os.path.join(base, "**", "*.jsonl"), recursive=True), key=os.path.getmtime, reverse=True)
    totals: dict[str, Counter] = defaultdict(Counter)
    n_compactions = n_files = 0
    for path in files:
        if args.limit and n_compactions >= args.limit:
            break
        try:
            with open(path, encoding="utf-8", errors="replace") as fh:
                if "compact_boundary" not in fh.read():
                    continue
        except OSError:
            continue
        results = audit_transcript(path)
        if results:
            n_files += 1
        for r in results:
            n_compactions += 1
            for v in r["verdicts"]:
                totals[v.item.kind][v.status] += 1
    if args.json:
        print(json.dumps({"transcripts": n_files, "compactions": n_compactions, "by_kind": totals}, indent=2))
        return 0
    print(f"Scanned {n_files} transcript(s), {n_compactions} compaction(s) under {base}\n")
    print(f"{'Kind':<20}{'Tracked':>9}{'Kept':>8}{'Partial':>9}{'Dropped':>9}{'Lost %':>9}")
    for kind in render.KIND_ORDER:
        c = totals.get(kind)
        if not c:
            continue
        total = sum(c.values())
        lost = c[match.DROPPED] + c[match.PARTIAL] * 0.5
        print(
            f"{render.KIND_TITLES[kind]:<20}{total:>9}{c[match.KEPT]:>8}{c[match.PARTIAL]:>9}"
            f"{c[match.DROPPED]:>9}{lost / total:>9.0%}"
        )
    print("\nLost % counts a partial keep as half lost. Heuristic, lexical matching — see README.")
    return 0


def _cmd_report(args: argparse.Namespace) -> int:
    if args.here and not args.cwd:
        args.cwd = os.getcwd()
    path = store.latest_report(cwd=args.cwd, session_id=args.session)
    if path is None and args.cwd and not args.session:
        path = store.latest_report()
    if path is None:
        print(f"No compact-diff reports yet (state dir: {store.root()}). They appear after the next /compact.")
        return 0
    report = store.read_json(path) or {}
    md = report.get("markdown_path")
    if md and os.path.exists(md):
        with open(md, encoding="utf-8") as fh:
            print(fh.read())
    else:
        print(report.get("one_line", ""))
    if args.show_injected and report.get("reinjection"):
        print("\n## Re-injected context\n\n" + report["reinjection"])
    return 0


def _cmd_hook(args: argparse.Namespace) -> int:
    out, code = hooks.run(args.event, sys.stdin.read())
    if out:
        sys.stdout.write(out)
        if not out.endswith("\n"):
            sys.stdout.write("\n")
    return code


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="compact-diff", description="See what Claude Code compaction dropped.")
    sub = p.add_subparsers(dest="cmd", required=True)

    h = sub.add_parser("hook", help="run as a Claude Code hook (payload on stdin)")
    h.add_argument("event", choices=sorted(hooks.HANDLERS))
    h.set_defaults(fn=_cmd_hook)

    a = sub.add_parser("audit", help="audit every compaction in one transcript")
    a.add_argument("transcript")
    a.add_argument("--json", action="store_true")
    a.set_defaults(fn=_cmd_audit)

    s = sub.add_parser("scan", help="aggregate audit across many transcripts")
    s.add_argument("dir", nargs="?", default="~/.claude/projects")
    s.add_argument("--limit", type=int, default=0, help="stop after N compactions")
    s.add_argument("--json", action="store_true")
    s.set_defaults(fn=_cmd_scan)

    r = sub.add_parser("report", help="print the latest live report")
    r.add_argument("--cwd", default=None)
    r.add_argument("--here", action="store_true", help="latest report for the current directory")
    r.add_argument("--session", default=None)
    r.add_argument("--show-injected", action="store_true")
    r.set_defaults(fn=_cmd_report)

    args = p.parse_args(argv)
    return args.fn(args)
