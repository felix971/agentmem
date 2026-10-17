"""DESIGN 12.2: item_completed, thread identity, exclusions and titles."""

import os

from tests.helpers import AgentmemCase, STAMP


class CodexParsingTests(AgentmemCase):
    def test_user_and_agent_message_text_variants(self):
        self.write_records(self.codex_path(), [
            self.codex_meta(),
            self.codex_message([
                {"type": "text", "text": "CodexUserMarker"},
                {"type": "image", "text": "CodexIgnoredBlockMarker"},
                {"type": "Text", "text": "CodexSecondBlockMarker"},
            ]),
            self.codex_message("CodexAssistantMarker", role="assistant", item_id="item-2"),
        ])
        self.assertEqual(self.index()["messages_added"], 2)
        user = self.search("CodexUserMarker")[0]
        assistant = self.search("CodexAssistantMarker")[0]
        self.assertEqual((user["source"], user["role"]), ("codex", "user"))
        self.assertEqual(assistant["role"], "assistant")
        self.assertIn("CodexUserMarker\n\nCodexSecondBlockMarker", self.session_text(user["ref"]))
        self.assertEqual(self.search("CodexIgnoredBlockMarker"), [])

    def test_response_items_legacy_events_and_commands_are_ignored(self):
        records = [self.codex_meta(), self.codex_message("CodexVisibleMarker")]
        for role in ("user", "developer"):
            records.append({"timestamp": STAMP, "type": "response_item", "payload": {
                "type": "message", "role": role,
                "content": [{"type": "input_text", "text": "CodexInjectedMarker AGENTS.md"}],
            }})
        for kind in ("user_message", "agent_message"):
            records.append({"timestamp": STAMP, "type": "event_msg", "payload": {
                "type": kind, "message": "CodexInjectedMarker legacy",
            }})
        records.append({"timestamp": STAMP, "type": "event_msg", "payload": {
            "type": "item_completed", "thread_id": "codex-thread-0001", "turn_id": "turn-1",
            "item": {"type": "CommandExecution", "id": "exec-1",
                     "content": [{"type": "text", "text": "CodexInjectedMarker command"}]},
        }})
        self.write_records(self.codex_path(), records)
        self.assertEqual(self.index()["messages_added"], 1)
        self.assertEqual(self.search("CodexInjectedMarker"), [])
        self.assertEqual(len(self.search("CodexVisibleMarker")), 1)

    def test_first_session_meta_wins(self):
        first, second = "first-thread-00001", "ignored-thread-00002"
        self.write_records(self.codex_path(), [
            self.codex_meta(first),
            self.codex_meta(second, cwd=self.other_project),
            self.codex_message("FirstIdentityMarker", thread_id=None, turn_id=None),
        ])
        self.index()
        row = self.search("FirstIdentityMarker")[0]
        self.assertEqual(row["session_id"], first)
        self.assertEqual(row["project_id"], "path:" + str(self.project.resolve()))
        self.assertIn(str(self.project), self.session_text("codex:" + first))
        error = self.cli("session", "codex:" + second, expected=1)
        self.assertTrue(error.stderr.strip())

    def test_both_subagent_markers_skip_messages_but_keep_sessions(self):
        cases = (
            ("subagent-threadsource", {"thread_source": "subagent"}),
            ("subagent-sourceobject", {"source": {"subagent": {"parent_thread_id": "parent"}}}),
        )
        for sid, options in cases:
            self.write_records(self.codex_path(f"rollout-{sid}.jsonl"), [
                self.codex_meta(sid, **options),
                self.codex_message("SkippedSubagentMarker", thread_id=sid),
            ])
        self.assertEqual(self.index()["messages_added"], 0)
        self.assertEqual(self.search("SkippedSubagentMarker"), [])
        for sid, _ in cases:
            with self.subTest(session=sid):
                # A successful session lookup with its original path proves a
                # sessions entry exists, without depending on SQLite tables.
                text = self.session_text("codex:" + sid)
                self.assertIn(str(self.codex_path(f"rollout-{sid}.jsonl")), text)
                self.assertIn(str(self.project), text)
                self.assertNotIn("SkippedSubagentMarker", text)
                self.assertEqual(self.session_rows("codex:" + sid), [])

    def test_imported_thread_skips_messages_but_keeps_session(self):
        sid = "imported-thread-0001"
        self.imports_file.write_text('{"records":[{"imported_thread_id":"' + sid + '"}]}', encoding="utf-8")
        path = self.write_records(self.codex_path(), [
            self.codex_meta(sid), self.codex_message("ImportedSecretMarker", thread_id=sid),
        ])
        self.assertEqual(self.index()["messages_added"], 0)
        self.assertEqual(self.search("ImportedSecretMarker"), [])
        text = self.session_text("codex:" + sid)
        self.assertIn(str(path), text)
        self.assertNotIn("ImportedSecretMarker", text)
        self.assertEqual(self.session_rows("codex:" + sid), [])

    def _assert_imports_fallback(self):
        self.write_records(self.codex_path(), [self.codex_meta(), self.codex_message("ImportsFallbackMarker")])
        self.assertEqual(self.index()["messages_added"], 1)
        self.assertEqual(len(self.search("ImportsFallbackMarker")), 1)

    def test_missing_imports_file_is_empty_set(self):
        self.imports_file.unlink()
        self._assert_imports_fallback()

    def test_corrupt_imports_file_is_empty_set(self):
        self.imports_file.write_text("{this is not JSON", encoding="utf-8")
        self._assert_imports_fallback()

    def test_fork_parent_thread_copies_do_not_duplicate(self):
        parent, fork = "fork-parent-00001", "fork-child-000002"
        copied = self.codex_message("ForkSharedMarker", thread_id=parent, item_id="shared-item")
        parent_path = self.write_records(self.codex_path("rollout-parent.jsonl"), [
            self.codex_meta(parent), copied,
        ])
        fork_path = self.write_records(self.codex_path("rollout-fork.jsonl"), [
            self.codex_meta(fork), copied,
            self.codex_message("ForkNewMarker", thread_id=fork, item_id="new-item"),
        ])
        os.utime(parent_path, ns=(1_000_000_000, 1_000_000_000))
        os.utime(fork_path, ns=(2_000_000_000, 2_000_000_000))
        self.assertEqual(self.index()["messages_added"], 2)
        shared = self.search("ForkSharedMarker")
        self.assertEqual(len(shared), 1)
        self.assertEqual(shared[0]["session_id"], parent)
        self.assertEqual(self.search("ForkNewMarker")[0]["session_id"], fork)
        self.assertEqual(self.index()["messages_added"], 0)

    def test_session_index_last_title_and_refresh_without_rollout_change(self):
        sid = "codex-title-thread"
        self.write_records(self.codex_path(), [self.codex_meta(sid), self.codex_message("CodexTitleMarker", thread_id=sid)])
        self.write_records(self.session_index, [
            {"id": sid, "thread_name": "Old Codex title", "updated_at": STAMP},
            {"id": sid, "thread_name": "Latest Codex title", "updated_at": STAMP},
        ])
        self.index()
        text = self.session_text("codex:" + sid)
        self.assertIn("Latest Codex title", text)
        self.assertNotIn("Old Codex title", text)
        self.write_records(self.session_index, [{"id": sid, "thread_name": "Refreshed Codex title", "updated_at": STAMP}], append=True)
        self.assertEqual(self.index()["messages_added"], 0)
        text = self.session_text("codex:" + sid)
        self.assertIn("Refreshed Codex title", text)
        self.assertNotIn("Latest Codex title", text)

    def test_turn_context_and_thread_state_survive_incremental_reads(self):
        sid = "incremental-codex-thread"
        path = self.write_records(self.codex_path(), [
            self.codex_meta(sid),
            {"timestamp": STAMP, "type": "turn_context", "payload": {"cwd": str(self.other_project)}},
            self.codex_message("FirstContextMarker", thread_id=None),
        ])
        self.assertEqual(self.index()["messages_added"], 1)
        self.write_records(path, [self.codex_message("SecondContextMarker", thread_id=None, item_id="item-2")], append=True)
        self.assertEqual(self.index()["messages_added"], 1)
        for query in ("FirstContextMarker", "SecondContextMarker"):
            row = self.search(query)[0]
            self.assertEqual(row["session_id"], sid)
            # Section 7 fixes ownership to session_meta.cwd, including across
            # incremental reads; turn_context.cwd does not move the session.
            self.assertEqual(row["project_id"], "path:" + str(self.project.resolve()))
        text = self.session_text("codex:" + sid)
        self.assertIn(str(self.project), text)
        self.assertNotIn(str(self.other_project), text)

    def test_cleaning_applies_to_codex_too(self):
        self.write_records(self.codex_path(), [
            self.codex_meta(),
            self.codex_message("<agentmem-context>CodexFeedbackMarker</agentmem-context>\n\n\nCodexCleanMarker"),
        ])
        self.assertEqual(self.index()["messages_added"], 1)
        self.assertEqual(self.search("CodexFeedbackMarker"), [])
        self.assertEqual(len(self.search("CodexCleanMarker")), 1)
