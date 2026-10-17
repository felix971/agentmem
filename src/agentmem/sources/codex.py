"""Codex item_completed records with persistent first-meta identity."""

import json
from pathlib import Path

from . import Message, Record, text_blocks, timestamp


def imported_threads(path: Path) -> set[str]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        records = data.get("records", []) if isinstance(data, dict) else []
        if not isinstance(records, list):
            return set()
        return {
            entry["imported_thread_id"]
            for entry in records
            if isinstance(entry, dict) and isinstance(entry.get("imported_thread_id"), str)
        }
    except (OSError, ValueError):
        return set()


def session_titles(path: Path) -> dict[str, str]:
    titles = {}
    try:
        with path.open("rb") as stream:
            for line in stream:
                try:
                    entry = json.loads(line)
                except (ValueError, UnicodeError):
                    continue
                if isinstance(entry, dict) and isinstance(entry.get("id"), str) and isinstance(entry.get("thread_name"), str):
                    titles[entry["id"]] = entry["thread_name"]
    except OSError:
        pass
    return titles


def parse(record: dict, raw_line: bytes, state: dict, imports: set[str]) -> Record | None:
    payload = record.get("payload")
    if not isinstance(payload, dict):
        return None
    record_type = record.get("type")
    if record_type == "session_meta":
        if state.get("meta_seen"):
            return None
        state["meta_seen"] = True
        thread_id = payload.get("id")
        thread_id = thread_id if isinstance(thread_id, str) else ""
        state["thread_id"] = thread_id
        cwd = payload.get("cwd")
        state["cwd"] = cwd if isinstance(cwd, str) else ""
        state["initial_cwd"] = state["cwd"]
        source = payload.get("source")
        if payload.get("thread_source") == "subagent" or isinstance(source, dict) and "subagent" in source:
            state["kind"] = "subagent"
        elif thread_id in imports:
            state["kind"] = "imported"
        else:
            state["kind"] = "main"
        if state["thread_id"]:
            return Record(state["thread_id"], state["initial_cwd"], state["kind"])
        return None

    thread_id = state.get("thread_id")
    if not thread_id:
        return None
    if record_type == "turn_context":
        if isinstance(payload.get("cwd"), str) and payload["cwd"]:
            state["cwd"] = payload["cwd"]
            return Record(thread_id, state.get("initial_cwd", ""), state["kind"])
        return None
    if record_type != "event_msg" or payload.get("type") != "item_completed":
        return None
    item = payload.get("item")
    if not isinstance(item, dict):
        return None
    role = {"UserMessage": "user", "AgentMessage": "assistant"}.get(item.get("type"))
    if role is None or not isinstance(item.get("id"), str):
        return None
    text = text_blocks(item.get("content"), {"text", "Text"})
    ts = timestamp(record.get("timestamp"))
    if not text or ts is None:
        return None
    original_thread_id = payload.get("thread_id") or thread_id
    turn_id = payload.get("turn_id") or ""
    key = f"codex:{original_thread_id}:{turn_id}:{item['id']}"
    return Record(
        thread_id,
        state.get("initial_cwd", ""),
        state["kind"],
        message=Message(key, role, ts, text),
    )
