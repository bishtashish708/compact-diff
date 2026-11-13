import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tests" / "fixtures"))

import build  # noqa: E402
from compact_diff import carry, extract, hooks, match, render, store  # noqa: E402
from compact_diff import transcript as tx  # noqa: E402
from compact_diff.cli import audit_transcript  # noqa: E402

CLI = [sys.executable, str(ROOT / "scripts" / "cdiff.py")]


def write_jsonl(path, entries):
    with open(path, "w", encoding="utf-8") as fh:
        for e in entries:
            fh.write(json.dumps(e) + "\n")


class TempStateMixin:
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.state = os.path.join(self.tmp.name, "state")
        self._old = os.environ.get("COMPACT_DIFF_HOME")
        os.environ["COMPACT_DIFF_HOME"] = self.state

    def tearDown(self):
        if self._old is None:
            os.environ.pop("COMPACT_DIFF_HOME", None)
        else:
            os.environ["COMPACT_DIFF_HOME"] = self._old
        self.tmp.cleanup()


class TranscriptTests(unittest.TestCase):
    def test_segments_split_at_boundary_and_capture_summary(self):
        segs = tx.segments(build.entries())
        self.assertEqual(len(segs), 2)
        self.assertIn("Pending Tasks", segs[0].summary)
        self.assertEqual(segs[0].boundary["compactMetadata"]["trigger"], "manual")
        self.assertEqual([tx.message_text(e) for e in segs[1].entries], ["ok continue"])

    def test_human_prompt_filter(self):
        entries = build.entries()
        prompts = [e["uuid"] for e in entries if tx.is_human_prompt(e)]
        self.assertEqual(prompts, ["u1", "u2", "u3"])  # not meta, tool results or the summary

    def test_load_skips_bad_lines(self):
        with tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False) as fh:
            fh.write('{"type":"user"}\nnot json\n[1,2]\n\n{"type":"assistant"}\n')
        try:
            self.assertEqual(len(tx.load(fh.name)), 2)
        finally:
            os.unlink(fh.name)
        self.assertEqual(tx.load("/nonexistent/x.jsonl"), [])


class ExtractTests(unittest.TestCase):
    def setUp(self):
        self.window = build.pre_compact_entries()
        self.items = extract.extract_state(self.window, self.window, build.CWD)

    def texts(self, kind):
        return [i.text for i in self.items if i.kind == kind]

    def test_instructions(self):
        got = self.texts("instruction")
        self.assertIn("Never push to main directly; open a PR instead.", got)
        self.assertIn("Use pnpm, not npm.", got)
        self.assertTrue(any("legacy/" in t for t in got))
        self.assertTrue(any("/api/v1/login" in t for t in got))
        self.assertFalse(any("model" in t for t in got))  # meta command ignored

    def test_rule_filter_rejects_spec_prose(self):
        rules = [
            "Never push to main directly; open a PR instead.",
            "Use pnpm, not npm.",
            "Quick look at the logs, please (don't restart the worker).",
            "Search page (v2: design only, do not ship yet)",
            "Keep the legacy importer behind a flag for now.",
            "Benchmarked both drivers, but always pin the faster one.",  # past tense, strong rule word
        ]
        prose = [
            '"Reply with valid YAML only, no prose"',
            "“Your parcel could not be delivered today”",
            "Pricing notes (only for reference)",
            "Glossary (important terms)",
            "Tried the cache first, but it was not faster and only helped cold starts.",
            "Measured the queue: it does not drain under load.",
            "The weather was nice today.",
        ]
        for s in rules:
            self.assertTrue(extract.looks_like_rule(s), s)
        for s in prose:
            self.assertFalse(extract.looks_like_rule(s), s)

    def test_files_relative_with_counts(self):
        files = {i.text: i for i in self.items if i.kind == "file"}
        self.assertEqual(set(files), {"src/auth.ts", "src/login.tsx", "src/middleware/session.ts", "tests/auth.test.ts"})
        self.assertEqual(files["src/auth.ts"].detail, "2 edit(s)")
        self.assertEqual(files["src/auth.ts"].anchors, ["auth.ts"])

    def test_errors_resolved_by_clean_rerun_are_dropped(self):
        errors = self.texts("error")
        self.assertEqual(len(errors), 1, errors)
        self.assertIn("TypeError", errors[0])

    def test_open_tasks_only(self):
        self.assertEqual(
            self.texts("todo"),
            ["Update the README auth section", "Add refresh-token rotation to the API gateway"],
        )

    def test_todowrite(self):
        entries = [
            build._a_tool("a", "t", "TodoWrite", {"todos": [
                {"content": "write docs", "status": "completed"},
                {"content": "ship release", "status": "in_progress"},
            ]})
        ]
        self.assertEqual([i.text for i in extract.extract_todos(entries)], ["ship release"])

    def test_decisions(self):
        self.assertTrue(any("PKCE" in t for t in self.texts("decision")))

    def test_prose_mentioning_error_is_not_an_error(self):
        entries = [
            build._a_tool("a", "t", "Bash", {"command": "python3 gen.py"}),
            build._result("r", "t", "200 cases, 16 expect ValueError"),
        ]
        self.assertEqual(extract.extract_errors(entries), [])

    def test_sentence_split_keeps_abbreviations(self):
        self.assertEqual(extract.sentences("Stop and ask (e.g. run gh auth login). Next."), ["Stop and ask (e.g. run gh auth login).", "Next."])

    def test_item_roundtrip(self):
        it = self.items[0]
        self.assertEqual(extract.Item.from_dict(it.to_dict()).id, it.id)


