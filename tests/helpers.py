"""Public CLI/stdio helpers. Never import agentmem or inspect its DB schema."""

import hashlib
import json
import os
from pathlib import Path
import queue
import re
import subprocess
import tempfile
import threading
import unittest
from typing import Any


REPOSITORY = Path(__file__).resolve().parents[1]
STAMP = "2026-10-01T01:00:00.000Z"
SEARCH_FIELDS = {
    "ref", "source", "session_id", "msg_id", "project_id", "project_name",
    "role", "ts", "snippet",
}
INDEX_FIELDS = {
    "files_seen", "files_changed", "messages_added", "skipped_lines",
    "errors", "elapsed_s", "complete",
}


def json_line(record):
    return (json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")


def sha256_text(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def tool_text(result):
    return "\n".join(part["text"] for part in result["content"] if part.get("type") == "text")


def tool_strings(result):
    """Accept either plain text or JSON encoded inside MCP text content.

    The spec promises notes content and a SHA, but does not prescribe their
    serialization inside the tool's text. Do not assume invented field names.
    """
    found = []

    def visit(value):
        if isinstance(value, str):
            found.append(value)
        elif isinstance(value, dict):
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    for part in result["content"]:
        if part.get("type") == "text":
            found.append(part["text"])
            try:
                visit(json.loads(part["text"]))
            except (ValueError, TypeError):
                pass
    return "\n".join(found)


class AgentmemCase(unittest.TestCase):
    """Every test gets its own sources, fake HOME, Git config and data home."""

    def setUp(self):
        super().setUp()
        self.sandbox = tempfile.TemporaryDirectory(prefix="agentmem-test-")
        self.addCleanup(self.sandbox.cleanup)
        self.root = Path(self.sandbox.name).resolve()
        self.home = self.root / "agentmem-home"  # Let agentmem create this.
        self.project = self.root / "ProjectAlpha"
        self.other_project = self.root / "ProjectBeta"
        self.claude_projects = self.root / "claude-projects"
        self.codex_sessions = self.root / "codex-sessions"
        self.imports_file = self.root / "codex-config" / "imports.json"
        self.session_index = self.root / "codex-config" / "session_index.jsonl"
        for directory in (
            self.project, self.other_project, self.claude_projects,
            self.codex_sessions, self.imports_file.parent,
            self.root / "user-home", self.root / "tmp",
        ):
            directory.mkdir(parents=True, exist_ok=True)
        self.imports_file.write_text('{"records": []}\n', encoding="utf-8")
        self.session_index.write_text("", encoding="utf-8")

        self.env = {
            key: value for key, value in os.environ.items()
            if not key.startswith(("AGENTMEM_", "GIT_"))
        }
        self.env.pop("PYTHONPYCACHEPREFIX", None)
        self.env.update({
            "HOME": str(self.root / "user-home"),
            "XDG_DATA_HOME": str(self.root / "xdg-data"),
            "XDG_CONFIG_HOME": str(self.root / "xdg-config"),
            "XDG_CACHE_HOME": str(self.root / "xdg-cache"),
            "TMPDIR": str(self.root / "tmp"),
            "PYTHONPATH": str(REPOSITORY / "src"),
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONIOENCODING": "utf-8",
            "PYTHONUTF8": "1",
            "AGENTMEM_HOME": str(self.home),
            "AGENTMEM_CLAUDE_PROJECTS": str(self.claude_projects),
            "AGENTMEM_CODEX_SESSIONS": str(self.codex_sessions),
            "AGENTMEM_CODEX_IMPORTS": str(self.imports_file),
            "AGENTMEM_CODEX_SESSION_INDEX": str(self.session_index),
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_CEILING_DIRECTORIES": str(self.root),
            "GIT_AUTHOR_NAME": "t",
            "GIT_AUTHOR_EMAIL": "t@t",
            "GIT_COMMITTER_NAME": "t",
            "GIT_COMMITTER_EMAIL": "t@t",
            "GIT_TERMINAL_PROMPT": "0",
        })

    def claude(self, content, *, uuid: str | None = "u1", session_id="claude-session-0001",
               role="user", cwd=None, ts=STAMP, **extra):
        if role == "assistant" and isinstance(content, str):
            content = [{"type": "text", "text": content}]
        record = {
            "type": role, "isSidechain": False, "sessionId": session_id,
            "cwd": str(cwd or self.project), "timestamp": ts,
            "message": {"role": role, "content": content},
        }
        if uuid is not None:
            record["uuid"] = uuid
        record.update(extra)
        return record

    def codex_meta(self, session_id="codex-thread-0001", *, cwd=None,
                   source: Any = "cli", thread_source: Any = "user"):
        return {
            "timestamp": STAMP, "type": "session_meta",
            "payload": {"id": session_id, "cwd": str(cwd or self.project),
                        "source": source, "thread_source": thread_source},
        }

    def codex_message(self, content, *, item_id="item-1", role="user",
                      thread_id: str | None = "codex-thread-0001",
                      turn_id: str | None = "turn-1", ts=STAMP):
        if isinstance(content, str):
            content = [{"type": "text" if role == "user" else "Text", "text": content}]
        payload = {
            "type": "item_completed",
            "item": {"type": "UserMessage" if role == "user" else "AgentMessage",
                     "id": item_id, "content": content},
        }
        if thread_id is not None:
            payload["thread_id"] = thread_id
        if turn_id is not None:
            payload["turn_id"] = turn_id
        return {"timestamp": ts, "type": "event_msg", "payload": payload}

    def claude_path(self, name="main.jsonl"):
        return self.claude_projects / "encoded-project" / name

    def codex_path(self, name="rollout-main.jsonl"):
        return self.codex_sessions / "2026" / "10" / "02" / name

    def write_records(self, path, records, *, append=False):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("ab" if append else "wb") as stream:
            for record in records:
                stream.write(json_line(record))
        return path

    def seed_claude(self, content: Any = "EvidenceMarker", **kwargs):
        return self.write_records(self.claude_path(), [self.claude(content, **kwargs)])

    def command(self, *args):
        return ["python3", "-m", "agentmem", *map(str, args)]

    def child_env(self, extra=None):
        env = self.env.copy()
        if extra:
            env.update(extra)
        return env

    def cli(self, *args, input_text=None, expected=0, timeout=15, env=None, cwd=None):
        try:
            completed = subprocess.run(
                self.command(*args), input=input_text, capture_output=True,
                text=True, encoding="utf-8", cwd=cwd or self.project,
                env=self.child_env(env), timeout=timeout, umask=0o022,
            )
        except subprocess.TimeoutExpired as exc:
            raise AssertionError(f"CLI timed out after {timeout}s: {self.command(*args)!r}\n{exc}") from exc
        if expected is not None:
            self.assertEqual(
                completed.returncode, expected,
                f"CLI {self.command(*args)!r}\nstdout:\n{completed.stdout}\nstderr:\n{completed.stderr}",
            )
        return completed

    def parse_json(self, text, *, description="output"):
        try:
            return json.loads(text)
        except (ValueError, TypeError) as exc:
            self.fail(f"Invalid JSON {description}: {text!r}: {exc}")

    def json_cli(self, *args, **kwargs):
        return self.parse_json(self.cli(*args, **kwargs).stdout)

    def index(self, *args):
        report = self.json_cli("index", *args, timeout=35)
        self.assertIsInstance(report, dict)
        self.assertTrue(INDEX_FIELDS <= report.keys(), report)
        return report

    def search(self, query, *options, limit=1000):
        rows = self.json_cli("search", query, *options, "--limit", limit, "--json")
        self.assertIsInstance(rows, list)
        for row in rows:
            self.assertIsInstance(row, dict)
            self.assertTrue(SEARCH_FIELDS <= row.keys(), row)
        return rows

    def session_rows(self, ref, *options):
        # Section 9 spells out session text headers, but does not separately
        # define session JSON fields. Search uses --json; here parse only the
        # documented public text, rather than inventing a JSON schema.
        text = self.session_text(ref, *options)
        headers = list(re.finditer(r"(?m)^#(\d+)[ \t]+(\S+)[ \t]+(user|assistant):[ \t]*\n?", text))
        rows = []
        for number, match in enumerate(headers):
            end = headers[number + 1].start() if number + 1 < len(headers) else len(text)
            rows.append({"msg_id": int(match.group(1)), "ts": match.group(2),
                         "role": match.group(3), "text": text[match.end():end].strip()})
        return rows

    def session_text(self, ref, *options):
        return self.cli("session", ref, *options).stdout

    def project_id(self, path=None):
        args = ("project-id",) if path is None else ("project-id", str(path))
        return self.cli(*args).stdout.strip()

    def git(self, *args, cwd=None, expected=0):
        result = subprocess.run(
            ["git", *map(str, args)], cwd=cwd or self.project,
            env=self.env, capture_output=True, text=True, encoding="utf-8", timeout=15,
        )
        self.assertEqual(result.returncode, expected, result.stdout + result.stderr)
        return result.stdout.strip()

    def create_git_repo(self, path):
        path.mkdir(parents=True, exist_ok=True)
        self.git("init", "--quiet", cwd=path)
        self.git("-c", "user.name=t", "-c", "user.email=t@t", "commit",
                 "--quiet", "--allow-empty", "-m", "fixture root", cwd=path)
        return path

    def start_cli(self, *args):
        process = subprocess.Popen(
            self.command(*args), stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            encoding="utf-8", cwd=self.project, env=self.env, umask=0o022,
        )
        self.addCleanup(self.stop_process, process)
        return process

    @staticmethod
    def stop_process(process):
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=2)
        for stream in (process.stdin, process.stdout, process.stderr):
            if stream is not None and not stream.closed:
                stream.close()

    def mcp(self, *, initialize=True):
        client = MCPClient(self)
        self.addCleanup(client.close)
        if initialize:
            response = client.request("initialize", {
                "protocolVersion": "2025-06-18", "capabilities": {},
                "clientInfo": {"name": "agentmem-unittest", "version": "1"},
            })
            self.assertIn("result", response, response)
            client.notify("notifications/initialized")
        return client

    def notes_fixture(self, *, cwd=None):
        cwd = Path(cwd or self.project)
        project_id = self.project_id(cwd)
        shown = self.cli("notes", "show", "--cwd", cwd).stdout
        safe_name = re.sub(r"[^A-Za-z0-9._-]", "_", cwd.name)
        digest = hashlib.sha1(project_id.encode("utf-8")).hexdigest()[:8]
        path = self.home / "notes" / f"{safe_name}--{digest}.md"
        self.assertTrue(path.is_file(), f"Missing specified notes path: {path}")
        return path, path.read_text(encoding="utf-8"), shown


class MCPClient:
    """Actual newline-delimited JSON-RPC over a long-lived subprocess's pipes."""

    def __init__(self, case):
        self.case = case
        self.counter = 0
        self.closed = False
        self.stderr = tempfile.TemporaryFile(dir=case.root)
        self.process = subprocess.Popen(
            case.command("mcp"), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=self.stderr, text=True, encoding="utf-8", bufsize=1,
            cwd=case.project, env=case.env, umask=0o022,
        )
        assert self.process.stdin is not None
        assert self.process.stdout is not None
        self.stdin = self.process.stdin
        self.stdout = self.process.stdout
        self.lines = queue.Queue()
        self.reader = threading.Thread(target=self._read_stdout, daemon=True)
        self.reader.start()

    def _read_stdout(self):
        try:
            for line in self.stdout:
                self.lines.put(line)
        finally:
            self.lines.put(None)

    def diagnostics(self):
        return os.pread(self.stderr.fileno(), 65536, 0).decode("utf-8", errors="replace")

    def send_raw(self, line):
        try:
            self.stdin.write(line + "\n")
            self.stdin.flush()
        except (BrokenPipeError, OSError) as exc:
            self.case.fail(f"MCP stdin closed: {exc}\nstderr:\n{self.diagnostics()}")

    def send(self, value):
        self.send_raw(json.dumps(value, ensure_ascii=False))

    def receive(self, *, timeout=6):
        try:
            line = self.lines.get(timeout=timeout)
        except queue.Empty as exc:
            raise AssertionError(f"MCP response timed out\nstderr:\n{self.diagnostics()}") from exc
        if line is None:
            self.process.wait(timeout=2)
            self.case.fail(f"MCP exited ({self.process.returncode})\nstderr:\n{self.diagnostics()}")
        reply = self.case.parse_json(line, description="on MCP stdout")
        self.case.assertIsInstance(reply, dict, reply)
        self.case.assertEqual(reply.get("jsonrpc"), "2.0", reply)
        return reply

    def request(self, method, params=None, *, timeout=6):
        self.counter += 1
        request_id = self.counter
        message = {"jsonrpc": "2.0", "id": request_id, "method": method}
        if params is not None:
            message["params"] = params
        self.send(message)
        response = self.receive(timeout=timeout)
        self.case.assertEqual(response.get("id"), request_id, response)
        return response

    def notify(self, method, params=None):
        message = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            message["params"] = params
        self.send(message)

    def tool(self, name, arguments=None, *, timeout=6):
        response = self.request("tools/call", {
            "name": name, "arguments": {} if arguments is None else arguments,
        }, timeout=timeout)
        self.case.assertNotIn("error", response, response)
        result = response.get("result")
        self.case.assertIsInstance(result, dict, response)
        self.case.assertIsInstance(result.get("isError"), bool, result)
        self.case.assertIsInstance(result.get("content"), list, result)
        self.case.assertTrue(result["content"], result)
        for part in result["content"]:
            self.case.assertEqual(part.get("type"), "text", part)
            self.case.assertIsInstance(part.get("text"), str, part)
        return result

    def assert_no_reply(self, timeout=0.15):
        try:
            line = self.lines.get(timeout=timeout)
        except queue.Empty:
            return
        self.case.fail(f"Unexpected MCP output/exit: {line!r}\n{self.diagnostics()}")

    def close(self):
        if self.closed:
            return
        self.closed = True
        try:
            if self.process.stdin and not self.process.stdin.closed:
                self.process.stdin.close()
        except BrokenPipeError:
            pass
        try:
            self.process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            self.process.terminate()
            try:
                self.process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=2)
        self.reader.join(timeout=2)
        self.stdout.close()
        self.stderr.close()
