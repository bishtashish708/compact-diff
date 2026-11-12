<!--
DRAFT: NOT APPROVED, NOT PUBLISHED. Numbers come from launch/EVALUATION.md (aggregates only).

Placeholders to fill (find them all with: grep -n "TODO(" launch/draft-how-much-does-compaction-drop.md):
  TODO(byline)     your name or handle, and the publish date
  TODO(gif)        the real screen recording (launch/demo/real-demo.gif or .mp4)
On approval: remove this comment and the "Draft." line under the title.
-->

# How much does Claude Code compaction drop?

*Draft. 12 real compactions, 329 labelled pieces of working state, a steering experiment, and one surprise about hook ordering.*

*By **TODO(byline): name or handle** · TODO(byline): date*

> **TODO(gif):** embed the real recording here, e.g. `![compact-diff in action](https://github.com/bishtashish708/compact-diff/raw/main/launch/demo/real-demo.gif)`. It should show three rules, `/compact`, the verdict line and `/compact-diff:report`. Until then, the replay GIF is at `launch/demo/demo.gif`.

When a Claude Code session fills its context window, `/compact` (or auto-compaction) replaces the conversation with a summary. The summary is usually good. When it isn't, nothing tells you. Claude just stops following the rule you set an hour ago, or forgets the test that was still failing.

