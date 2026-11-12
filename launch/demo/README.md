# Demo recordings

## `real-demo.gif`: a real Claude Code session

`real-demo.gif` (about 69 s, 875×672) is a real Claude Code 2.1.322 session on Haiku, in a throwaway
folder with a tiny `math.js`, a failing test script and nothing else. The prompts are:

1. three project rules;
2. add three functions with tests and run them;
3. add README and CHANGELOG entries;
4. `/compact`, then `continue`, where the verdict line appears;
5. `/compact-diff:report`, then "What project rules am I under?".

How it was recorded:

- Claude Code ran in a pseudo-terminal, with no user settings (`--setting-sources project,local`) and auto-memory off.
- compact-diff was loaded with `--plugin-dir`, and steering was off (`COMPACT_DIFF_STEER=0`).
- Every screen change was captured with the `pyte` terminal emulator and drawn with a variant of `render.swift`.

What was edited afterwards:

- Waits are shortened, and the verdict line and the report are held on screen for a few seconds.
- The subscription plan name and one plan promotion line are blanked.
- Nothing else is changed.

In this run the summary kept 8 of 9 tracked items, with one decision only partly kept. Short sessions often lose nothing, and that is a real result.

## `demo.gif`: replay on a synthetic session

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
