"""Command line entry points for agentmem."""

import argparse
import json
import math
import os
import sqlite3
import sys
from contextlib import closing

from . import __version__


class Parser(argparse.ArgumentParser):
    def error(self, message):
        self.print_usage(sys.stderr)
        self.exit(1, f"{self.prog}: {message}\n")


def nonnegative_int(value):
    number = int(value)
    if number < 0:
        raise argparse.ArgumentTypeError("必须是非负整数")
    return number


def positive_int(value):
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError("必须是正整数")
    return number


def budget_seconds(value):
    number = float(value)
    if not math.isfinite(number) or number < 0:
        raise argparse.ArgumentTypeError("预算必须是有限的非负秒数")
    return number


def build_parser():
    parser = Parser(prog="agentmem", description="共享本机聊天历史与项目笔记")
    parser.add_argument("--version", action="version", version=__version__)
    commands = parser.add_subparsers(dest="command", required=True)

    index = commands.add_parser("index", help="增量索引会话文件")
    index.add_argument("--quiet", action="store_true")
    index.add_argument("--budget", type=budget_seconds)

    search = commands.add_parser("search", help="检索历史聊天")
    search.add_argument("query")
    search.add_argument("--project")
    search.add_argument("--source", choices=("claude", "codex"))
    search.add_argument("--role", choices=("user", "assistant"))
    search.add_argument("--limit", type=positive_int, default=8)
    search.add_argument("--json", action="store_true")

    session = commands.add_parser("session", help="查看会话及消息上下文")
    session.add_argument("ref")
    session.add_argument("--around", type=positive_int)
    session.add_argument("--context", type=nonnegative_int, default=6)
    session.add_argument("--limit", type=positive_int, default=40)
    session.add_argument("--json", action="store_true")

    recent = commands.add_parser("recent", help="最近会话")
    recent.add_argument("--project")
    recent.add_argument("--limit", type=positive_int, default=10)
    recent.add_argument("--json", action="store_true")

    projects = commands.add_parser("projects", help="已知项目")
    projects.add_argument("--json", action="store_true")
    project_id = commands.add_parser("project-id", help="解析项目身份")
    project_id.add_argument("path", nargs="?", default=None)

    notes = commands.add_parser("notes", help="项目笔记")
    note_commands = notes.add_subparsers(dest="note_command", required=True)
    for name in ("show", "path"):
        command = note_commands.add_parser(name)
        selector = command.add_mutually_exclusive_group()
        selector.add_argument("--project")
        selector.add_argument("--cwd")

    commands.add_parser("stats", help="索引统计（JSON）")
    hook = commands.add_parser("hook", help="会话生命周期 hook")
    hook.add_argument("hook_event", choices=("session-start",))
    commands.add_parser("mcp", help="JSON-RPC stdio MCP 服务")
    return parser


def print_json(value):
    print(json.dumps(value, ensure_ascii=False))


def run(args):
    # Protocol entry points must own stdout and handle failures themselves.
    if args.command == "hook":
        from .hook import session_start

        return session_start() or 0
    if args.command == "mcp":
        from .mcp_server import serve

        return serve() or 0
    if args.command == "index":
        from .indexer import index

        result = index(budget=args.budget, wait_for_lock=True)
        if not args.quiet:
            print_json(result)
        return 0

    from .db import connect
    from . import notes, projects, search

    with closing(connect()) as conn:
        if args.command == "project-id":
            project_id, _, _ = projects.resolve_project(args.path or os.getcwd(), conn)
            conn.commit()
            print(project_id)
        elif args.command == "notes":
            result = notes.get_project_notes(
                project=args.project,
                cwd=args.cwd,
                conn=conn,
            )
            conn.commit()
            if args.note_command == "path":
                print(result["path"])
            else:
                print(result["content"], end="" if result["content"].endswith("\n") else "\n")
        elif args.command == "search":
            rows = search.search_history(
                conn, args.query, project=args.project,
                source=args.source, role=args.role, limit=args.limit,
            )
            print_json(rows) if args.json else print(search.format_search(rows))
        elif args.command == "session":
            session = search.get_session(
                conn, args.ref, around=args.around,
                context=args.context, limit=args.limit,
            )
            # JSON commands return lists; the text formatter includes the session header.
            print_json(session["messages"]) if args.json else print(search.format_session(session))
        elif args.command == "recent":
            rows = search.recent_sessions(conn, project=args.project, limit=args.limit)
            print_json(rows) if args.json else print(search.format_recent(rows))
        elif args.command == "projects":
            rows = search.list_projects(conn)
            print_json(rows) if args.json else print(search.format_projects(rows))
        elif args.command == "stats":
            result = {
                "schema_version": conn.execute(
                    "SELECT v FROM meta WHERE k='schema_version'"
                ).fetchone()[0],
            }
            for table in ("files", "sessions", "messages", "projects"):
                result[table] = conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
            result["sources"] = {
                row["source"]: row["n"]
                for row in conn.execute("SELECT source, count(*) AS n FROM messages GROUP BY source")
            }
            print_json(result)
    return 0


def main(argv=None):
    os.umask(0o077)
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return run(args)
    except (ValueError, OSError, sqlite3.Error, OverflowError) as exc:
        print(f"agentmem: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
