"""DESIGN 7: session ownership, visible projects and first-registered names.

Only public CLI/MCP interfaces are used. Git repositories, worktrees, source
records, imports and agentmem's data/notes all live in AgentmemCase's sandbox.
"""

import hashlib
import json
from pathlib import Path

from tests.helpers import AgentmemCase, STAMP, tool_text


class ProjectRuleTests(AgentmemCase):
    def _git_project(self, name):
        path = self.root / name
        path.mkdir()
        self.git("init", "--quiet", cwd=path)
        # Identical empty commits made within one second can have identical
        # hashes in unrelated repositories. Distinct root messages prevent a
        # false pass caused by A and B accidentally sharing a project ID.
        self.git("-c", "user.name=t", "-c", "user.email=t@t", "commit",
                 "--quiet", "--allow-empty", "-m", f"fixture root for {name}", cwd=path)
        roots = self.git("rev-list", "--max-parents=0", "HEAD", cwd=path).splitlines()
        self.assertTrue(roots)
        return path, "git:" + sorted(roots)[0][:12]

    def _git_pair(self):
        first, first_id = self._git_project("repoA")
        later, later_id = self._git_project("repoB")
        self.assertNotEqual(first_id, later_id, "The two fixture repositories must be unrelated")
        return first, first_id, later, later_id

    def _assert_session_project(self, query, source, sid, first, first_id, later, messages):
        rows = self.search(query)
        self.assertEqual(len(rows), len(messages))
        self.assertEqual(len({row["msg_id"] for row in rows}), len(messages))
        for row in rows:
            self.assertEqual(row["source"], source)
            self.assertEqual(row["session_id"], sid)
            self.assertEqual(row["project_id"], first_id)
            self.assertEqual(row["project_name"], first.name)
        refs = {row["ref"] for row in rows}
        for project in (first_id, first):
            with self.subTest(project_filter=str(project)):
                filtered = self.search(query, "--project", project)
                self.assertEqual(len(filtered), len(messages))
                self.assertEqual({row["ref"] for row in filtered}, refs)
        ref = f"{source}:{sid}"
        self.assertCountEqual(
            [(row["role"], row["text"]) for row in self.session_rows(ref)], messages,
        )
        header_and_messages = self.session_text(ref)
        self.assertIn(str(first), header_and_messages)
        self.assertNotIn(str(later), header_and_messages)
        # Make B independently queryable before asserting a successful empty
        # result. Without a registered B, section 7 also permits a not-found
        # error. A positive control rules out a filter that always returns [].
        # The separate listing test keeps B as a later-cwd-only project.
        self.write_records(self.claude_path("project-filter-control.jsonl"), [
            self.claude("ProjectFilterControlMarker", uuid="project-filter-control",
                        session_id="project-filter-control-session", cwd=later),
        ])
        self.assertEqual(self.index()["messages_added"], 1)
        control = self.search("ProjectFilterControlMarker", "--project", later)
        self.assertEqual(len(control), 1)
        self.assertEqual(control[0]["session_id"], "project-filter-control-session")
        self.assertNotEqual(control[0]["project_id"], first_id)
        for project in (later, control[0]["project_id"], later.name):
            with self.subTest(excluded_project=str(project)):
                self.assertEqual(self.search(query, "--project", project), [])

    def _assert_project_listings(self, visible, hidden):
        # The spec does not prescribe projects JSON field names or ordering.
        # Check list size and identifiers/names without assuming such a schema.
        with self.subTest(interface="projects --json"):
            rows = self.json_cli("projects", "--json")
            self.assertIsInstance(rows, list)
            self.assertEqual(len(rows), len(visible), rows)
            rendered = json.dumps(rows, ensure_ascii=False)
            for name, project_id in visible.items():
                self.assertIn(json.dumps(name), rendered)
                self.assertIn(json.dumps(project_id), rendered)
            for name, project_id in hidden.items():
                self.assertNotIn(json.dumps(name), rendered)
                self.assertNotIn(json.dumps(project_id), rendered)
        with self.subTest(interface="MCP list_projects"):
            client = self.mcp()
            result = client.tool("list_projects")
            self.assertFalse(result["isError"], result)
            text = tool_text(result)
            self.assertEqual(text.strip(), self.cli("projects").stdout.strip())
            for name, project_id in visible.items():
                self.assertIn(name, text)
                self.assertIn(project_id, text)
            for name, project_id in hidden.items():
                self.assertNotIn(name, text)
                self.assertNotIn(project_id, text)

    def test_claude_cwd_change_keeps_all_messages_in_initial_git_project(self):
        first, first_id, later, _ = self._git_pair()
        sid = "claude-cd-session"
        messages = [
            ("user", "ClaudeCwdMarker before"),
            ("assistant", "ClaudeCwdMarker after assistant"),
            ("user", "ClaudeCwdMarker after user"),
        ]
        self.write_records(self.claude_path(), [
            self.claude(text, uuid=f"cd-{number}", session_id=sid, role=role,
                        cwd=first if number == 0 else later)
            for number, (role, text) in enumerate(messages)
        ])
        self.assertEqual(self.index()["messages_added"], len(messages))
        self._assert_session_project(
            "ClaudeCwdMarker", "claude", sid, first, first_id, later, messages,
        )

    def test_claude_incremental_cwd_change_keeps_new_messages_in_initial_project(self):
        first, first_id, later, _ = self._git_pair()
        sid = "claude-incremental-cd-session"
        before = [
            ("user", "ClaudeIncrementalCwdMarker before user"),
            ("assistant", "ClaudeIncrementalCwdMarker before assistant"),
        ]
        after = [
            ("user", "ClaudeIncrementalCwdMarker after user"),
            ("assistant", "ClaudeIncrementalCwdMarker after assistant"),
        ]
        path = self.write_records(self.claude_path(), [
            self.claude(text, uuid=f"before-{number}", session_id=sid, role=role, cwd=first)
            for number, (role, text) in enumerate(before)
        ])
        self.assertEqual(self.index()["messages_added"], len(before))
        original = self.search("ClaudeIncrementalCwdMarker")
        self.assertEqual(len(original), len(before))
        self.assertEqual({row["project_id"] for row in original}, {first_id})
        self.write_records(path, [
            self.claude(text, uuid=f"after-{number}", session_id=sid, role=role, cwd=later)
            for number, (role, text) in enumerate(after)
        ], append=True)
        # index() launches a fresh process: the initial project must survive
        # through persisted parsing/session state, not just an in-memory cache.
        self.assertEqual(self.index()["messages_added"], len(after))
        self._assert_session_project(
            "ClaudeIncrementalCwdMarker", "claude", sid,
            first, first_id, later, before + after,
        )
        self.assertEqual(self.index()["messages_added"], 0)

    def test_claude_first_cwd_record_not_first_indexed_message_sets_project(self):
        first, first_id, later, _ = self._git_pair()
        sid = "claude-initial-system-cwd"
        messages = [("user", "FirstCwdRecordMarker question"),
                    ("assistant", "FirstCwdRecordMarker answer")]
        self.write_records(self.claude_path(), [
            {"type": "system", "sessionId": sid, "cwd": str(first), "timestamp": STAMP},
            *[self.claude(text, uuid=f"first-record-{number}", session_id=sid,
                          role=role, cwd=later)
              for number, (role, text) in enumerate(messages)],
        ])
        self.assertEqual(self.index()["messages_added"], len(messages))
        self._assert_session_project(
            "FirstCwdRecordMarker", "claude", sid, first, first_id, later, messages,
        )

    def test_codex_session_meta_cwd_wins_over_turn_context_before_first_message(self):
        first, first_id, later, _ = self._git_pair()
        sid = "codex-turn-context-cd"
        messages = [("user", "CodexCwdMarker question"),
                    ("assistant", "CodexCwdMarker answer")]
        self.write_records(self.codex_path(), [
            self.codex_meta(sid, cwd=first),
            {"timestamp": STAMP, "type": "turn_context", "payload": {"cwd": str(later)}},
            *[self.codex_message(text, item_id=f"cd-{number}", role=role, thread_id=sid)
              for number, (role, text) in enumerate(messages)],
        ])
        self.assertEqual(self.index()["messages_added"], len(messages))
        self._assert_session_project(
            "CodexCwdMarker", "codex", sid, first, first_id, later, messages,
        )

    def test_projects_and_mcp_hide_project_seen_only_in_later_cwd(self):
        first, first_id, later, later_id = self._git_pair()
        sid = "claude-listing-cd"
        self.write_records(self.claude_path(), [
            self.claude("ListingCwdMarker before", uuid="listing-before", session_id=sid, cwd=first),
            self.claude("ListingCwdMarker after", uuid="listing-after", session_id=sid,
                        role="assistant", cwd=later),
        ])
        self.assertEqual(self.index()["messages_added"], 2)
        # Explicit resolution must not make an otherwise empty project visible.
        self.assertEqual(self.project_id(later), later_id)
        self._assert_project_listings({first.name: first_id}, {later.name: later_id})

    def test_projects_and_mcp_include_empty_main_but_not_non_main_or_resolved_only(self):
        paths = {}
        for name in ("VisibleMessages", "EmptyMainProject", "SubagentOnlyProject",
                     "ImportedOnlyProject", "ResolvedOnlyProject"):
            paths[name] = self.root / name
            paths[name].mkdir()
        self.seed_claude("VisibleProjectMarker", cwd=paths["VisibleMessages"])
        empty_sid = "empty-main-project-thread"
        empty_path = self.write_records(self.codex_path("rollout-empty-main.jsonl"), [
            self.codex_meta(empty_sid, cwd=paths["EmptyMainProject"]),
        ])
        subagent_sid, imported_sid = "project-subagent-thread", "project-imported-thread"
        self.write_records(self.codex_path("rollout-subagent-only.jsonl"), [
            self.codex_meta(subagent_sid, cwd=paths["SubagentOnlyProject"], thread_source="subagent"),
            self.codex_message("HiddenProjectMarker subagent", thread_id=subagent_sid),
        ])
        self.write_records(self.codex_path("rollout-imported-only.jsonl"), [
            self.codex_meta(imported_sid, cwd=paths["ImportedOnlyProject"]),
            self.codex_message("HiddenProjectMarker imported", thread_id=imported_sid),
        ])
        self.imports_file.write_text(json.dumps({
            "records": [{"imported_thread_id": imported_sid}],
        }), encoding="utf-8")
        self.assertEqual(self.index()["messages_added"], 1)
        self.assertEqual(self.search("HiddenProjectMarker"), [])
        self.assertEqual(self.session_rows("codex:" + empty_sid), [])
        self.assertIn(str(empty_path), self.session_text("codex:" + empty_sid))
        self.assertEqual(self.project_id(paths["ResolvedOnlyProject"]),
                         "path:" + str(paths["ResolvedOnlyProject"]))
        visible = {name: "path:" + str(paths[name])
                   for name in ("VisibleMessages", "EmptyMainProject")}
        hidden = {name: "path:" + str(paths[name])
                  for name in ("SubagentOnlyProject", "ImportedOnlyProject", "ResolvedOnlyProject")}
        self._assert_project_listings(visible, hidden)

    def _check_worktree_notes(self, *, worktree_first):
        repository, project_id = self._git_project("repoX")
        worktree = self.root / "repoX-wt"
        self.git("worktree", "add", "--quiet", "-b", "fixture-notes", worktree, "HEAD", cwd=repository)
        first, later = (worktree, repository) if worktree_first else (repository, worktree)
        self.assertNotEqual(first.name, later.name)
        self.write_records(self.claude_path("first.jsonl"), [
            self.claude("WorktreeNameMarker first", uuid="worktree-first",
                        session_id="worktree-first-session", cwd=first),
        ])
        # Sequential indexes establish registration order without mtime races.
        self.assertEqual(self.index()["messages_added"], 1)
        digest = hashlib.sha1(project_id.encode("utf-8")).hexdigest()[:8]
        expected_path = self.home / "notes" / f"{first.name}--{digest}.md"
        first_path = Path(self.cli("notes", "path", "--cwd", first).stdout.strip())
        self.assertEqual(first_path, expected_path)
        self.write_records(self.claude_path("later.jsonl"), [
            self.claude("WorktreeNameMarker later", uuid="worktree-later",
                        session_id="worktree-later-session", cwd=later),
        ])
        self.assertEqual(self.index()["messages_added"], 1)
        rows = self.search("WorktreeNameMarker")
        self.assertEqual(len(rows), 2)
        self.assertEqual({row["project_id"] for row in rows}, {project_id})
        self.assertEqual({row["project_name"] for row in rows}, {first.name})
        for option, value in (("--cwd", later), ("--cwd", first), ("--project", project_id)):
            with self.subTest(option=option, value=str(value)):
                path = Path(self.cli("notes", "path", option, value).stdout.strip())
                self.assertEqual(path, first_path)
        shown = self.cli("notes", "show", "--cwd", later).stdout
        self.assertIn(f"# {first.name} 项目笔记", shown)
        self.assertIn(f"<!-- project_id: {project_id} -->", shown)
        self.assertEqual(list((self.home / "notes").glob("*.md")), [first_path])
        self._assert_project_listings({first.name: project_id}, {})

    def test_worktree_notes_share_path_and_keep_first_registered_repository_name(self):
        self._check_worktree_notes(worktree_first=False)

    def test_worktree_registered_first_keeps_its_name_and_shared_notes_path(self):
        self._check_worktree_notes(worktree_first=True)

    def _check_subagent_import_overlap(self, **metadata):
        sid, control_sid = "overlapping-subagent-import", "overlap-main-control"
        self.imports_file.write_text(json.dumps({
            "records": [{"imported_thread_id": sid}],
        }), encoding="utf-8")
        path = self.write_records(self.codex_path("rollout-overlap.jsonl"), [
            self.codex_meta(sid, **metadata),
            self.codex_message("OverlapSkippedMarker user", thread_id=sid),
            self.codex_message("OverlapSkippedMarker assistant", thread_id=sid,
                               role="assistant", item_id="item-2"),
        ])
        self.write_records(self.codex_path("rollout-control.jsonl"), [
            self.codex_meta(control_sid),
            self.codex_message("OverlapVisibleMarker", thread_id=control_sid),
        ])
        self.assertEqual(self.index()["messages_added"], 1)
        self.assertEqual(len(self.search("OverlapVisibleMarker")), 1)
        self.assertEqual(self.search("OverlapSkippedMarker"), [])
        ref = "codex:" + sid
        text = self.session_text(ref)
        self.assertIn(str(path), text)
        self.assertNotIn("OverlapSkippedMarker", text)
        self.assertEqual(self.session_rows(ref), [])
        self.write_records(path, [
            self.codex_message("OverlapSkippedMarker appended", thread_id=sid, item_id="item-3"),
        ], append=True)
        self.assertEqual(self.index()["messages_added"], 0)
        self.assertEqual(self.search("OverlapSkippedMarker"), [])
        self.assertEqual(self.session_rows(ref), [])
        # The public session text contract does not expose kind. Do not inspect
        # SQLite or invent JSON fields to distinguish subagent from imported;
        # the specified observable outcome here is a retained, empty session.

    def test_subagent_thread_source_also_imported_produces_no_messages(self):
        self._check_subagent_import_overlap(thread_source="subagent")

    def test_subagent_source_object_also_imported_produces_no_messages(self):
        self._check_subagent_import_overlap(source={"subagent": {"parent_thread_id": "parent"}})
