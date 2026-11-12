# Labelling rubric (blind)

Each packet has a compaction `summary` and a list of `items` extracted from the conversation
BEFORE compaction. For every item decide whether the summary still carries it.

label:
- "kept": a reader of only the summary would know this item, with its key specifics
  (a rule with its polarity and scope; a file named by name/path; an error with its kind or
  cause; an open task as still pending; a decision with what was chosen).
  Paraphrase is fine. Polarity matters: "never X" summarised as "did X" is NOT kept.
- "partial": the summary touches the topic but loses a key specific (condition, scope,
  which file, which error, that a task is still open, what was chosen over what).
- "dropped": the summary does not convey it at all.

is_real_item (extraction quality, judged from the item text alone + context in the summary):
- instruction: true only if it is a rule/constraint/preference the user set for the work
  (not a description, question, or narrative sentence).
- todo / file / error / decision: true if it is a genuine open task / edited file /
  error actually encountered / choice actually made. Errors that are clearly expected
  output (e.g. a deliberately failing test fixture) are still real errors; mark true.

Write JSON: {"id": <packet id>, "labels": [{"id": <item id>, "label": "...", "is_real_item": true|false, "note": "<=12 words"}]}
One entry per item, every item. Judge from the summary text only; don't guess about what
the tool decided.

## Clarifications added after the first packets

- A rule restated only as past history still counts as kept if its content and polarity are conveyed.
- A file described but not named is partial.
- A decision whose chosen outcome is clear but whose alternative is missing is kept.
- Items that aren't really decisions or instructions get `is_real_item: false` but still get a label.
