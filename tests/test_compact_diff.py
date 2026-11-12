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
from compact_diff import extract, hooks, match, render, store  # noqa: E402
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
            self.assertIn('"${CLAUDE_PLUGIN_ROOT}/scripts/cdiff.py"', cmd)
            self.assertTrue(cmd.endswith("Deliver" if ev in ("UserPromptSubmit", "PostToolUse") else ev))

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
            r = subprocess.run(["/bin/sh", "-c", cmd.replace("python3", sys.executable)],
                               input=json.dumps({"session_id": "s9", "hook_event_name": "PostToolUse"}),
                               capture_output=True, text=True, env=env)
            self.assertEqual(r.returncode, 0, r.stderr)


if __name__ == "__main__":
    unittest.main()
