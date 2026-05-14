"""Parse a JSONL event stream into a structured activity summary.

Used by peek_codex / wait_for_codex / poll_codex_job to report what codex has
done so far — agent messages, tool calls, file changes, retry count — without
needing to monitor the codex process directly.
"""
from __future__ import annotations

import json
from pathlib import Path


def parse_codex_activity(stream_path: Path) -> dict:
    activity = {
        "agent_messages": [],
        "tool_calls": [],
        "file_changes": [],
        "reasoning_excerpts": [],
        "errors": [],
        "retry_count": 0,
    }
    if not stream_path.exists():
        return activity
    try:
        with stream_path.open(encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    e = json.loads(line)
                except Exception:
                    continue
                t = e.get("type", "")
                if t == "error":
                    msg = e.get("message", "")
                    if "Reconnecting" in msg or "stream disconnected" in msg:
                        activity["retry_count"] += 1
                    else:
                        activity["errors"].append({"type": "error", "message": msg[:500]})
                elif t == "turn.failed":
                    activity["errors"].append(
                        {"type": "turn.failed", "message": e.get("message", "")[:500]}
                    )
                elif t == "item.completed":
                    item = e.get("item", {}) or {}
                    it = item.get("type", "")
                    text = item.get("text") or item.get("message") or ""
                    if it == "agent_message" and text:
                        activity["agent_messages"].append(text)
                    elif it == "reasoning" and text:
                        activity["reasoning_excerpts"].append(text[:600])
                    elif it in ("function_call", "tool_call", "command_execution"):
                        activity["tool_calls"].append(
                            {
                                "kind": it,
                                "name": item.get("name") or item.get("tool") or "",
                                "summary": str(
                                    item.get("arguments")
                                    or item.get("args")
                                    or item.get("command")
                                    or ""
                                )[:300],
                            }
                        )
                    elif it == "file_change":
                        # Codex JSONL schema: item.changes = [{path, kind}, ...]
                        changes = item.get("changes") or []
                        if changes:
                            for ch in changes:
                                activity["file_changes"].append(
                                    {
                                        "path": ch.get("path") or ch.get("file") or "",
                                        "action": ch.get("kind") or ch.get("action") or "",
                                    }
                                )
                        else:
                            activity["file_changes"].append(
                                {
                                    "path": item.get("path") or item.get("file") or "",
                                    "action": item.get("kind") or item.get("action") or "",
                                }
                            )
                    else:
                        activity["tool_calls"].append(
                            {"kind": it or "?", "name": "", "summary": str(item)[:300]}
                        )
    except Exception:
        pass
    return activity


def peek_last_jsonl_event(stream_path: Path) -> dict:
    """Look at the tail of the stream and return the most recent meaningful event."""
    info = {
        "last_event_type": "",
        "last_item_type": "",
        "last_item_status": "",
        "last_item_summary": "",
    }
    try:
        with stream_path.open("rb") as f:
            f.seek(0, 2)
            size = f.tell()
            f.seek(max(0, size - 16384))
            tail = f.read().decode("utf-8", errors="replace")
        lines = [l for l in tail.split("\n") if l.strip()]
        for line in reversed(lines):
            try:
                e = json.loads(line)
            except Exception:
                continue
            t = e.get("type", "")
            if t == "error" and "Reconnecting" in str(e.get("message", "")):
                continue
            info["last_event_type"] = t
            item = e.get("item") or {}
            if item:
                info["last_item_type"] = item.get("type", "") or ""
                info["last_item_status"] = item.get("status", "") or ""
                summary = (
                    item.get("command")
                    or item.get("name")
                    or item.get("text")
                    or item.get("arguments")
                    or ""
                )
                info["last_item_summary"] = str(summary)[:200]
            break
    except Exception:
        pass
    return info
