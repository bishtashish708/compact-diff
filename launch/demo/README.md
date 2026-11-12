# Demo recording

`demo.gif` (about 22 s, 875×634) replays the compact-diff lifecycle on the **synthetic** session in
[`tests/fixtures/build.py`](../../tests/fixtures/build.py). The session is invented and contains no
personal data.

It is **not a screen capture of Claude Code**. `demo.py` sends real hook payloads to the real
`scripts/cdiff.py` in this order:

1. PreCompact
2. SessionStart (fires early, as observed in Claude Code 2.1.252)
3. PostCompact, with the fixture's compaction summary
4. UserPromptSubmit, for delivery
5. `report --show-injected`

The conversation lines are styled to look like a Claude Code terminal. Every compact-diff line in
the recording is the code's actual output. The steering note is off (`COMPACT_DIFF_STEER=0`)
because the fixture summary is fixed. State goes to a temp directory, and its path is shown as
`~/.local/state/compact-diff`.

## Regenerate

```bash
python3 launch/demo/demo.py
```

```bash
swift launch/demo/render.swift
```

`demo.py` writes `cast.json` (the frames) and `demo.txt` (the plain-text transcript).
`render.swift` draws the frames in Menlo with AppKit and encodes the GIF with ImageIO. It runs on
macOS only and needs no dependencies.
