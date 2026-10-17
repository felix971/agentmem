"""DESIGN 12.1: Claude records are observed only through CLI results."""

from tests.helpers import AgentmemCase


class ClaudeParsingTests(AgentmemCase):
    def test_string_user_content(self):
        self.seed_claude("ClaudeStringMarker 我们决定用 SQLite 存原文")
        self.assertEqual(self.index()["messages_added"], 1)
        rows = self.search("ClaudeStringMarker")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["source"], "claude")
        self.assertEqual(rows[0]["role"], "user")
        self.assertIn("我们决定用 SQLite 存原文", self.session_text(rows[0]["ref"]))

    def test_user_text_blocks_joined_and_nontext_ignored(self):
        self.seed_claude([
            {"type": "text", "text": "FirstBlockMarker"},
            {"type": "tool_result", "tool_use_id": "t1", "content": "IgnoredBlockMarker"},
            {"type": "image", "source": {"type": "base64", "data": "IgnoredImageMarker"}},
            {"type": "text", "text": "SecondBlockMarker"},
        ])
        self.assertEqual(self.index()["messages_added"], 1)
        row = self.search("FirstBlockMarker")[0]
        self.assertIn("FirstBlockMarker\n\nSecondBlockMarker", self.session_text(row["ref"]))
        self.assertEqual(self.search("IgnoredBlockMarker"), [])
        self.assertEqual(self.search("IgnoredImageMarker"), [])

    def test_system_reminder_and_other_paired_tags_are_removed(self):
        tags = ("system-reminder", "agentmem-context", "local-command-caveat", "command-message")
        chunks = [f"<{tag}>\nHiddenTagMarker\nline two\n</{tag}>" for tag in tags]
        # Two blocks of the same kind also check non-greedy deletion.
        text = "\n\n".join(chunks) + "\n\n\n\nCleanTextMarker\n\n\n\nKeptTailMarker"
        text += "\n<system-reminder>HiddenTagMarker</system-reminder>"
        self.seed_claude(text)
        self.assertEqual(self.index()["messages_added"], 1)
        row = self.search("CleanTextMarker")[0]
        body = self.session_text(row["ref"])
        self.assertIn("CleanTextMarker\n\nKeptTailMarker", body)
        self.assertNotIn("HiddenTagMarker", body)
        self.assertEqual(self.search("HiddenTagMarker"), [])

    def test_messages_empty_after_cleaning_are_not_indexed(self):
        self.write_records(self.claude_path(), [
            self.claude("<system-reminder>EmptyCleanMarker</system-reminder>", uuid="empty-1"),
            self.claude(" \n\t ", uuid="empty-2"),
            self.claude("VisibleCleanMarker", uuid="visible"),
        ])
        self.assertEqual(self.index()["messages_added"], 1)
        self.assertEqual(self.search("EmptyCleanMarker"), [])
        self.assertEqual(len(self.search("VisibleCleanMarker")), 1)

    def test_meta_tool_only_sidechain_and_other_record_types_are_skipped(self):
        records = [
            self.claude("VisibleRecordMarker", uuid="keep"),
            self.claude("IgnoredRecordMarker meta", uuid="meta", isMeta=True),
            self.claude("IgnoredRecordMarker side", uuid="side", isSidechain=True),
            self.claude([{"type": "tool_result", "content": "IgnoredRecordMarker tool",
                          "tool_use_id": "t1"}], uuid="tool"),
            self.claude([{"type": "thinking", "thinking": "IgnoredRecordMarker thought"}],
                        uuid="thought", role="assistant"),
            self.claude([{"type": "tool_use", "id": "t1", "name": "exec",
                          "input": {"command": "IgnoredRecordMarker tooluse"}}],
                        uuid="tooluse", role="assistant"),
        ]
        for kind in ("attachment", "system", "queue-operation", "file-history-snapshot"):
            records.append(self.claude("IgnoredRecordMarker " + kind, uuid=kind, type=kind))
        self.write_records(self.claude_path(), records)
        self.assertEqual(self.index()["messages_added"], 1)
        self.assertEqual(self.search("IgnoredRecordMarker"), [])
        self.assertEqual(len(self.search("VisibleRecordMarker")), 1)

    def test_subagents_directory_is_excluded(self):
        self.seed_claude("MainDirectoryMarker")
        self.write_records(self.claude_path("subagents/agent-child.jsonl"), [
            self.claude("SubagentDirectoryMarker", uuid="sub-u", session_id="sub-directory-id"),
        ])
        self.assertEqual(self.index()["messages_added"], 1)
        self.assertEqual(self.search("SubagentDirectoryMarker"), [])
        self.assertEqual(len(self.search("MainDirectoryMarker")), 1)

    def test_assistant_takes_only_text_blocks(self):
        self.seed_claude([
            {"type": "thinking", "thinking": "PrivateThinkingMarker"},
            {"type": "text", "text": "AssistantTextMarker"},
            {"type": "tool_use", "id": "exec-1", "name": "shell",
             "input": {"command": "PrivateToolMarker"}},
            {"type": "text", "text": "AssistantTailMarker"},
        ], role="assistant", uuid="a1")
        self.assertEqual(self.index()["messages_added"], 1)
        row = self.search("AssistantTextMarker")[0]
        self.assertEqual(row["role"], "assistant")
        self.assertIn("AssistantTextMarker\n\nAssistantTailMarker", self.session_text(row["ref"]))
        self.assertEqual(self.search("PrivateThinkingMarker"), [])
        self.assertEqual(self.search("PrivateToolMarker"), [])

    def test_custom_title_wins_and_latest_same_kind_wins(self):
        sid = "claude-title-session"
        path = self.write_records(self.claude_path(), [
            self.claude("TitleEvidenceMarker", session_id=sid),
            {"type": "ai-title", "sessionId": sid, "aiTitle": "Earlier AI title"},
            {"type": "custom-title", "sessionId": sid, "customTitle": "Earlier custom title"},
            {"type": "custom-title", "sessionId": sid, "customTitle": "Winning custom title"},
            {"type": "ai-title", "sessionId": sid, "aiTitle": "Later AI title"},
        ])
        self.index()
        text = self.session_text("claude:" + sid)
        self.assertIn("Winning custom title", text)
        for rejected in ("Earlier AI title", "Earlier custom title", "Later AI title"):
            self.assertNotIn(rejected, text)
        self.write_records(path, [{"type": "ai-title", "sessionId": sid,
                                   "aiTitle": "Incremental AI title"}], append=True)
        self.assertEqual(self.index()["messages_added"], 0)
        text = self.session_text("claude:" + sid)
        self.assertIn("Winning custom title", text)
        self.assertNotIn("Incremental AI title", text)

    def test_latest_ai_title_used_without_custom_title(self):
        sid = "claude-ai-title-session"
        self.write_records(self.claude_path(), [
            self.claude("AITitleEvidenceMarker", session_id=sid),
            {"type": "ai-title", "sessionId": sid, "aiTitle": "Old generated title"},
            {"type": "ai-title", "sessionId": sid, "aiTitle": "Latest generated title"},
        ])
        self.index()
        text = self.session_text(sid)
        self.assertIn("Latest generated title", text)
        self.assertNotIn("Old generated title", text)

    def test_missing_uuid_uses_stable_raw_line_identity(self):
        record = self.claude("MissingUuidMarker", uuid=None)
        self.write_records(self.claude_path(), [record, record])
        self.assertEqual(self.index()["messages_added"], 1)
        self.assertEqual(self.index()["messages_added"], 0)
        self.assertEqual(len(self.search("MissingUuidMarker")), 1)
