# compact-diff evaluation, v0.1 (November 2026)

How often does Claude Code compaction lose working state? How well does compact-diff detect it? This page reports **aggregate numbers only**. No transcript text, rule text, paths or usernames appear here or anywhere else in the repo.

## Setup

| | |
| :- | :- |
| Compactions | **12 real compactions** from one developer's machine |
| Historical | 2 auto-compactions from one long coding session, which happened during normal use |
| Created for this eval | 10 manual compactions. We forked 5 sessions that had never been compacted (67k–99k tokens of context each) with `claude --resume <id> --fork-session` and ran `/compact` twice per fork with Haiku 4.5. That cost $0.64 at API prices; the original sessions were not touched. |
| Items | compact-diff extracted 364 items. Annotators judged **329 of them real working state**; see Extraction precision below. |
| Labels | Blind labels against a written rubric ([`eval/RUBRIC.md`](eval/RUBRIC.md)). Each packet held the summary plus the item list, never the tool's verdict. **The annotators were Claude subagents, not humans**, with one annotator per packet and no inter-annotator agreement measured. A human pass is on the roadmap. |
| Goal vs. reality | We aimed for 30–50 compactions. The machine had only 2, so the other 10 were created as described above. |

Packets were built with [`eval/make_packets.py`](eval/make_packets.py) and scored with [`eval/score.py`](eval/score.py). Aggregate output is in [`eval/results.json`](eval/results.json).

## 1. How much does compaction drop?

These are gold labels on the 329 real items:

| Kind | Items | Kept | Partial | Dropped | Not fully kept |
| :- | -: | -: | -: | -: | -: |
| User instructions | 120 | 70 | 31 | 19 | **42%** |
| Unresolved errors | 17 | 5 | 3 | 9 | **71%** |
| Decisions | 11 | 5 | 5 | 1 | 55% |
| Files edited | 179 | 157 | 3 | 19 | 12% |
| Open tasks | 2 | 2 | 0 | 0 | 0% |
| **All** | **329** | **239** | **42** | **48** | **27%** |

- Per compaction, the share of state not fully kept ranged from 0% to 100%, with a median of 31%. The 100% case has only 2 items.
- Rules are usually lost in part, not outright: 26% of instructions were *partial*. A typical partial is a rule that survives without its condition or scope, such as "do X before Y" becoming "do X".
- Two summaries said no errors had occurred when real errors had.
- **Compaction is partly random.** We compacted 5 sessions twice each from identical context, giving 113 item pairs. 85% of the pairs got the same label. Of the 38 items lost in at least one run, **17 (45%) were lost in only one** of the two runs.

## 2. How good is compact-diff's detector?

These are compact-diff's verdicts with default thresholds (kept ≥ 0.70, partial ≥ 0.40) against the gold labels, on 329 items:

| | Precision | Recall |
| :- | -: | -: |
| **kept** | **97.5%** | 80.8% |
| partial | 30.8% | 38.1% |
| dropped | 46.8% | 77.1% |
| **"lost" (partial or dropped), binary** | **64.9%** | **94.4%** |

The 3-class accuracy is 74.8%, with macro-F1 0.60.

By kind, for the binary lost/kept call:

| Kind | Items | Accuracy (3-class) | Lost precision | Lost recall |
| :- | -: | -: | -: | -: |
| Files edited | 179 | 98% | 100% | 91% |
| User instructions | 120 | 50% | 55% | 96% |
| Unresolved errors | 17 | 29% | 73% | 92% |
| Decisions | 11 | 36% | 67% | 100% |

What this means in practice:

- **It rarely misses a loss.** It catches 94% of lost items, and when it says an item was kept, that is right 97.5% of the time.
- **It over-reports loss for reworded rules.** Matching is lexical, so a rule the summary carries in different words often scores as partial or dropped. Only 55% of its "lost instruction" flags are real. The cost is some duplicate context when items are re-injected, which is the cheap side of the trade-off.
- **Partial vs. dropped is close to a coin flip.** Treat those two labels as one "check this" bucket.

### Extraction precision

The annotators judged whether each extracted item was real working state:

| Kind | Real / extracted |
| :- | -: |
| Files edited | 179 / 179 (100%) |
| Unresolved errors | 17 / 17 (100%) |
| Open tasks | 2 / 2 |
| User instructions | 120 / 146 (82%) |
| Decisions | 11 / 20 (55%) |

The false "instructions" are mostly spec prose and background text inside long prompts. Most false "decisions" are observations or open questions.

## 3. Threshold tuning: no change

We searched the grid kept ∈ [0.40, 0.90] × partial ∈ [0.10, 0.70] and scored it with leave-one-compaction-out cross-validation:

- **Held-out accuracy:** tuned 71.1%, default 74.8%.
- **Best in-sample point (0.70, 0.20):** +0.012 macro-F1 and −0.6 points of accuracy, which is within noise.
- **Instructions only:** held-out accuracy 48.3% tuned vs 50.0% default.

**The defaults stay.** Moving thresholds doesn't help: the instruction errors come from paraphrase, which lexical scoring can't see. The next accuracy gain needs a semantic matcher (roadmap).

## 4. Steering (`COMPACT_DIFF_STEER`): not measured yet

The plan was to compact each fork once with the PreCompact steering note and once without. Both arms turned out to be unsteered. With `--fork-session`, the new session's transcript isn't on disk when PreCompact fires, so the snapshot was empty and no steering note was printed. The 10 runs are therefore 5 pairs of **unsteered** compactions, which is where the run-to-run randomness figure above comes from.

- **Plugin fix:** the audit now re-extracts the snapshot after the fact for forked sessions ([`817e8f1`](https://github.com/bishtashish708/compact-diff/commit/817e8f1)). Steering still can't work in that case.
- **What we have:** one live anecdote, where a steered summary kept the session's rules. That's n=1, not evidence.
- **Next experiment:** 5 sessions × steered vs. unsteered, with the steering text passed as `/compact <instructions>` so it doesn't depend on the hook. That's 5 more Haiku compactions.

## 5. Hook behaviour we observed (Claude Code 2.1.252)

1. **`SessionStart:compact` fires before `PostCompact`,** about 36 ms earlier in our live run, and before the summary is written to the transcript. A plugin can't rely on SessionStart to act on the summary. compact-diff delivers on the next `UserPromptSubmit` or `PostToolUse` instead.
2. **With `--fork-session`, PreCompact sees an empty transcript.** The fork's history is written only after compaction.
3. **`PostCompact` gets the summary but can't add context.** Its `systemMessage` is discarded, so it can only log.
4. **`PreCompact` stdout really does steer the summary.** It becomes part of the summariser's instructions, capped at 10,000 characters.

## Limitations

- One machine, one developer, 12 compactions. 10 were manual and run on Haiku, and the 2 historical ones are from a single session.
- LLM annotators, one per packet, with no human adjudication. The rubric needed four clarifications during labelling (listed in the rubric).
- Item text comes from compact-diff's own extractor, so state the extractor never saw (git branch, env, URLs) isn't measured at all.
- The file-stem matching fix (`test_cli` counting as a mention of `test_cli.py`) was found while reading the first labelled packets, then evaluated on all 12. That is a mild leak, and it affects files only.