class MatchTests(unittest.TestCase):
    def test_audit_fixture(self):
        window = build.pre_compact_entries()
        items = extract.extract_state(window, window, build.CWD)
        verdicts = {v.item.text: v.status for v in match.audit(items, build.SUMMARY)}
        self.assertEqual(verdicts["Use pnpm, not npm."], match.KEPT)
        self.assertEqual(verdicts["Never push to main directly; open a PR instead."], match.DROPPED)
        self.assertEqual(verdicts["src/auth.ts"], match.KEPT)
        self.assertEqual(verdicts["src/middleware/session.ts"], match.DROPPED)
        self.assertEqual(verdicts["Update the README auth section"], match.KEPT)
        self.assertEqual(verdicts["Add refresh-token rotation to the API gateway"], match.DROPPED)

    def test_file_stem_without_extension(self):
        item = extract.Item("file", "tests/test_pricing.py", "high", anchors=["test_pricing.py"])
        self.assertEqual(match.judge(item, match.Summary("Tests: test_cli, test_pricing, test_budget.")).status, match.KEPT)
        plain = extract.Item("file", "src/session.ts", "high", anchors=["session.ts"])
        self.assertEqual(match.judge(plain, match.Summary("This session is being continued.")).status, match.DROPPED)
        generic = extract.Item("file", "src/utils.py", "high", anchors=["utils.py"])
        self.assertEqual(match.judge(generic, match.Summary("We cleaned up utils.")).status, match.DROPPED)

    def test_negation_guard(self):
        item = extract.Item("instruction", "Never deploy the staging database migrations on Friday.", "critical")
        v = match.judge(item, match.Summary("We deploy the staging database migrations on Friday."))
        self.assertEqual(v.status, match.PARTIAL)
        v = match.judge(item, match.Summary("Rule: never deploy the staging database migrations on Friday."))
        self.assertEqual(v.status, match.KEPT)

    def test_paraphrase_is_partial_or_kept_not_dropped(self):
        item = extract.Item("todo", "Add refresh-token rotation to the API gateway", "critical")
        v = match.judge(item, match.Summary("Still to do: refresh token rotation in the gateway."))
        self.assertIn(v.status, (match.KEPT, match.PARTIAL))

    def test_empty_summary_drops_everything(self):
        item = extract.Item("instruction", "Always run the linter first.", "critical")
        self.assertEqual(match.judge(item, match.Summary("")).status, match.DROPPED)


