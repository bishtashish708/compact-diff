"""Build blind labelling packets from Claude Code transcripts.

    python3 launch/eval/make_packets.py OUT_DIR transcript.jsonl [transcript.jsonl ...]

For every compaction recorded in each transcript, writes:
  OUT_DIR/<id>.json       items + summary only (give these to annotators)
  OUT_DIR/<id>.full.json  the same plus extraction metadata (for scoring; keep away from annotators)

Packets contain private transcript text. Keep them local; never commit them.
"""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "scripts"))
from compact_diff import extract  # noqa: E402
from compact_diff import transcript as tx  # noqa: E402


def main(out, paths):
    os.makedirs(out, exist_ok=True)
    for path in paths:
        sid = os.path.basename(path)[:-6][:8]
        entries = tx.load(path)
        segs = tx.segments(entries)
        cwd = next((e.get("cwd") for e in entries if e.get("cwd")), None)
        seen = []
        for seg in segs[:-1]:
            seen.extend(seg.entries)
            if not seg.summary:
                continue
            items = extract.extract_state(seen, seg.entries, cwd)
            pid = f"{sid}-{seg.index}"
            full = {"id": pid, "items": [i.to_dict() for i in items], "summary": seg.summary}
            blind = {"id": pid, "summary": seg.summary,
                     "items": [{k: d[k] for k in ("id", "kind", "text", "detail")} for d in full["items"]]}
            json.dump(blind, open(os.path.join(out, pid + ".json"), "w"), indent=1)
            json.dump(full, open(os.path.join(out, pid + ".full.json"), "w"))
            print(pid, len(items), "items")


if __name__ == "__main__":
    if len(sys.argv) < 3:
        sys.exit(__doc__)
    main(sys.argv[1], sys.argv[2:])
