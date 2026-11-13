# Changelog

## 0.2.3 (2026-11-13)

- **Carry-forward across compactions** ([#1](https://github.com/bishtashish708/compact-diff/issues/1)). Re-injected errors, edited files and decisions used to be checked only once. They're now recorded in a per-session ledger (`carried.json`) and included in the next compaction's snapshot, marked "carried from compaction N", so a later summary that drops them again is caught.
  - Items are deduped by kind plus a hash of the normalized text.
  - An item stops being carried when the summary keeps it, when an error's command later runs cleanly, when a newer decision covers the same subject, or after 3 carries.
  - The report lists what stopped being carried, and why.
- **Reversed rules.** A rule that you later explicitly reverse ("actually, use npm after all") is no longer tracked or re-injected. This is a heuristic; see the README for the cues it needs.
- 12 new tests for multi-compaction sessions and rule reversal, on synthetic transcripts.

## 0.2.2 (2026-11-12)

- `/compact-diff:report` now pre-approves only its exact report command, instead of any `python3` command.
- README and write-up use a real Claude Code session recording.
- Evaluation includes the human spot-check.

## 0.2.1 (2026-11-12)

- Hook commands meet the plugin directory's path rules (shell guard script). Listing fields, icon and PRIVACY.md.

## 0.2.0 (2026-11-12)

- Evaluation on 12 real compactions, rule-extraction filter, forked-session late snapshot, delivery on the next prompt or tool call.
