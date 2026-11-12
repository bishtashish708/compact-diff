# compact-diff evaluation, v0.2 (November 2026)

How often does Claude Code compaction lose working state? How well does compact-diff detect it? This page reports **aggregate numbers only**. No transcript text, rule text, paths or usernames appear here or anywhere else in the repo.

## Setup

| | |
| :- | :- |
| Compactions | **12 real compactions** from one developer's machine |
| Historical | 2 auto-compactions from one long coding session, which happened during normal use |
| Created for this eval | 10 manual compactions. We forked 5 sessions that had never been compacted (67k–99k tokens of context each) with `claude --resume <id> --fork-session` and ran `/compact` twice per fork with Haiku 4.5, for $0.64 at API prices. 5 more steered runs, for $0.45, are used only in section 4. The original sessions were not touched. |
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

**v0.2 rule filter** ([`f66ae4a`](https://github.com/bishtashish708/compact-diff/commit/f66ae4a)) rejects three kinds of sentence unless a strong rule word (never, don't, must, always…) is present: quoted text (UI copy, prompt templates), short headings whose rule word appears only inside parentheses, and past-tense narrative. Re-measured on the same labelled set:

| | Before | After |
| :- | -: | -: |
| Instruction extraction precision | 120 / 146 (82.2%) | 118 / 132 (**89.4%**) |
| All-kind extraction precision | 329 / 364 (90.4%) | 327 / 350 (93.4%) |
| Real items lost by the filter | | 2 (one borderline spec row, counted in two packets) |
| Detector: lost recall / lost precision | 94.4% / 64.9% | 95.5% / 64.6% |

The filters were designed by looking at these same labelled items, so the gain is **in-sample**. Expect less on new data. The detector numbers barely move, as expected, because the filter changes which items are extracted, not how they're matched.

## 3. Threshold tuning: no change

We searched the grid kept ∈ [0.40, 0.90] × partial ∈ [0.10, 0.70] and scored it with leave-one-compaction-out cross-validation:

- **Held-out accuracy:** tuned 71.1%, default 74.8%.
- **Best in-sample point (0.70, 0.20):** +0.012 macro-F1 and −0.6 points of accuracy, which is within noise.
- **Instructions only:** held-out accuracy 48.3% tuned vs 50.0% default.

**The defaults stay.** Moving thresholds doesn't help: the instruction errors come from paraphrase, which lexical scoring can't see. The next accuracy gain needs a semantic matcher (roadmap).

## 4. Does steering the summary help? Yes for rules, no overall

PreCompact steering means asking the summariser to keep the extracted items. We tested it on the same 5 sessions:

- **Unsteered:** the 10 runs above, two per session. They ended up unsteered because, with `--fork-session`, the fork's transcript isn't on disk when PreCompact fires, so the hook had nothing to steer with. That's fixed for auditing in [`817e8f1`](https://github.com/bishtashish708/compact-diff/commit/817e8f1).
- **Steered:** 5 new runs, one per session. Each passed compact-diff's exact steering note as `/compact <note>`, the same channel PreCompact stdout feeds. This cost $0.45 at API prices on Haiku 4.5.
- Item lists were identical across arms. Labels were blind, with the same rubric, from a different annotator subagent than the unsteered arm.

Share of real items **not fully kept**:

| Kind | Unsteered (2 runs × 5) | Steered (1 run × 5) |
| :- | -: | -: |
| **User instructions** | 47% (n=68) | **18%** (n=34) |
| Unresolved errors | 80% (n=10) | 20% (n=5) |
| Files edited | 13% (n=144) | **38%** (n=72) |
| **All kinds** | 26% (n=226) | 30% (n=113) |

Item by item:

- **Recovered:** of the 21 items lost in *both* unsteered runs, the steered run kept **9**: 7 instructions and 2 errors.
- **Regressed:** of the 75 items kept in both unsteered runs, the steered run lost 17. **16 of those were files from one session**, whose steered summary came out about half the length of its unsteered summaries (12k vs 20–23k characters). It kept the rules and dropped the file list.
- **By session:** steering lowered the not-fully-kept share in 3 sessions, tied in 1, and raised it in 1 (that same session).

Reading: **steering shifts what survives toward the things it names first** (rules, then tasks and errors). In one of five sessions it cost the file list. Rules are the costly thing to lose, while files are cheap to restore and compact-diff re-injects them anyway, so steering stays **on by default**. Two changes follow:

- **Already true.** The steering note lists rules first, then tasks and errors, then files.
- **Roadmap.** Drop the file list from the steering note when it's long, and rely on re-injection for files.

Caveats: 5 sessions and one steered run each, on Haiku, with a different annotator per arm. Steering went through `/compact <note>` rather than the hook. This is a direction, not a measurement of effect size.

## 5. Hook behaviour we observed (Claude Code 2.1.252)

1. **`SessionStart:compact` fires before `PostCompact`,** about 36 ms earlier in our live run, and before the summary is written to the transcript. A plugin can't rely on SessionStart to act on the summary. compact-diff delivers on the next `UserPromptSubmit` or `PostToolUse` instead.
2. **With `--fork-session`, PreCompact sees an empty transcript.** The fork's history is written only after compaction.
3. **`PostCompact` gets the summary but can't add context.** Its `systemMessage` is discarded, so it can only log.
4. **`PreCompact` stdout really does steer the summary.** It becomes part of the summariser's instructions, capped at 10,000 characters.

## Limitations

- One machine, one developer, 12 compactions. 10 were manual and run on Haiku, and the 2 historical ones are from a single session.
- LLM annotators, one per packet, with no human adjudication. The rubric needed four clarifications during labelling (listed in the rubric).
- Item text comes from compact-diff's own extractor, so state the extractor never saw (git branch, env, URLs) isn't measured at all.
- The v0.2 rule filter was designed on the labelled items (in-sample).
- The file-stem matching fix (`test_cli` counting as a mention of `test_cli.py`) was found while reading the first labelled packets, then evaluated on all 12. That is a mild leak, and it affects files only.
