#!/bin/sh
# compact-diff delivery guard for the UserPromptSubmit and PostToolUse hooks.
# These hooks fire often, so this only starts Python when a compaction report is
# waiting to be delivered (the state directory's flags/ folder is non-empty).
state="${COMPACT_DIFF_HOME:-${CLAUDE_PLUGIN_DATA:-$HOME/.local/state/compact-diff}}"
[ -n "$(ls -A "$state/flags" 2>/dev/null)" ] || exit 0
exec python3 "$(dirname "$0")/cdiff.py" hook Deliver
