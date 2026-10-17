"""Stable project identity and transaction-owned SQLite caching."""

import os
import subprocess


def _normalized_path(cwd: str | os.PathLike[str]) -> str:
    path = os.path.abspath(os.path.expanduser(os.fspath(cwd)))
    return os.path.realpath(path) if os.path.exists(path) else path


def _git(cwd: str, *arguments: str) -> str:
    environment = os.environ.copy()
    environment["GIT_OPTIONAL_LOCKS"] = "0"
    result = subprocess.run(
        ["git", "-C", cwd, *arguments],
        check=True,
        capture_output=True,
        text=True,
        timeout=3,
        env=environment,
    )
    return result.stdout.strip()


def _resolve_uncached(cwd) -> tuple[str, str, str]:
    """Inspect a path without registering a project or opening the database."""
    path = _normalized_path(cwd)
    project_id = "path:" + path
    name = os.path.basename(path)
    kind = "path"
    if os.path.isdir(path):
        try:
            roots = sorted(_git(path, "rev-list", "--max-parents=0", "HEAD").splitlines())
            if roots:
                top_level = _git(path, "rev-parse", "--show-toplevel")
                if top_level:
                    project_id = "git:" + roots[0][:12]
                    name = os.path.basename(top_level)
                    kind = "git"
        except (OSError, subprocess.SubprocessError, UnicodeError):
            pass
    return project_id, name, kind


def _resolve_cached(cwd, conn) -> tuple[str, str, str]:
    path = _normalized_path(cwd)
    cached = conn.execute(
        "SELECT p.project_id, p.name, p.kind FROM cwd_map c "
        "JOIN projects p ON p.project_id = c.project_id WHERE c.cwd = ?",
        (path,),
    ).fetchone()
    if cached is not None:
        project_id, name, kind = cached
        if kind != "git":
            return project_id, name, kind
        if os.path.isdir(path):
            try:
                top_level = _git(path, "rev-parse", "--show-toplevel")
                if top_level:
                    return project_id, name, kind
            except (OSError, subprocess.SubprocessError, UnicodeError):
                pass
        project_id, name, kind = "path:" + path, os.path.basename(path), "path"
    else:
        project_id, name, kind = _resolve_uncached(path)

    conn.execute(
        "INSERT OR IGNORE INTO projects(project_id, name, kind) VALUES (?, ?, ?)",
        (project_id, name, kind),
    )
    conn.execute(
        "INSERT INTO cwd_map(cwd, project_id) VALUES (?, ?) "
        "ON CONFLICT(cwd) DO UPDATE SET project_id = excluded.project_id",
        (path, project_id),
    )
    # A clone or worktree can resolve to an already registered project. Return
    # its original name so every caller uses the same notes path.
    row = conn.execute(
        "SELECT project_id, name, kind FROM projects WHERE project_id = ?",
        (project_id,),
    ).fetchone()
    return tuple(row)


def resolve_project(cwd, conn=None) -> tuple[str, str, str]:
    """Resolve and cache a cwd; never commit a caller-supplied connection."""
    if conn is not None:
        return _resolve_cached(cwd, conn)

    from . import db

    connection = db.connect()
    try:
        result = _resolve_cached(cwd, connection)
        connection.commit()
        return result
    except BaseException:
        connection.rollback()
        raise
    finally:
        connection.close()


def _project_dict(row) -> dict:
    return dict(zip(("project_id", "name", "kind"), tuple(row)))


def match_project(conn, project: str) -> dict:
    """Match an existing ID, case-insensitive name, or resolved path."""
    if not isinstance(project, str) or not project.strip():
        raise ValueError("项目不能为空。")
    row = conn.execute(
        "SELECT project_id, name, kind FROM projects WHERE project_id = ?",
        (project,),
    ).fetchone()
    if row is not None:
        return _project_dict(row)

    rows = conn.execute(
        "SELECT project_id, name, kind FROM projects ORDER BY project_id"
    ).fetchall()
    for candidate in rows:
        if candidate[1].casefold() == project.casefold():
            return _project_dict(candidate)

    path = _normalized_path(project)
    row = conn.execute(
        "SELECT p.project_id, p.name, p.kind FROM cwd_map c "
        "JOIN projects p ON p.project_id = c.project_id WHERE c.cwd = ?",
        (path,),
    ).fetchone()
    if row is None:
        project_id, _, _ = _resolve_uncached(path)
        row = conn.execute(
            "SELECT project_id, name, kind FROM projects WHERE project_id = ?",
            (project_id,),
        ).fetchone()
    if row is None:
        raise ValueError("找不到项目：" + project)
    return _project_dict(row)


def get_project(conn, project=None, cwd=None) -> dict:
    if project is not None:
        return match_project(conn, project)
    project_id, _, _ = resolve_project(cwd if cwd is not None else os.getcwd(), conn)
    # Repository copies can have different basenames. The first registered
    # name is the shared project's name and keeps its notes filename stable.
    row = conn.execute(
        "SELECT project_id, name, kind FROM projects WHERE project_id = ?",
        (project_id,),
    ).fetchone()
    return _project_dict(row)
