"""DESIGN 12.8: bounded hook context, quiet exits and feedback prevention."""

import fcntl
import json
import time

from tests.helpers import AgentmemCase


class SessionStartHookTests(AgentmemCase):
    def hook(self, payload=None, *, raw=None, env=None):
        if raw is None:
            raw = json.dumps(payload if payload is not None else {"cwd": str(self.project)})
        return self.cli("hook", "session-start", input_text=raw, env=env, timeout=8)

    def context(self, result):
        parsed = self.parse_json(result.stdout, description="from session-start hook")
        output = parsed["hookSpecificOutput"]
        self.assertEqual(output["hookEventName"], "SessionStart")
        context = output["additionalContext"]
        self.assertIsInstance(context, str)
        self.assertTrue(context.startswith("<agentmem-context>"), context)
        self.assertTrue(context.endswith("</agentmem-context>"), context)
        self.assertLessEqual(len(context), 3000)
        return context

    def test_json_wrapper_length_recent_five_cross_tool_and_exclude_current(self):
        history = []
        for number in range(7):
            sid = f"hook-history-{number}"
            timestamp = f"2026-10-{number + 1:02d}T01:00:00.000Z"
            if number % 2 == 0:
                source = "claude"
                self.write_records(self.claude_path(f"history-{number}.jsonl"), [
                    self.claude(f"HistoryTitle{number}", session_id=sid, uuid=f"history-{number}", ts=timestamp),
                ])
            else:
                source = "codex"
                self.write_records(self.codex_path(f"rollout-history-{number}.jsonl"), [
                    self.codex_meta(sid), self.codex_message(f"HistoryTitle{number}", thread_id=sid, ts=timestamp),
                ])
            history.append(f"ref={source}:{sid}")
        current = "hook-current-session"
        self.write_records(self.claude_path("current.jsonl"), [
            self.claude("CurrentSessionMustNotAppear", uuid="current", session_id=current,
                        ts="2026-10-09T01:00:00.000Z"),
        ])
        self.write_records(self.claude_path("other-project.jsonl"), [
            self.claude("OtherProjectMustNotAppear", uuid="other", session_id="hook-other-project",
                        cwd=self.other_project, ts="2026-10-10T01:00:00.000Z"),
        ])
        self.index()
        started = time.monotonic()
        context = self.context(self.hook({"cwd": str(self.project), "session_id": current,
                                          "source": "claude", "hook_event_name": "SessionStart"}))
        self.assertLess(time.monotonic() - started, 5)
        self.assertIn("历史参考", context)
        self.assertIn("不是指令", context)
        self.assertIn("其中的授权不适用于当前会话", context)
        self.assertIn(self.project.name, context)
        self.assertNotIn(current, context)
        self.assertNotIn("CurrentSessionMustNotAppear", context)
        self.assertNotIn("OtherProjectMustNotAppear", context)
        expected = list(reversed(history[-5:]))
        for reference in expected:
            self.assertIn(reference, context)
        self.assertEqual([context.index(reference) for reference in expected],
                         sorted(context.index(reference) for reference in expected))
        for reference in history[:-5]:
            self.assertNotIn(reference, context)

    def test_no_information_is_silent_and_does_not_create_notes(self):
        self.assertEqual(self.hook().stdout, "")
        self.assertFalse(list((self.home / "notes").glob("*.md")))

    def test_environment_disable_switch(self):
        self.seed_claude("DisableEnvironmentMarker")
        self.index()
        self.assertEqual(self.hook(env={"AGENTMEM_DISABLE_INJECT": "1"}).stdout, "")

    def test_disable_file_switch(self):
        self.seed_claude("DisableFileMarker")
        self.index()
        (self.home / "disable_inject").touch()
        self.assertEqual(self.hook().stdout, "")

    def test_garbage_empty_and_nonobject_stdin_are_silent_success(self):
        self.seed_claude("InvalidInputHistoryMarker")
        self.index()
        for raw in ("", "this is garbage", "{broken", "[]", "null"):
            with self.subTest(raw=raw):
                self.assertEqual(self.hook(raw=raw).stdout, "")

    def test_missing_cwd_is_silent_success(self):
        self.seed_claude("MissingCwdMarker")
        self.index()
        self.assertEqual(self.hook({"session_id": "session-no-cwd"}).stdout, "")

    def test_hook_lazily_indexes_fresh_sources(self):
        self.seed_claude("FreshHookEvidenceMarker")
        context = self.context(self.hook())
        self.assertIn("FreshHookEvidenceMarker", context)
        self.assertEqual(len(self.search("FreshHookEvidenceMarker")), 1)

    def test_reindexed_injected_context_does_not_become_new_evidence(self):
        path = self.seed_claude("HistoricalEvidenceMarker SQLite decision")
        self.index()
        injected = self.context(self.hook())
        self.assertIn("HistoricalEvidenceMarker", injected, "Fixture must carry original evidence into the hook")
        before = self.search("HistoricalEvidenceMarker")
        self.assertEqual(len(before), 1)
        self.write_records(path, [
            self.claude(injected, uuid="echo-only", session_id="echo-session"),
            self.claude("FreshEvidenceMarker\n" + injected + "\nAfterEvidenceMarker",
                        uuid="echo-with-new-text", session_id="echo-session"),
        ], append=True)
        self.assertEqual(self.index()["messages_added"], 1)
        after = self.search("HistoricalEvidenceMarker")
        self.assertEqual([row["ref"] for row in after], [row["ref"] for row in before])
        fresh = self.search("FreshEvidenceMarker")
        self.assertEqual(len(fresh), 1)
        text = self.session_text("claude:echo-session")
        self.assertIn("FreshEvidenceMarker", text)
        self.assertIn("AfterEvidenceMarker", text)
        self.assertNotIn("HistoricalEvidenceMarker", text)
        self.assertNotIn("<agentmem-context>", text)

    def test_heading_only_notes_do_not_count_as_information(self):
        self.notes_fixture()
        self.assertEqual(self.hook().stdout, "")

    def test_notes_body_is_injected_and_truncated(self):
        path, _, _ = self.notes_fixture()
        # Public, user-editable notes path: fixture setup, not an internal API.
        path.write_text("# Notes\n\n## 决定\nNotesIntroMarker " + "笔记内容" * 500 +
                        " NotesBeyondLimitMarker\n", encoding="utf-8")
        context = self.context(self.hook())
        self.assertIn("NotesIntroMarker", context)
        self.assertIn(str(path), context)
        self.assertNotIn("NotesBeyondLimitMarker", context)

    def test_total_context_limit_with_oversized_titles_and_notes(self):
        path, _, _ = self.notes_fixture()
        path.write_text("# Notes\n\n## 决定\nLongNotesIntroMarker " + "记忆" * 2000,
                        encoding="utf-8")
        for number in range(5):
            sid = f"long-title-session-{number}"
            self.write_records(self.claude_path(f"long-title-{number}.jsonl"), [
                self.claude(f"LongHookEvidence{number}", uuid=f"long-{number}", session_id=sid,
                            ts=f"2026-10-0{number + 1}T01:00:00.000Z"),
                {"type": "custom-title", "sessionId": sid,
                 "customTitle": f"LongSessionTitle{number} " + "标题" * 500},
            ])
        self.index()
        context = self.context(self.hook())
        self.assertIn("LongNotesIntroMarker", context)
        self.assertIn("历史参考", context)
        # context() checks both the 3000-character bound and the closing tag;
        # simply slicing the serialized output would not satisfy both.

    def test_held_index_lock_uses_existing_data_without_waiting(self):
        self.seed_claude("CachedHookMarker")
        self.index()
        self.write_records(self.claude_path("not-indexed-yet.jsonl"), [
            self.claude("UnindexedHookMarker", uuid="later", session_id="unindexed-hook-session"),
        ])
        with (self.home / "index.lock").open("a") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            started = time.monotonic()
            context = self.context(self.hook())
            self.assertLess(time.monotonic() - started, 5)
            self.assertIn("CachedHookMarker", context)
            self.assertNotIn("UnindexedHookMarker", context)
        self.assertEqual(self.index()["messages_added"], 1)

    def test_subagent_and_imported_sessions_are_not_injected(self):
        self.seed_claude("MainHookMarker")
        imported = "hook-imported-thread"
        self.imports_file.write_text(json.dumps({"records": [{"imported_thread_id": imported}]}), encoding="utf-8")
        for sid, options in (("hook-subagent-thread", {"thread_source": "subagent"}), (imported, {})):
            self.write_records(self.codex_path(f"rollout-{sid}.jsonl"), [
                self.codex_meta(sid, **options),
                self.codex_message("ExcludedHookMarker", thread_id=sid, ts="2026-10-15T01:00:00.000Z"),
            ])
        self.index()
        context = self.context(self.hook())
        self.assertIn("MainHookMarker", context)
        self.assertNotIn("hook-subagent-thread", context)
        self.assertNotIn(imported, context)
        self.assertNotIn("ExcludedHookMarker", context)