class RenderTests(unittest.TestCase):
    def setUp(self):
        window = build.pre_compact_entries()
        self.items = extract.extract_state(window, window, build.CWD)
        self.verdicts = match.audit(self.items, build.SUMMARY)

    def test_steering_lists_rules_but_not_decisions(self):
        text = render.steering(self.items)
        self.assertIn("Never push to main directly", text)
        self.assertIn("src/middleware/session.ts", text)
        self.assertNotIn("Decisions:", text)
        self.assertLessEqual(len(text), render.STEER_LIMIT)

    def test_reinjection_only_lost_items(self):
        text, n = render.reinjection(self.verdicts)
        self.assertIn("Never push to main directly", text)
        self.assertIn("refresh-token rotation", text)
        self.assertNotIn("Use pnpm, not npm.", text)  # kept by the summary
        self.assertNotIn("<system-reminder>", text)
        self.assertGreater(n, 0)

    def test_report_markdown(self):
        md = render.report_markdown(self.verdicts, {"trigger": "manual"})
        self.assertIn("## Dropped", md)
        self.assertIn("| User instructions |", md)

    def test_limits(self):
        many = [extract.Item("instruction", f"Never do thing number {i} " + "x" * 200, "critical") for i in range(200)]
        self.assertLessEqual(len(render.steering(many)), render.STEER_LIMIT + 40)
        text, _ = render.reinjection(match.audit(many, ""))
        self.assertLessEqual(len(text), render.INJECT_LIMIT + 40)


