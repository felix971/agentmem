"""Claude Code JSONL records, excluding tool and injected context content."""

import hashlib

from . import Message, Record, text_blocks, timestamp


def observe_identity(record: dict, state: dict) -> tuple[str, str] | None:
    """Remember the first cwd even on records whose content is ignored."""
    session_id = record.get("sessionId") or state.get("session_id")
    if not isinstance(session_id, str) or not session_id:
        return None
    state["session_id"] = session_id
    initial_cwds = state.setdefault("initial_cwds", {})
    cwd = record.get("cwd")
    if isinstance(cwd, str) and cwd:
        state["cwd"] = cwd
        if not initial_cwds.get(session_id):
            initial_cwds[session_id] = cwd
    return session_id, initial_cwds.get(session_id, "")


def parse(record: dict, raw_line: bytes, state: dict) -> Record | None:
    session_id = record.get("sessionId") or state.get("session_id")
    previous_cwd = state.get("initial_cwds", {}).get(session_id) if isinstance(session_id, str) else None
    identity = observe_identity(record, state)
    if identity is None or record.get("isSidechain") is True:
        return None
    session_id, cwd = identity
    # Ignoring a record's content must not discard the session's first cwd.
    initial_record = Record(session_id, cwd) if cwd and not previous_cwd else None
    record_type = record.get("type")
    if record_type not in {"user", "assistant", "custom-title", "ai-title"}:
        return initial_record
    if record_type == "user" and record.get("isMeta") is True:
        return initial_record
    titles = state.setdefault("titles", {}).setdefault(session_id, {})
    if record_type in {"custom-title", "ai-title"}:
        field = "customTitle" if record_type == "custom-title" else "aiTitle"
        if isinstance(record.get(field), str):
            titles[field] = record[field]
        title = titles.get("customTitle", titles.get("aiTitle"))
        return Record(session_id, cwd, title=title)

    message = record.get("message")
    if not isinstance(message, dict):
        return initial_record
    content = message.get("content")
    if record_type == "user":
        if message.get("role") != "user":
            return initial_record
        text = content if isinstance(content, str) else text_blocks(content, {"text"})
    else:
        text = text_blocks(content, {"text"})
    ts = timestamp(record.get("timestamp"))
    if not text or ts is None:
        return initial_record
    uuid = record.get("uuid")
    key = "claude:" + (str(uuid) if uuid else hashlib.sha1(raw_line).hexdigest())
    title = titles.get("customTitle", titles.get("aiTitle"))
    return Record(
        session_id,
        cwd,
        title=title,
        message=Message(key, record_type, ts, text),
    )
