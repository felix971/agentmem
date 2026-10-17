"""DESIGN 12.7: public notes files plus real MCP optimistic updates."""

from tests.helpers import AgentmemCase, sha256_text, tool_strings


class ProjectNotesTests(AgentmemCase):
    def test_first_access_creates_specified_template_path_and_git_repo(self):
        directory = self.root / "项目 notes"
        directory.mkdir()
        path, content, shown = self.notes_fixture(cwd=directory)
        project_id = "path:" + str(directory.resolve())
        self.assertIn(f"# {directory.name} 项目笔记", content)
        self.assertIn(f"<!-- project_id: {project_id} -->", content)
        for section in ("## 决定", "## 进度", "## 已撤回 / 已替代"):
            self.assertIn(section, content)
        self.assertIn(content.strip(), shown)
        self.assertIn(str(path), self.cli("notes", "path", "--cwd", directory).stdout)
        self.assertEqual(self.git("rev-parse", "--show-toplevel", cwd=path.parent), str(path.parent))
        self.assertFalse(path.is_relative_to(directory))

    def test_get_notes_returns_content_and_actual_sha256(self):
        path, content, _ = self.notes_fixture()
        client = self.mcp()
        result = client.tool("get_project_notes", {"cwd": str(self.project)})
        self.assertFalse(result["isError"], result)
        exposed = tool_strings(result)
        self.assertIn(content.strip(), exposed)
        self.assertIn(sha256_text(content), exposed)
        self.assertEqual(path.read_text(encoding="utf-8"), content)

    def test_matching_sha_updates_file_and_creates_commit(self):
        path, original, _ = self.notes_fixture()
        updated = "# 项目笔记\n\n## 决定\n用户明确决定使用 SQLite。\n\n## 进度\nNotesUpdatedMarker\n"
        client = self.mcp()
        result = client.tool("update_project_notes", {
            "cwd": str(self.project), "content": updated,
            "base_sha256": sha256_text(original), "agent": "unittest",
        })
        self.assertFalse(result["isError"], result)
        self.assertEqual(path.read_text(encoding="utf-8"), updated)
        fetched = client.tool("get_project_notes", {"cwd": str(self.project)})
        self.assertFalse(fetched["isError"], fetched)
        self.assertIn(updated.strip(), tool_strings(fetched))
        self.assertIn(sha256_text(updated), tool_strings(fetched))
        self.assertEqual(self.git("log", "-1", "--format=%s", cwd=path.parent),
                         f"{self.project.name}: update via unittest")
        self.assertEqual(self.git("show", f"HEAD:{path.name}", cwd=path.parent), updated.strip())
        self.assertEqual(self.git("status", "--porcelain", cwd=path.parent), "")

    def test_wrong_sha_refuses_write_and_returns_current_content_and_hash(self):
        path, original, _ = self.notes_fixture()
        client = self.mcp()
        result = client.tool("update_project_notes", {
            "cwd": str(self.project), "content": "RejectedNotesMarker",
            "base_sha256": "0" * 64, "agent": "unittest",
        })
        self.assertTrue(result["isError"], result)
        self.assertEqual(path.read_text(encoding="utf-8"), original)
        error = tool_strings(result)
        self.assertIn(original.strip(), error)
        self.assertIn(sha256_text(original), error)

    def test_second_client_with_stale_sha_cannot_overwrite_first(self):
        path, original, _ = self.notes_fixture()
        first, second = self.mcp(), self.mcp()
        base = sha256_text(original)
        content = "# Notes\n\n## 决定\nFirstWriterMarker\n"
        accepted = first.tool("update_project_notes", {
            "cwd": str(self.project), "content": content, "base_sha256": base, "agent": "first",
        })
        self.assertFalse(accepted["isError"], accepted)
        head = self.git("rev-parse", "HEAD", cwd=path.parent)
        refused = second.tool("update_project_notes", {
            "cwd": str(self.project), "content": "StaleWriterMarker", "base_sha256": base, "agent": "second",
        })
        self.assertTrue(refused["isError"], refused)
        self.assertEqual(path.read_text(encoding="utf-8"), content)
        self.assertEqual(self.git("rev-parse", "HEAD", cwd=path.parent), head)
        self.assertIn(sha256_text(content), tool_strings(refused))

    def test_content_limit_is_8000_characters_not_utf8_bytes(self):
        path, original, _ = self.notes_fixture()
        client = self.mcp()
        content = "记" * 8000
        result = client.tool("update_project_notes", {
            "cwd": str(self.project), "content": content,
            "base_sha256": sha256_text(original), "agent": "unittest",
        })
        self.assertFalse(result["isError"], result)
        self.assertEqual(path.read_text(encoding="utf-8"), content)
        too_long = client.tool("update_project_notes", {
            "cwd": str(self.project), "content": content + "忆",
            "base_sha256": sha256_text(content), "agent": "unittest",
        })
        self.assertTrue(too_long["isError"], too_long)
        self.assertEqual(path.read_text(encoding="utf-8"), content)

    def test_notes_do_not_enter_history_search(self):
        self.seed_claude("ActualConversationMarker")
        self.index()
        path, original, _ = self.notes_fixture()
        client = self.mcp()
        updated = "# Notes\n\n## 决定\nNotesOnlyEvidenceMarker\n"
        result = client.tool("update_project_notes", {
            "cwd": str(self.project), "content": updated,
            "base_sha256": sha256_text(original), "agent": "unittest",
        })
        self.assertFalse(result["isError"], result)
        self.assertEqual(path.read_text(encoding="utf-8"), updated)
        self.index()
        self.assertEqual(self.search("NotesOnlyEvidenceMarker"), [])
        self.assertEqual(len(self.search("ActualConversationMarker")), 1)
