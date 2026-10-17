"""Transactional, locked incremental ingestion of source-owned JSONL files."""

from datetime import datetime, timezone
import fcntl
import io
import json
import math
import os
from pathlib import Path
import sqlite3
import time

from . import db, projects, textproc
from .config import ensure_home, settings
from .sources import claude, codex


def _instant(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _time_bound(current: str | None, value: str | None, earliest: bool) -> str | None:
    if not value:
        return current
    if not current:
        return value
    before = _instant(value) < _instant(current)
    return value if before == earliest else current


def _candidates(config, stats: dict) -> list[tuple[Path, str, os.stat_result]]:
    candidates = []
    for root, source, pattern in (
        (config.claude_projects, "claude", "*.jsonl"),
        (config.codex_sessions, "codex", "rollout-*.jsonl"),
    ):
        try:
            for path in root.rglob(pattern):
                if source == "claude" and "subagents" in path.parts[:-1]:
                    continue
                try:
                    stat = path.stat()
                    if path.is_file():
                        candidates.append((path, source, stat))
                except OSError:
                    stats["errors"] += 1
        except OSError:
            stats["errors"] += 1
    candidates.sort(key=lambda candidate: (candidate[2].st_mtime_ns, str(candidate[0])))
    return candidates


def _merge_session(connection, source: str, session_id: str, update: dict) -> None:
    existing = connection.execute(
        "SELECT started_at, last_at FROM sessions WHERE source=? AND session_id=?",
        (source, session_id),
    ).fetchone()
    if existing:
        update["started_at"] = _time_bound(existing["started_at"], update["started_at"], True)
        update["last_at"] = _time_bound(existing["last_at"], update["last_at"], False)
    connection.execute(
        """INSERT INTO sessions
           (source, session_id, project_id, cwd, title, kind, path, started_at, last_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(source, session_id) DO UPDATE SET
             project_id=COALESCE(sessions.project_id, excluded.project_id),
             cwd=CASE WHEN COALESCE(sessions.cwd, '')='' THEN excluded.cwd ELSE sessions.cwd END,
             title=COALESCE(excluded.title, sessions.title),
             kind=excluded.kind, path=excluded.path,
             started_at=excluded.started_at, last_at=excluded.last_at""",
        (
            source, session_id, update["project_id"], update["cwd"], update["title"],
            update["kind"], update["path"], update["started_at"], update["last_at"],
        ),
    )
    connection.execute(
        """UPDATE messages SET project_id=(
               SELECT project_id FROM sessions WHERE source=? AND session_id=?
           ) WHERE source=? AND session_id=? AND project_id IS NOT (
               SELECT project_id FROM sessions WHERE source=? AND session_id=?
           )""",
        (source, session_id, source, session_id, source, session_id),
    )


def _process_file(connection, path: Path, source: str, stat, previous, imports, titles) -> dict:
    reset = previous is None or previous["inode"] != stat.st_ino or stat.st_size < previous["offset"]
    offset = 0 if reset else previous["offset"]
    state = {} if reset else json.loads(previous["state_json"])
    if not isinstance(state, dict):
        state = {}
        offset = 0
    counters = {"messages_added": 0, "skipped_lines": 0}
    updates = {}
    project_cache = {}
    path_string = str(path)

    def session_update(session_id, cwd, kind):
        if session_id not in updates:
            existing = connection.execute(
                "SELECT project_id, cwd FROM sessions WHERE source=? AND session_id=?",
                (source, session_id),
            ).fetchone()
            updates[session_id] = {
                "project_id": existing["project_id"] if existing else None,
                "cwd": existing["cwd"] if existing else cwd,
                "title": None, "kind": kind, "path": path_string,
                "started_at": None, "last_at": None,
            }
        update = updates[session_id]
        if source == "claude" and not update["cwd"] and cwd:
            update["cwd"] = cwd
        initial_cwd = update["cwd"]
        if update["project_id"] is None and initial_cwd:
            if initial_cwd not in project_cache:
                project_cache[initial_cwd] = projects.resolve_project(initial_cwd, conn=connection)[0]
            update["project_id"] = project_cache[initial_cwd]
        return update

    with path.open("rb") as stream:
        actual_stat = os.fstat(stream.fileno())
        if actual_stat.st_ino != stat.st_ino or actual_stat.st_size < offset:
            offset = 0
            state = {}
        stat = actual_stat
        stream.seek(offset)
        data = stream.read(max(0, stat.st_size - offset))

    end = data.rfind(b"\n") + 1
    complete_data = data[:end]
    position = offset
    connection.execute("BEGIN")
    try:
        for line in io.BytesIO(complete_data):
            line_offset = position
            position += len(line)
            if not line.strip():
                continue
            try:
                value = json.loads(line)
                if not isinstance(value, dict):
                    counters["skipped_lines"] += 1
                    continue
                if source == "claude":
                    parsed = claude.parse(value, line, state)
                else:
                    parsed = codex.parse(value, line, state, imports)
            except (ValueError, UnicodeError, TypeError, KeyError, AttributeError):
                counters["skipped_lines"] += 1
                continue
            if parsed is None:
                continue

            update = session_update(parsed.session_id, parsed.cwd, parsed.kind)
            if source == "codex":
                update["title"] = titles.get(parsed.session_id)
            elif parsed.title is not None:
                update["title"] = parsed.title
            message = parsed.message
            if message is None:
                continue
            text = textproc.clean_text(message.text)
            if not text:
                continue
            update["started_at"] = _time_bound(update["started_at"], message.ts, True)
            update["last_at"] = _time_bound(update["last_at"], message.ts, False)
            if parsed.kind != "main":
                continue
            inserted = connection.execute(
                """INSERT OR IGNORE INTO messages
                   (key, source, session_id, project_id, role, ts, text, file_path, file_offset)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (message.key, source, parsed.session_id, update["project_id"], message.role,
                 message.ts, text, path_string, line_offset),
            )
            if inserted.rowcount:
                connection.execute(
                    "INSERT INTO messages_fts(rowid, tok) VALUES (?, ?)",
                    (inserted.lastrowid, textproc.index_tokens(text)),
                )
                counters["messages_added"] += 1

        for session_id, update in updates.items():
            _merge_session(connection, source, session_id, update)
        connection.execute(
            """INSERT INTO files(path, source, inode, size, mtime_ns, offset, state_json, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(path) DO UPDATE SET
                 source=excluded.source, inode=excluded.inode, size=excluded.size,
                 mtime_ns=excluded.mtime_ns, offset=excluded.offset,
                 state_json=excluded.state_json, updated_at=excluded.updated_at""",
            (path_string, source, stat.st_ino, stat.st_size, stat.st_mtime_ns, offset + end,
             json.dumps(state, ensure_ascii=False), datetime.now(timezone.utc).isoformat()),
        )
        connection.commit()
    except BaseException:
        connection.rollback()
        raise
    return counters


def index(budget: float | None = None, wait_for_lock: bool = True) -> dict:
    """Index complete new lines, returning JSON-serializable run statistics."""
    if budget is not None and (not math.isfinite(budget) or budget < 0):
        raise ValueError("budget must be finite and nonnegative")
    started = time.monotonic()
    deadline = None if budget is None else started + budget
    stats = {
        "files_seen": 0, "files_changed": 0, "messages_added": 0,
        "skipped_lines": 0, "errors": 0, "elapsed_s": 0.0, "complete": True,
    }
    connection = None
    lock = None
    locked = False
    try:
        config = settings()
        home = ensure_home()
        lock = os.open(home / "index.lock", os.O_CREAT | os.O_RDWR, 0o600)
        os.fchmod(lock, 0o600)
        while not locked:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                locked = True
            except BlockingIOError:
                if not wait_for_lock or deadline is not None and time.monotonic() >= deadline:
                    stats["complete"] = False
                    return stats
                remaining = 0.05 if deadline is None else min(0.05, max(0, deadline - time.monotonic()))
                time.sleep(remaining)

        connection = db.connect()
        imports = codex.imported_threads(config.codex_imports)
        titles = codex.session_titles(config.codex_session_index)
        with connection:
            for session_id, title in titles.items():
                connection.execute(
                    "UPDATE sessions SET title=? WHERE source='codex' AND session_id=?",
                    (title, session_id),
                )
        candidates = _candidates(config, stats)
        stats["files_seen"] = len(candidates)
        for path, source, stat in candidates:
            if deadline is not None and time.monotonic() >= deadline:
                stats["complete"] = False
                break
            previous = connection.execute("SELECT * FROM files WHERE path=?", (str(path),)).fetchone()
            if previous is not None and previous["inode"] == stat.st_ino and previous["size"] == stat.st_size and previous["mtime_ns"] == stat.st_mtime_ns:
                continue
            try:
                counters = _process_file(connection, path, source, stat, previous, imports, titles)
            except (OSError, sqlite3.Error, ValueError, TypeError):
                connection.rollback()
                stats["errors"] += 1
                continue
            stats["files_changed"] += 1
            for name, count in counters.items():
                stats[name] += count
    finally:
        if connection is not None:
            connection.close()
        if lock is not None:
            if locked:
                fcntl.flock(lock, fcntl.LOCK_UN)
            os.close(lock)
        finished = time.monotonic()
        stats["elapsed_s"] = round(finished - started, 6)
        if deadline is not None and finished > deadline:
            stats["complete"] = False
    return stats