I built [compact-diff](https://github.com/bishtashish708/compact-diff), a Claude Code plugin that takes a snapshot of working state before each compaction, diffs it against the summary afterwards, and re-injects only what went missing. Building it raised the obvious question: **how much does compaction actually drop?**

## The short answer

Over 12 real compactions, about **27% of tracked working state didn't fully survive** the summary. It depends heavily on the kind of state:

| What | Not fully kept |
| :- | -: |
| Errors that were still unresolved | **71%** |
| Decisions ("going with X instead of Y") | 55% |
| **Rules the user gave** ("never…", "don't…", "use X, not Y") | **42%** |
| Files that were edited | 12% |

Two more findings stood out.

- **Rules are lost in part, not outright.** Only 16% of rules disappeared entirely. Another 26% survived without their condition or scope. "Do X before Y" becomes "do X", and "never touch Z" becomes "Z exists".
- **Compaction is partly random.** I compacted five sessions twice each from identical context. 45% of the items lost in either run were lost in only one of them. The same session can come out of `/compact` with or without your rule, depending on the run.

And twice, the summary said no errors had occurred while a real error was still open.

## How this was measured

- **Data.** 12 compactions from my own machine. Two happened during normal use: auto-compaction of a long coding session, about 511k → 30k tokens. I had too few historical compactions, so I made 10 more. I forked five sessions that had never been compacted (67k–99k tokens each) and ran `/compact` twice on each fork with Haiku 4.5, for $0.64 at API prices.
- **Items.** compact-diff extracts five kinds of state with deterministic heuristics: user instructions, open tasks, unresolved errors, edited files and decisions. That gave 364 items, and 329 were judged real.
- **Labels.** Each item was labelled kept, partial or dropped against the summary, following [a written rubric](https://github.com/bishtashish708/compact-diff/blob/main/launch/eval/RUBRIC.md). The labellers never saw the tool's verdicts. **The labellers were Claude subagents, not people.** As a check, I reviewed a random sample by hand: **all 40 sampled labels matched my own call**, both kept vs. lost and the exact label. That check wasn't blind (I saw the label first), so read it as "no errors found", not as a proper agreement rate.
- **Caveats.** One developer and one machine. Mostly Haiku summaries. Small *n*. Full method and limits are in [EVALUATION.md](https://github.com/bishtashish708/compact-diff/blob/main/launch/EVALUATION.md).

## Can a tool notice the loss?

compact-diff matches items against the summary by shared words (no model calls), so it runs in milliseconds inside a hook. Against the labels:

- It **catches 94% of the items that were lost**.
- When it says an item was **kept**, it's right **97.5%** of the time.
- It is too pessimistic about reworded rules: only 55% of its "lost rule" flags are real. The rule was there in different words.

For a safety net that's the right way round. A false alarm re-injects a few hundred tokens the summary already had. A miss means Claude forgets your rule. Tuning the thresholds didn't help when tested on held-out compactions, so the next accuracy gain needs semantic matching.

<details>
<summary>What `cdiff.py scan` reports over the same history (tool estimates)</summary>

`scan` runs the tool's own matcher over every compaction in `~/.claude/projects`, which is 14 here, including two tiny test compactions. Its "lost %" counts a partial as half lost:

| Kind | Tracked | Kept | Partial | Dropped | Lost % |
| :- | -: | -: | -: | -: | -: |
| User instructions | 148 | 39 | 43 | 66 | 59% |
| Unresolved errors | 17 | 2 | 8 | 7 | 65% |
| Files edited | 180 | 160 | 0 | 20 | 11% |
| Decisions | 20 | 2 | 7 | 11 | 72% |

Compared with the labelled numbers, the tool overstates instruction loss, as expected from its 55% precision there. Use `scan` to find compactions worth reading, not as a measurement.

</details>

## Can you ask the summary to keep things?

Claude Code lets a `PreCompact` hook add instructions to the summariser. compact-diff uses this to list the rules, open tasks and errors it found, and asks for them to be carried forward. I tested it on the same five sessions: two unsteered compactions each, against one compaction with compact-diff's note.

| | Unsteered | Steered |
| :- | -: | -: |
| **User rules not fully kept** | 47% | **18%** |
| Unresolved errors not fully kept | 80% | 20% (n=5, take it lightly) |
| Edited files not fully kept | 13% | **38%** |
| Everything | 26% | 30% |

Of the items lost in *both* unsteered runs, the steered run recovered 43%, almost all of them rules. But it isn't free. In one session, the steered summary came out half the usual length, kept the rules and dropped 16 files from its file list. **Steering moves what survives toward what you name, not how much survives.** For rules that's a good trade. Files are the cheapest thing to restore, and compact-diff re-injects them anyway.

Small sample: 5 sessions, Haiku summaries, and LLM labels. Treat this as a direction, not an effect size.

## The surprise: compaction hooks don't fire in the order you'd think

Claude Code has three hook points around compaction:

- **`PreCompact`**: its stdout is appended to the summariser's instructions, so you can ask the summary to keep things.
- **`PostCompact`**: receives the finished `compact_summary`, but can't add anything to Claude's context.
- **`SessionStart`** with matcher `compact`: can add context.

The natural design diffs in PostCompact, then re-injects the gaps from SessionStart. **That doesn't work.** In Claude Code 2.1.252, `SessionStart:compact` fires *before* `PostCompact`, about 36 ms earlier in my run. The summary isn't in the transcript yet either. So at SessionStart you have nothing to diff against.

compact-diff handles this by delivering on whichever comes next, the next `UserPromptSubmit` or the next `PostToolUse` (auto-compaction often happens mid-turn). A shell guard keeps those hooks from starting Python unless a report is waiting.

One more trap: with `claude --resume <id> --fork-session`, **PreCompact sees an empty transcript**, because the fork's history isn't written to disk until after compaction. Any snapshot-before-compact tool silently records nothing there. compact-diff now recovers the snapshot afterwards, but it can't steer the summary in that case.

## Try it

```
/plugin marketplace add bishtashish708/compact-diff
/plugin install compact-diff@compact-diff
```

After each compaction you get one line, `compact-diff: summary kept 9/14 tracked items; 4 dropped, 1 partial…`. `/compact-diff:report` shows the full diff. To see what past compactions dropped in your own history, without installing anything, run this from a clone of the repo:

```
git clone https://github.com/bishtashish708/compact-diff && cd compact-diff
python3 scripts/cdiff.py scan ~/.claude/projects
```

It's local only: no network calls, no model calls, stdlib Python.

## What's next

- A full human labelling pass beyond the spot-check, with an inter-annotator agreement check.
- A larger steering experiment (more sessions, the bigger models people actually use), and a slimmer steering note that leaves files to re-injection.
- Semantic matching for reworded rules.
- If you run `scan` on your own history, I'd love the aggregate table, numbers only.

Related Claude Code issues: [#89831](https://github.com/anthropics/claude-code/issues/89831) (visible record of what compaction dropped), [#29890](https://github.com/anthropics/claude-code/issues/29890) (compaction loses critical working knowledge), [#60973](https://github.com/anthropics/claude-code/issues/60973) (signal compaction and recommend a state refresh).