class HookLifecycleTests(TempStateMixin, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.path = os.path.join(self.tmp.name, "t.jsonl")
        write_jsonl(self.path, build.pre_compact_entries())
        self.base = {"session_id": "s1", "transcript_path": self.path, "cwd": build.CWD}

    def pre(self):
        return hooks.run("PreCompact", json.dumps({**self.base, "hook_event_name": "PreCompact", "trigger": "manual", "custom_instructions": None}))

    def test_full_lifecycle_with_postcompact(self):
        out, code = self.pre()
        self.assertEqual(code, 0)
        self.assertIn("Never push to main", out)
        self.assertTrue((Path(self.state) / "sessions" / "s1" / "pending.json").exists())

        out, code = hooks.run("PostCompact", json.dumps({**self.base, "trigger": "manual", "compact_summary": build.SUMMARY}))
        self.assertEqual((out, code), ("", 0))

        out, code = hooks.run("SessionStart", json.dumps({**self.base, "source": "compact"}))
        data = json.loads(out)
        self.assertIn("compact-diff: summary kept", data["systemMessage"])
        ctx = data["hookSpecificOutput"]["additionalContext"]
        self.assertEqual(data["hookSpecificOutput"]["hookEventName"], "SessionStart")
        self.assertIn("Never push to main directly", ctx)
        self.assertNotIn("Use pnpm, not npm.", ctx)

        # Second SessionStart for the same compaction injects nothing.
        self.assertEqual(hooks.run("SessionStart", json.dumps({**self.base, "source": "compact"})), ("", 0))

    def test_fallback_reads_summary_from_transcript(self):
        self.pre()
        write_jsonl(self.path, build.entries())  # transcript now contains boundary + summary
        out, _ = hooks.run("SessionStart", json.dumps({**self.base, "source": "compact"}))
        data = json.loads(out)
        self.assertIn("Never push to main directly", data["hookSpecificOutput"]["additionalContext"])
        report = store.read_json(store.latest_report())
        self.assertEqual(report["meta"]["summary_source"], "transcript")

    def test_observed_order_sessionstart_before_postcompact(self):
        # Claude Code 2.1.252: SessionStart:compact fires before PostCompact, and before
        # the summary is in the transcript. Delivery must fall through to the next hook.
        self.pre()
        flags = Path(self.state) / "flags"
        self.assertTrue(any(flags.iterdir()))
        self.assertEqual(hooks.run("SessionStart", json.dumps({**self.base, "source": "compact"})), ("", 0))
        self.assertEqual(hooks.run("Deliver", json.dumps({**self.base, "hook_event_name": "PostToolUse"})), ("", 0))
        hooks.run("PostCompact", json.dumps({**self.base, "compact_summary": build.SUMMARY}))
        out, _ = hooks.run("Deliver", json.dumps({**self.base, "hook_event_name": "UserPromptSubmit", "prompt": "go on"}))
        data = json.loads(out)
        self.assertEqual(data["hookSpecificOutput"]["hookEventName"], "UserPromptSubmit")
        self.assertIn("Never push to main directly", data["hookSpecificOutput"]["additionalContext"])
        self.assertFalse(any(flags.iterdir()))  # flag cleared: the shell guard goes quiet again
        self.assertEqual(hooks.run("Deliver", json.dumps({**self.base, "hook_event_name": "PostToolUse"})), ("", 0))
        report = store.read_json(store.latest_report())
        self.assertEqual(report["meta"]["delivered_via"], "UserPromptSubmit")

    def test_forked_session_empty_transcript_at_precompact(self):
        # Observed with `claude --resume X --fork-session`: the fork's transcript is not on
        # disk when PreCompact fires. The audit must recover once the file is written.
        fork = os.path.join(self.tmp.name, "fork.jsonl")
        base = {**self.base, "session_id": "fork1", "transcript_path": fork}
        out, _ = hooks.run("PreCompact", json.dumps({**base, "trigger": "manual"}))
        self.assertEqual(out, "")  # nothing to steer with
        hooks.run("PostCompact", json.dumps({**base, "compact_summary": build.SUMMARY}))
        self.assertIsNone(store.latest_report(session_id="fork1"))
        self.assertEqual(hooks.run("Deliver", json.dumps({**base, "hook_event_name": "PostToolUse"})), ("", 0))
        build.write(fork)  # Claude Code writes the fork's history after compaction
        out, _ = hooks.run("Deliver", json.dumps({**base, "hook_event_name": "UserPromptSubmit"}))
        data = json.loads(out)
        self.assertIn("Never push to main directly", data["hookSpecificOutput"]["additionalContext"])
        report = store.read_json(store.latest_report(session_id="fork1"))
        self.assertTrue(report["meta"]["snapshot"].startswith("late"))
        self.assertEqual(report["meta"]["summary_source"], "PostCompact.compact_summary")

    def test_old_summary_is_not_reused(self):
        # A transcript that already had one compaction: its summary must not be taken
        # as the summary for the next one.
        write_jsonl(self.path, build.entries())
        self.pre()
        self.assertEqual(hooks.run("Deliver", json.dumps({**self.base, "hook_event_name": "PostToolUse"})), ("", 0))
        self.assertTrue((Path(self.state) / "sessions" / "s1" / "pending.json").exists())

    def test_gives_up_when_compaction_never_finishes(self):
        self.pre()
        pending = Path(self.state) / "sessions" / "s1" / "pending.json"
        data = json.loads(pending.read_text())
        data["created"] -= hooks.GIVE_UP_AFTER_S + 1
        pending.write_text(json.dumps(data))
        hooks.run("Deliver", json.dumps({**self.base, "hook_event_name": "PostToolUse"}))
        self.assertFalse(any((Path(self.state) / "flags").iterdir()))

    def test_steer_and_inject_can_be_disabled(self):
        os.environ["COMPACT_DIFF_STEER"] = "0"
        os.environ["COMPACT_DIFF_INJECT"] = "0"
        try:
            out, _ = self.pre()
            self.assertEqual(out, "")
            hooks.run("PostCompact", json.dumps({**self.base, "compact_summary": build.SUMMARY}))
            data = json.loads(hooks.run("SessionStart", json.dumps({**self.base, "source": "compact"}))[0])
            self.assertNotIn("hookSpecificOutput", data)
            self.assertIn("systemMessage", data)
        finally:
            os.environ.pop("COMPACT_DIFF_STEER")
            os.environ.pop("COMPACT_DIFF_INJECT")

    def test_other_sources_ignored(self):
        self.pre()
        for src in ("startup", "resume", "clear"):
            self.assertEqual(hooks.run("SessionStart", json.dumps({**self.base, "source": src})), ("", 0))

    def test_garbage_never_raises(self):
        for raw in ("", "not json", "[]", '{"transcript_path": 5}', json.dumps({"session_id": "../../etc"})):
            for ev in ("PreCompact", "PostCompact", "SessionStart", "Bogus"):
                out, code = hooks.run(ev, raw)
                self.assertEqual(code, 0)
        # Path traversal in session_id stays inside the state dir.
        for p in Path(self.state).rglob("*"):
            self.assertTrue(str(p.resolve()).startswith(str(Path(self.state).resolve())))


SUMMARY_RULES_ONLY = (
    "This session is being continued from a previous conversation that ran out of context.\n\n"
    "Summary: the user is refactoring auth to OAuth2. Never push to main directly; open a PR instead. "
    "Use pnpm, not npm. Don't touch anything in the legacy/ folder. Keep the old /api/v1/login endpoint "
    "working until the mobile app ships 4.2; it must return a deprecation header. Pending: update the README "
    "auth section; add refresh-token rotation to the API gateway."
)
TEST_CMD = "pnpm test tests/auth.test.ts"


def _boundary(uid):
    return {"type": "system", "subtype": "compact_boundary", "uuid": uid, "content": "Conversation compacted",
            "compactMetadata": {"trigger": "manual", "preTokens": 1000, "postTokens": 100}}


def _bash(uid, cmd, output, is_error=False):
    return [build._a_tool(f"a-{uid}", f"t-{uid}", "Bash", {"command": cmd}), build._result(f"r-{uid}", f"t-{uid}", output, is_error)]


class MultiCompactionTests(TempStateMixin, unittest.TestCase):
    """Errors, files and decisions re-injected after one compaction stay tracked in the next."""

    def setUp(self):
        super().setUp()
        self.path = os.path.join(self.tmp.name, "t.jsonl")
        self.base = {"session_id": "s1", "transcript_path": self.path, "cwd": build.CWD}
        self.entries = build.pre_compact_entries()
        self.n = 0

    def compact(self, summary=SUMMARY_RULES_ONLY, window=()):
        """Append ``window`` (after the previous summary), run one full compaction, return the delivered report."""
        self.entries = self.entries + list(window)
        write_jsonl(self.path, self.entries)
        hooks.run("PreCompact", json.dumps({**self.base, "hook_event_name": "PreCompact", "trigger": "manual"}))
        hooks.run("PostCompact", json.dumps({**self.base, "compact_summary": summary}))
        out, _ = hooks.run("Deliver", json.dumps({**self.base, "hook_event_name": "UserPromptSubmit"}))
        self.n += 1
        self.entries = self.entries + [_boundary(f"b{self.n}"), build._u(f"s{self.n}", summary, isCompactSummary=True)]
        report = store.read_json(store.latest_report(session_id="s1"))
        return report, (json.loads(out) if out else {})

    def carried(self, report, kind):
        return [v for v in report["verdicts"] if v["item"]["kind"] == kind and v["item"].get("carried_from")]

    def ledger(self):
        return store.read_json(store.session_dir("s1") / "carried.json")["items"]

    def test_dropped_error_is_checked_again_and_reinjected_again(self):
        r1, _ = self.compact()
        self.assertTrue(any(v["item"]["kind"] == "error" and v["status"] == "dropped" for v in r1["verdicts"]))
        r2, out2 = self.compact(window=[build._u("u9", "ok continue with the README")])
        errs = self.carried(r2, "error")
        self.assertEqual(len(errs), 1)
        self.assertEqual(errs[0]["item"]["carried_from"], 1)
        self.assertEqual(errs[0]["status"], "dropped")
        self.assertIn("TypeError", out2["hookSpecificOutput"]["additionalContext"])
        self.assertIn("[carried from compaction 1]", out2["hookSpecificOutput"]["additionalContext"])
        md = Path(r2["markdown_path"]).read_text()
        self.assertIn("carried from compaction 1", md)
        self.assertIn("Carried forward from earlier compactions", md)

    def test_files_and_decisions_are_carried_too(self):
        self.compact()
        r2, _ = self.compact(window=[build._u("u9", "ok")])
        self.assertTrue(self.carried(r2, "file"))
        self.assertTrue(self.carried(r2, "decision"))

    def test_error_resolved_by_clean_rerun_is_retired(self):
        self.compact()
        r2, _ = self.compact(window=_bash("x1", TEST_CMD, "PASS tests/auth.test.ts\n4 passed"))
        self.assertFalse(self.carried(r2, "error"))
        self.assertTrue(any("resolved" in r["reason"] for r in r2["meta"]["retired"]))
        self.assertIn("No longer carried forward", Path(r2["markdown_path"]).read_text())

    def test_same_error_again_is_not_duplicated(self):
        self.compact()
        fail = " FAIL  tests/auth.test.ts\n    TypeError: Cannot read properties of undefined (reading 'refreshToken')\nExit code 1"
        r2, _ = self.compact(window=_bash("x2", TEST_CMD, fail, is_error=True))
        errs = [v for v in r2["verdicts"] if v["item"]["kind"] == "error"]
        self.assertEqual(len(errs), 1, [e["item"]["text"] for e in errs])
        self.assertFalse(errs[0]["item"].get("carried_from"))  # the fresh copy wins

    def test_summary_keeping_it_removes_it_from_the_ledger(self):
        self.compact()
        keeps = SUMMARY_RULES_ONLY + " Open error: TypeError: Cannot read properties of undefined (reading 'refreshToken') in tests/auth.test.ts."
        r2, _ = self.compact(summary=keeps, window=[build._u("u9", "ok")])
        self.assertTrue(any(v["status"] == "kept" for v in self.carried(r2, "error")))
        self.assertFalse([k for k in self.ledger() if k.startswith("error:")])

    def test_decision_superseded_by_newer_decision_is_retired(self):
        self.compact()
        newer = build._a_text("a99", "Switching to the implicit grant flow instead of PKCE, because the client is now confidential.")
        r2, _ = self.compact(window=[newer])
        old = [v for v in self.carried(r2, "decision") if "PKCE flow instead of implicit grant" in v["item"]["text"]]
        self.assertFalse(old)
        self.assertTrue(any("superseded" in r["reason"] for r in r2["meta"]["retired"]))

    def test_carry_is_capped(self):
        self.compact()
        for i in range(carry.MAX_CARRIES):
            r, _ = self.compact(window=[build._u(f"u{i}x", "ok")])
            self.assertTrue(self.carried(r, "error"), f"compaction {i + 2}")
        r, _ = self.compact(window=[build._u("ulast", "ok")])
        self.assertFalse(self.carried(r, "error"))
        self.assertTrue(any("carried" in x["reason"] for x in r["meta"]["retired"]))

    def test_no_carry_when_reinjection_is_off(self):
        os.environ["COMPACT_DIFF_INJECT"] = "0"
        try:
            self.compact()
            r2, _ = self.compact(window=[build._u("u9", "ok")])
        finally:
            os.environ.pop("COMPACT_DIFF_INJECT", None)
        self.assertFalse(self.carried(r2, "error"))

    def test_audit_cli_unaffected(self):
        self.compact()
        self.compact(window=[build._u("u9", "ok")])
        self.assertEqual(len(audit_transcript(self.path)), 1)  # second boundary's summary isn't in the file yet


class SupersededRuleTests(unittest.TestCase):
    def rules(self, *prompts):
        entries = [build._u(f"u{i}", p) for i, p in enumerate(prompts)]
        return [i.text for i in extract.extract_instructions(entries)]

    def test_explicit_reversal_drops_the_older_rule(self):
        got = self.rules("Use pnpm, not npm. Never push to main directly.", "Actually, use npm after all.")
        self.assertNotIn("Use pnpm, not npm.", got)
        self.assertIn("Never push to main directly.", got)

    def test_unrelated_reversal_cue_keeps_the_rule(self):
        got = self.rules("Don't touch anything in the legacy/ folder, it is frozen.", "We no longer need the old importer script.")
        self.assertIn("Don't touch anything in the legacy/ folder, it is frozen.", got)

    def test_actually_without_shared_subject_keeps_the_rule(self):
        got = self.rules("Never push to main directly; open a PR instead.", "Actually, also run the tests before you push.")
        self.assertIn("Never push to main directly; open a PR instead.", got)


class CliTests(TempStateMixin, unittest.TestCase):
    def run_cli(self, *args, stdin=""):
        env = {**os.environ, "COMPACT_DIFF_HOME": self.state}
        return subprocess.run(CLI + list(args), input=stdin, capture_output=True, text=True, env=env, timeout=60)

    def test_audit_and_scan(self):
        path = os.path.join(self.tmp.name, "proj", "t.jsonl")
        os.makedirs(os.path.dirname(path))
        build.write(path)
        r = self.run_cli("audit", path)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("Never push to main directly", r.stdout)
        r = self.run_cli("audit", path, "--json")
        self.assertEqual(json.loads(r.stdout)[0]["trigger"], "manual")
        r = self.run_cli("scan", self.tmp.name)
        self.assertIn("1 compaction(s)", r.stdout)
        self.assertEqual(len(audit_transcript(path)), 1)

    def test_hook_via_subprocess_and_report(self):
        path = os.path.join(self.tmp.name, "t.jsonl")
        write_jsonl(path, build.pre_compact_entries())
        base = {"session_id": "abc", "transcript_path": path, "cwd": build.CWD}
        r = self.run_cli("hook", "PreCompact", stdin=json.dumps({**base, "trigger": "auto"}))
        self.assertEqual(r.returncode, 0)
        self.assertIn("Unresolved errors:", r.stdout)
        self.run_cli("hook", "PostCompact", stdin=json.dumps({**base, "compact_summary": build.SUMMARY}))
        r = self.run_cli("report", "--cwd", build.CWD, "--show-injected")
        self.assertIn("# compact-diff report", r.stdout)
        self.assertIn("## Re-injected context", r.stdout)

    def test_report_when_empty(self):
        r = self.run_cli("report")
        self.assertIn("No compact-diff reports yet", r.stdout)


class PluginLayoutTests(unittest.TestCase):
    def test_manifest_and_hooks(self):
        manifest = json.loads((ROOT / ".claude-plugin" / "plugin.json").read_text())
        self.assertEqual(manifest["name"], "compact-diff")
        hooks_cfg = json.loads((ROOT / "hooks" / "hooks.json").read_text())["hooks"]
        self.assertEqual(set(hooks_cfg), {"PreCompact", "PostCompact", "SessionStart", "UserPromptSubmit", "PostToolUse"})
        self.assertEqual(hooks_cfg["SessionStart"][0]["matcher"], "compact")
        for ev, groups in hooks_cfg.items():
            cmd = groups[0]["hooks"][0]["command"]
            if ev in ("UserPromptSubmit", "PostToolUse"):
                self.assertEqual(cmd, 'sh "${CLAUDE_PLUGIN_ROOT}/scripts/deliver-guard.sh"')
            else:
                self.assertEqual(cmd, f'python3 "${{CLAUDE_PLUGIN_ROOT}}/scripts/cdiff.py" hook {ev}')

    def test_hook_commands_meet_directory_rules(self):
        # Directory validation blocks hook commands with variables other than
        # ${CLAUDE_PLUGIN_ROOT}, command substitution, wildcards or inline programs.
        import re
        hooks_cfg = json.loads((ROOT / "hooks" / "hooks.json").read_text())["hooks"]
        for groups in hooks_cfg.values():
            for g in groups:
                for h in g["hooks"]:
                    cmd = h["command"].replace("${CLAUDE_PLUGIN_ROOT}", "")
                    self.assertNotRegex(cmd, r"\$|`|\*|\?| -c ", h["command"])
        self.assertNotIn("$", (ROOT / "commands" / "report.md").read_text().split("!`", 1)[1].split("`", 1)[0].replace("${CLAUDE_PLUGIN_ROOT}", ""))

    @unittest.skipIf(sys.platform == "win32", "POSIX shell guard")
    def test_shell_guard(self):
        cmd = json.loads((ROOT / "hooks" / "hooks.json").read_text())["hooks"]["PostToolUse"][0]["hooks"][0]["command"]
        with tempfile.TemporaryDirectory() as state:
            env = {**os.environ, "COMPACT_DIFF_HOME": state, "CLAUDE_PLUGIN_ROOT": str(ROOT)}
            env.pop("CLAUDE_PLUGIN_DATA", None)
            # No flags: exits 0 without starting Python. A fake python3 records any call.
            fake = Path(state, "fakebin")
            fake.mkdir()
            marker = Path(state, "python-was-called")
            (fake / "python3").write_text(f"#!/bin/sh\ntouch '{marker}'\n")
            (fake / "python3").chmod(0o755)
            r = subprocess.run(["/bin/sh", "-c", cmd], input="{}", capture_output=True, text=True,
                               env={**env, "PATH": f"{fake}:/bin:/usr/bin"})
            self.assertEqual((r.returncode, r.stdout), (0, ""))
            self.assertFalse(marker.exists())
            os.makedirs(os.path.join(state, "flags"))
            Path(state, "flags", "s9").write_text("1")
            real = Path(state, "realbin"); real.mkdir()
            (real / "python3").symlink_to(sys.executable)
            env["PATH"] = f"{real}:/bin:/usr/bin"
            r = subprocess.run(["/bin/sh", "-c", cmd],
                               input=json.dumps({"session_id": "s9", "hook_event_name": "PostToolUse"}),
                               capture_output=True, text=True, env=env)
            self.assertEqual(r.returncode, 0, r.stderr)


if __name__ == "__main__":
    unittest.main()
