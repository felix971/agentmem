"""Environment-backed locations for agentmem's local data."""

from dataclasses import dataclass
import os
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    home: Path
    claude_projects: Path
    codex_sessions: Path
    codex_imports: Path
    codex_session_index: Path


def settings() -> Settings:
    """Read configuration at call time, including for embedded callers."""
    return Settings(
        home=Path(os.environ.get("AGENTMEM_HOME", "~/.local/share/agentmem")).expanduser(),
        claude_projects=Path(os.environ.get("AGENTMEM_CLAUDE_PROJECTS", "~/.claude/projects")).expanduser(),
        codex_sessions=Path(os.environ.get("AGENTMEM_CODEX_SESSIONS", "~/.codex/sessions")).expanduser(),
        codex_imports=Path(os.environ.get("AGENTMEM_CODEX_IMPORTS", "~/.codex/external_agent_session_imports.json")).expanduser(),
        codex_session_index=Path(os.environ.get("AGENTMEM_CODEX_SESSION_INDEX", "~/.codex/session_index.jsonl")).expanduser(),
    )


def ensure_home() -> Path:
    home = settings().home
    home.mkdir(mode=0o700, parents=True, exist_ok=True)
    home.chmod(0o700)
    return home
