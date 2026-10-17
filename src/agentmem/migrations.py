"""One-time repair of version 1 indexes whose cwd followed each message."""

import json
from pathlib import Path

from . import projects
from .sources import claude, codex


MARKER = "session_projects_v1"


def _state(value: str) -> dict:
    try:
        result = json.loads(value)
        return result if isinstance(result, dict) else {}
    except (ValueError, TypeError):
        return {}


def _initial_cwds(source: str, state: dict) -> dict[str, str]:
    if source == "claude":
        entries = state.get("initial_cwds", {})
        if not isinstance(entries, dict):
            return {}
        return {
            sid: cwd for sid, cwd in entries.items()
            if isinstance(sid, str) and sid and isinstance(cwd, str) and cwd
        }
    sid = state.get("thread_id")
    cwd = state.get("initial_cwd")
    if isinstance(sid, str) and sid and isinstance(cwd, str):
        return {sid: cwd}
    return {}


def _read_initial_cwds(path: str, source: str) -> dict[str, str]:
    state = {}
    try:
        with Path(path).open("rb") as stream:
            for line in stream:
                if not line.endswith(b"\n"):
                    break
                try:
                    record = json.loads(line)
                    if not isinstance(record, dict):
                        continue
                    if source == "claude":
                        claude.observe_identity(record, state)
                    else:
                        codex.parse(record, line, state, set())
                        if state.get("meta_seen"):
                            break
                except (ValueError, UnicodeError, TypeError, KeyError, AttributeError):
                    continue
    except OSError:
        # Removed source files must not make archived history inaccessible.
        pass
    return _initial_cwds(source, state)


def _fallback_cwd(connection, session, project_id: str | None) -> str:
    if project_id is None:
        return ""
    if project_id.startswith("path:"):
        return project_id[len("path:"):]
    cwd = session["cwd"]
    if cwd:
        row = connection.execute(
            "SELECT project_id FROM cwd_map WHERE cwd=?",
            (projects._normalized_path(cwd),),
        ).fetchone()
        if row is not None and row[0] == project_id:
            return cwd
    row = connection.execute(
        "SELECT cwd FROM cwd_map WHERE project_id=? ORDER BY length(cwd), cwd LIMIT 1",
        (project_id,),
    ).fetchone()
    return row[0] if row is not None else ""


def _repair(connection) -> None:
    sessions = connection.execute("SELECT * FROM sessions ORDER BY rowid").fetchall()
    files = connection.execute("SELECT * FROM files ORDER BY rowid").fetchall()
    paths = {(row["source"], row["path"]): None for row in files}
    for row in sessions:
        if row["path"]:
            paths.setdefault((row["source"], row["path"]), None)
    for row in connection.execute("SELECT source, file_path FROM messages ORDER BY id"):
        paths.setdefault((row["source"], row["file_path"]), None)

    states = {row["path"]: _state(row["state_json"]) for row in files}
    initial = {}
    for row in files:
        for sid, cwd in _initial_cwds(row["source"], states[row["path"]]).items():
            initial.setdefault((row["source"], sid), cwd)
    for source, path in paths:
        recovered = _read_initial_cwds(path, source)
        paths[source, path] = recovered
        for sid, cwd in recovered.items():
            initial.setdefault((source, sid), cwd)

    earliest_projects = {}
    for row in connection.execute(
        "SELECT source, session_id, project_id FROM messages "
        "WHERE project_id IS NOT NULL ORDER BY id"
    ):
        earliest_projects.setdefault((row["source"], row["session_id"]), row["project_id"])

    canonical = {}
    for session in sessions:
        key = (session["source"], session["session_id"])
        if key in initial:
            cwd = initial[key]
            project_id = projects.resolve_project(cwd, conn=connection)[0] if cwd else None
        else:
            # Version 1 retained only the latest cwd. Without its source log,
            # the earliest archived project is the best surviving evidence;
            # a cached cwd may be representative rather than the original.
            project_id = earliest_projects.get(key, session["project_id"])
            cwd = _fallback_cwd(connection, session, project_id)
        canonical[key] = cwd
        connection.execute(
            "UPDATE sessions SET cwd=?, project_id=? WHERE source=? AND session_id=?",
            (cwd, project_id, *key),
        )
        connection.execute(
            "UPDATE messages SET project_id=? WHERE source=? AND session_id=? "
            "AND project_id IS NOT ?",
            (project_id, *key, project_id),
        )

    for row in files:
        source, path = row["source"], row["path"]
        state = states[path]
        recovered = dict(_initial_cwds(source, state))
        recovered.update(paths[source, path])
        sid = state.get("session_id" if source == "claude" else "thread_id")
        if isinstance(sid, str) and (source, sid) in canonical:
            recovered[sid] = canonical[source, sid]
        recovered = {
            sid: canonical.get((source, sid), cwd) for sid, cwd in recovered.items()
        }
        if source == "claude":
            state["initial_cwds"] = {sid: cwd for sid, cwd in recovered.items() if cwd}
        elif isinstance(sid, str) and sid in recovered:
            state["initial_cwd"] = recovered[sid]
        connection.execute(
            "UPDATE files SET state_json=? WHERE path=?",
            (json.dumps(state, ensure_ascii=False), path),
        )


def migrate_session_projects(connection) -> None:
    if connection.execute("SELECT 1 FROM meta WHERE k=?", (MARKER,)).fetchone():
        return
    connection.execute("BEGIN IMMEDIATE")
    try:
        if not connection.execute("SELECT 1 FROM meta WHERE k=?", (MARKER,)).fetchone():
            _repair(connection)
            connection.execute("INSERT INTO meta(k, v) VALUES (?, '1')", (MARKER,))
        connection.commit()
    except BaseException:
        connection.rollback()
        raise
