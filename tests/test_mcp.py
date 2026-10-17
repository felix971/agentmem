"""DESIGN 12.9: real persistent MCP stdio, including malformed requests."""

import fcntl

from tests.helpers import AgentmemCase, tool_text


TOOLS = {
    "search_history", "get_session", "recent_sessions", "list_projects",
    "get_project_notes", "update_project_notes",
}


class MCPProtocolTests(AgentmemCase):
    def test_initialize_negotiates_supported_versions_and_fallback(self):
        versions = ("2024-11-05", "2025-03-26", "2025-06-18", "2025-11-25", "not-supported")
        for version in versions:
            with self.subTest(version=version):
                client = self.mcp(initialize=False)
                response = client.request("initialize", {
                    "protocolVersion": version, "capabilities": {},
                    "clientInfo": {"name": "unittest", "version": "1"},
                })
                self.assertNotIn("error", response)
                result = response["result"]
                self.assertEqual(result["protocolVersion"], "2025-06-18" if version == "not-supported" else version)
                self.assertEqual(result["capabilities"], {"tools": {"listChanged": False}})
                self.assertEqual(result["serverInfo"]["name"], "agentmem")
                self.assertIsInstance(result["serverInfo"]["version"], str)
                self.assertTrue(result["serverInfo"]["version"])
                client.close()

    def test_initialize_includes_short_string_instructions(self):
        client = self.mcp(initialize=False)
        response = client.request("initialize", {
            "protocolVersion": "2025-06-18", "capabilities": {},
            "clientInfo": {"name": "unittest", "version": "1"},
        })
        self.assertNotIn("error", response)
        result = response["result"]
        self.assertIn("instructions", result)
        self.assertIsInstance(result["instructions"], str)
        self.assertLessEqual(len(result["instructions"]), 512)

    def test_initialize_does_not_index_on_startup(self):
        self.seed_claude("NoStartupIndexMarker")
        client = self.mcp()
        client.close()
        self.assertEqual(self.index()["messages_added"], 1)

    def test_tools_list_contains_exactly_six_object_schema_tools(self):
        client = self.mcp()
        response = client.request("tools/list")
        self.assertNotIn("error", response)
        tools = response["result"]["tools"]
        self.assertEqual(len(tools), 6)
        self.assertEqual({tool["name"] for tool in tools}, TOOLS)
        for tool in tools:
            self.assertEqual(tool["inputSchema"]["type"], "object")
            self.assertIsInstance(tool.get("description"), str)
            self.assertTrue(tool["description"].strip())

    def test_tools_list_annotations_have_correct_read_only_hints(self):
        client = self.mcp()
        response = client.request("tools/list")
        self.assertNotIn("error", response)
        tools = {tool["name"]: tool for tool in response["result"]["tools"]}
        expected_hints = {
            "search_history": True,
            "get_session": True,
            "recent_sessions": True,
            "list_projects": True,
            "get_project_notes": True,
            "update_project_notes": False,
        }
        for name, read_only in expected_hints.items():
            with self.subTest(tool=name):
                self.assertIn(name, tools)
                self.assertIn("annotations", tools[name])
                annotations = tools[name]["annotations"]
                self.assertIsInstance(annotations, dict)
                self.assertIn("readOnlyHint", annotations)
                self.assertIs(annotations["readOnlyHint"], read_only)

    def test_normal_history_tools_match_cli_text_and_lazily_index(self):
        self.seed_claude("McpEvidenceMarker SQLite")
        client = self.mcp()
        search = client.tool("search_history", {"query": "McpEvidenceMarker"})
        self.assertFalse(search["isError"], search)
        self.assertIn("McpEvidenceMarker", tool_text(search))
        self.assertEqual(tool_text(search).strip(), self.cli("search", "McpEvidenceMarker").stdout.strip())
        ref = self.search("McpEvidenceMarker")[0]["ref"]
        for tool_name, args, cli_args in (
            ("get_session", {"ref": ref}, ("session", ref)),
            ("recent_sessions", {}, ("recent",)),
            ("list_projects", {}, ("projects",)),
        ):
            with self.subTest(tool=tool_name):
                result = client.tool(tool_name, args)
                self.assertFalse(result["isError"], result)
                self.assertEqual(tool_text(result).strip(), self.cli(*cli_args).stdout.strip())

    def test_unknown_method_returns_method_not_found_and_continues(self):
        client = self.mcp()
        result = client.request("not/a/real/method")
        self.assertEqual(result["error"]["code"], -32601)
        self.assertEqual(client.request("ping")["result"], {})

    def test_empty_optional_arguments_are_treated_as_omitted(self):
        self.seed_claude("McpEmptyOptionalMarker")
        client = self.mcp()
        expected = client.tool("search_history", {"query": "McpEmptyOptionalMarker"})
        self.assertFalse(expected["isError"], expected)
        self.assertIn("McpEmptyOptionalMarker", tool_text(expected))

        cases = (
            {"project": "", "source": "", "role": ""},
            {"project": " \t\n", "source": "\t ", "role": "\n\u3000"},
            {"limit": None},
            {"project": "", "source": "", "role": "", "limit": None},
        )
        for optional in cases:
            with self.subTest(arguments=optional):
                result = client.tool("search_history", {
                    "query": "McpEmptyOptionalMarker", **optional,
                })
                self.assertFalse(result["isError"], result)
                self.assertIn("McpEmptyOptionalMarker", tool_text(result))
                self.assertEqual(tool_text(result), tool_text(expected))

    def test_bad_json_returns_parse_error_then_server_still_works(self):
        client = self.mcp()
        client.send_raw('{"jsonrpc": "2.0", broken')
        response = client.receive()
        self.assertIsNone(response["id"])
        self.assertEqual(response["error"]["code"], -32700)
        self.assertEqual(client.request("ping")["result"], {})
        self.assertEqual(len(client.request("tools/list")["result"]["tools"]), 6)

    def test_batch_is_invalid_request_and_server_continues(self):
        client = self.mcp()
        client.send([{"jsonrpc": "2.0", "id": 1000, "method": "ping"}])
        response = client.receive()
        self.assertEqual(response["error"]["code"], -32600)
        self.assertEqual(client.request("ping")["result"], {})

    def test_notifications_have_no_response(self):
        client = self.mcp()
        client.notify("notifications/initialized")
        client.notify("some/unknown/notification", {"value": 1})
        # A notification response would be the next queued line, and its id
        # would fail the following real request's id assertion.
        self.assertEqual(client.request("ping")["result"], {})
        client.assert_no_reply()

    def test_invalid_tool_arguments_are_iserror_not_rpc_errors(self):
        client = self.mcp()
        cases = (
            ("search_history", {}),
            ("search_history", {"query": 42}),
            ("search_history", {"query": "fixture", "limit": 0}),
            ("search_history", {"query": "fixture", "limit": 31}),
            ("search_history", {"query": "fixture", "source": "unknown"}),
            ("search_history", {"query": "fixture", "role": "system"}),
            ("get_session", {"ref": 42}),
            ("update_project_notes", {"content": "missing base"}),
            ("not_a_real_tool", {}),
        )
        for name, arguments in cases:
            with self.subTest(tool=name, arguments=arguments):
                result = client.tool(name, arguments)
                self.assertTrue(result["isError"], result)
                self.assertTrue(tool_text(result).strip())
                self.assertEqual(client.request("ping")["result"], {})

    def test_missing_session_tool_error_does_not_exit_server(self):
        client = self.mcp()
        result = client.tool("get_session", {"ref": "nonexistent-session-1234"})
        self.assertTrue(result["isError"], result)
        self.assertEqual(client.request("ping")["result"], {})

    def test_held_index_lock_serves_existing_data_without_indexing(self):
        self.seed_claude("McpCachedMarker")
        self.index()
        self.write_records(self.claude_path("later.jsonl"), [
            self.claude("McpUnindexedMarker", uuid="later", session_id="mcp-later-session"),
        ])
        with (self.home / "index.lock").open("a") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            client = self.mcp()
            result = client.tool("search_history", {"query": "McpCachedMarker"}, timeout=3)
            self.assertFalse(result["isError"], result)
            self.assertIn("McpCachedMarker", tool_text(result))
            result = client.tool("search_history", {"query": "McpUnindexedMarker"}, timeout=3)
            self.assertFalse(result["isError"], result)
            self.assertNotIn("McpUnindexedMarker", tool_text(result))
            self.assertEqual(client.request("ping")["result"], {})
        client.close()
        self.assertEqual(self.index()["messages_added"], 1)
