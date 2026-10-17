"""Source parsers yield only the explicitly supported conversation records."""

from dataclasses import dataclass
from datetime import datetime, timezone


@dataclass
class Message:
    key: str
    role: str
    ts: str
    text: str


@dataclass
class Record:
    session_id: str
    cwd: str
    kind: str = "main"
    title: str | None = None
    message: Message | None = None


def timestamp(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    if value.endswith("Z") or parsed.utcoffset().total_seconds() == 0:
        return value
    return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def text_blocks(content: object, accepted: set[str]) -> str:
    if not isinstance(content, list):
        return ""
    return "\n\n".join(
        block["text"]
        for block in content
        if isinstance(block, dict)
        and block.get("type") in accepted
        and isinstance(block.get("text"), str)
    )
