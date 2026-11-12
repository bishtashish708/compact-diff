# Privacy

compact-diff runs entirely on your machine.

- **What it reads:** the Claude Code session transcript (`transcript_path`) and the hook payload that Claude Code passes to its hooks, including the compaction summary.
- **What it writes:** small JSON and Markdown files (working-state snapshots, reports, and empty flag files) in the plugin's data directory (`$CLAUDE_PLUGIN_DATA`, or `$COMPACT_DIFF_HOME` if you set it). Claude Code deletes that directory when you uninstall the plugin.
- **What it sends:** nothing. compact-diff makes no network requests, calls no model or API, and has no telemetry or analytics.
- **What reaches Claude:** after a compaction, the items the summary lost are added back to Claude's context as `additionalContext`, and Claude Code's normal data handling applies to that context. You can turn this off with `COMPACT_DIFF_INJECT=0`.
- **What it runs:** `python3` (stdlib only, no packages installed) and `sh`/`ls` for the delivery guard. All source is readable in this repository.

Questions or concerns: open an issue at https://github.com/bishtashish708/compact-diff/issues.
