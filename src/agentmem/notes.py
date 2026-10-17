"""Private project notes with atomic writes and optimistic concurrency."""

from contextlib import contextmanager
import fcntl
import hashlib
import os
from pathlib import Path
import re
import subprocess
import tempfile

from . import config, db, projects


class NotesConflictError(ValueError):
    def __init__(self, content: str, sha256: str):
        self.current_content = content
        self.current_sha256 = sha256
        super().__init__(
            "项目笔记已变更，base_sha256 不匹配。\n"
            "当前 sha256: " + sha256 + "\n当前内容:\n" + content
        )


def notes_path(project_id: str, name: str) -> Path:
    """Compute the filename without creating the directory or a template."""
    safe_name = re.sub(r"[^A-Za-z0-9._-]", "_", name)
    suffix = hashlib.sha1(project_id.encode("utf-8")).hexdigest()[:8]
    return config.settings().home / "notes" / (safe_name + "--" + suffix + ".md")


def _git(directory: Path, *arguments: str) -> bool:
    try:
        subprocess.run(
            ["git", "-C", str(directory), *arguments],
            check=True,
            capture_output=True,
            timeout=10,
        )
        return True
    except FileNotFoundError:
        return False


def _init_git(directory: Path) -> bool:
    return (directory / ".git").exists() or _git(directory, "init", "-q")


def _git_failure(content: str, error: Exception) -> ValueError:
    sha256 = hashlib.sha256(content.encode("utf-8")).hexdigest()
    return ValueError(
        "项目笔记已保存，但 git 操作失败（" + type(error).__name__ + "），"
        "未确认提交成功。\n当前 sha256: " + sha256
    )


@contextmanager
def _notes_lock():
    home = config.settings().home
    home.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(home, 0o700)
    lock_path = home / "notes.lock"
    descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        os.fchmod(descriptor, 0o600)
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        directory = home / "notes"
        directory.mkdir(mode=0o700, exist_ok=True)
        os.chmod(directory, 0o700)
        yield directory
    finally:
        os.close(descriptor)


def _atomic_write(path: Path, content: str) -> None:
    descriptor, temporary = tempfile.mkstemp(prefix=".agentmem-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _template(project: dict) -> str:
    return (
        "# " + project["name"] + " 项目笔记\n"
        "<!-- project_id: " + project["project_id"] + " -->\n\n"
        "## 决定\n\n## 进度\n\n## 已撤回 / 已替代\n"
    )


def _result(project: dict, path: Path, content: str) -> dict:
    return {
        "project_id": project["project_id"],
        "name": project["name"],
        "path": str(path),
        "content": content,
        "sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
    }


def _read_content(path: Path) -> str:
    # Preserve line endings so the returned hash describes the actual bytes.
    with path.open("r", encoding="utf-8", newline="") as handle:
        return handle.read()


@contextmanager
def _project_connection(conn):
    owned = conn is None
    if owned:
        conn = db.connect()
    try:
        yield conn
        if owned:
            conn.commit()
    except BaseException:
        if owned:
            conn.rollback()
        raise
    finally:
        if owned:
            conn.close()


def get_project_notes(project=None, cwd=None, conn=None) -> dict:
    with _project_connection(conn) as connection:
        resolved = projects.get_project(connection, project=project, cwd=cwd)
        path = notes_path(resolved["project_id"], resolved["name"])
        with _notes_lock() as directory:
            if not path.exists():
                _atomic_write(path, _template(resolved))
            content = _read_content(path)
            try:
                _init_git(directory)
            except (OSError, subprocess.SubprocessError) as exc:
                raise _git_failure(content, exc) from exc
            return _result(resolved, path, content)


def update_project_notes(
    content: str,
    base_sha256: str,
    project=None,
    cwd=None,
    agent: str = "agentmem",
    conn=None,
) -> dict:
    if not isinstance(content, str):
        raise ValueError("笔记内容必须是字符串。")
    if len(content) > 8000:
        raise ValueError("笔记内容不能超过 8000 字符。")
    if not isinstance(base_sha256, str):
        raise ValueError("base_sha256 必须是字符串。")
    with _project_connection(conn) as connection:
        resolved = projects.get_project(connection, project=project, cwd=cwd)
        path = notes_path(resolved["project_id"], resolved["name"])
        with _notes_lock() as directory:
            if not path.exists():
                _atomic_write(path, _template(resolved))
            current = _read_content(path)
            current_sha = hashlib.sha256(current.encode("utf-8")).hexdigest()
            if base_sha256 != current_sha:
                raise NotesConflictError(current, current_sha)
            _atomic_write(path, content)
            try:
                if _init_git(directory) and _git(directory, "add", "-A"):
                    _git(
                        directory,
                        "-c", "user.name=agentmem",
                        "-c", "user.email=agentmem@localhost",
                        "-c", "commit.gpgsign=false",
                        "commit", "--allow-empty", "-q", "-m",
                        resolved["name"] + ": update via " + str(agent),
                    )
            except (OSError, subprocess.SubprocessError) as exc:
                raise _git_failure(content, exc) from exc
            return _result(resolved, path, content)
