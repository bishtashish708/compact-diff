---
description: Show what the last compaction dropped from this session, and what compact-diff re-injected
allowed-tools: Bash(python3 "${CLAUDE_PLUGIN_ROOT}/scripts/cdiff.py" report --here --show-injected)
---

The latest compact-diff report for this project:

!`python3 "${CLAUDE_PLUGIN_ROOT}/scripts/cdiff.py" report --here --show-injected`

Show the report above to the user as-is. Then, in two or three sentences, point out anything in the "Dropped" or "Partially kept" sections that matters for the work in progress. Do not re-run the command.
