"""Standalone tail viewer for codex --json event streams.

Renders JSONL events from `~/.ai-bridge/streams/<job_id>.jsonl` to a terminal
with ANSI colors + ASCII box-drawing (no Unicode dependency).

Usage:
    python tail_viewer.py <stream.jsonl>

Set env CCB_SHOW_TRACE=1 to surface codex's internal tracing log noise.
"""
from __future__ import annotations

import json
import os
import re
import sys
import time


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: tail_viewer.py <stream.jsonl>")
        return 1
    stream = sys.argv[1]

    # Force UTF-8 stdout (Windows parent process may inherit cp936/Latin-1)
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    # Windows: enable ANSI virtual terminal + UTF-8 codepage
    if os.name == "nt":
        try:
            import ctypes
            kernel32 = ctypes.windll.kernel32
            for hid in (-11, -12):
                h = kernel32.GetStdHandle(hid)
                mode = ctypes.c_ulong()
                if kernel32.GetConsoleMode(h, ctypes.byref(mode)):
                    kernel32.SetConsoleMode(h, mode.value | 0x0004)
            kernel32.SetConsoleOutputCP(65001)
        except Exception:
            pass

    ESC = "\x1b"
    R = ESC + "[0m"
    B = ESC + "[1m"
    DIM = ESC + "[2m"
    ITAL = ESC + "[3m"
    RED = ESC + "[31m"
    GRN = ESC + "[32m"
    YEL = ESC + "[33m"
    BLU = ESC + "[34m"
    MAG = ESC + "[35m"
    CYA = ESC + "[36m"
    GRY = ESC + "[90m"

    try:
        WIDTH = max(40, os.get_terminal_size().columns)
    except Exception:
        WIDTH = 80

    SPINNER = "|/-\\"
    spin_state = {"i": 0}

    def spin() -> str:
        c = SPINNER[spin_state["i"] % len(SPINNER)]
        spin_state["i"] += 1
        return c

    def hr(ch: str = "=", color: str = GRY) -> str:
        return color + (ch * WIDTH) + R

    def banner(title: str = "") -> str:
        if not title:
            return hr()
        left = GRY + "== " + R + B + title + R + " "
        visible = 4 + len(title)
        return left + GRY + ("=" * max(0, WIDTH - visible)) + R

    def render_md(text: str) -> list[str]:
        out: list[str] = []
        in_code = False
        for line in text.splitlines():
            m = re.match(r"^```\s*(\w*)\s*$", line)
            if m:
                if not in_code:
                    in_code = True
                    lang = m.group(1) or ""
                    out.append(
                        "  " + GRY + "+-- " + R + CYA + (lang or "code") + R + " "
                        + GRY + ("-" * max(0, WIDTH - 11 - len(lang))) + R
                    )
                else:
                    out.append("  " + GRY + "+" + ("-" * max(0, WIDTH - 3)) + R)
                    in_code = False
                continue
            if in_code:
                out.append("  " + GRY + "| " + R + line)
                continue
            line = re.sub(r"`([^`]+)`", lambda mm: CYA + mm.group(1) + R, line)
            line = re.sub(r"\*\*([^*]+)\*\*", lambda mm: B + mm.group(1) + R, line)
            out.append(line)
        return out

    def emit_agent_message(text: str) -> None:
        if not (text and text.strip()):
            print(DIM + "  (empty assistant message)" + R, flush=True)
            return
        print(flush=True)
        print(banner("assistant"), flush=True)
        for ln in render_md(text):
            print(ln, flush=True)
        print(hr(), flush=True)
        print(flush=True)

    def emit_reasoning(text: str) -> None:
        if not (text and text.strip()):
            return
        print(flush=True)
        for ln in text.splitlines():
            print(GRY + "  | " + ITAL + ln + R, flush=True)

    def emit_tool_start(item: dict) -> None:
        it = item.get("type", "")
        name = item.get("name") or item.get("tool") or it
        cmd = item.get("command") or item.get("arguments") or item.get("args") or ""
        if isinstance(cmd, list):
            cmd = " ".join(str(x) for x in cmd)
        cmd = str(cmd)
        disp = cmd if len(cmd) <= 600 else cmd[:600] + GRY + " ...(truncated)" + R
        print(BLU + ">" + R + " " + B + name + R + "  " + disp, flush=True)

    def emit_tool_done(item: dict) -> None:
        rc = item.get("exit_code")
        dur_ms = item.get("duration_ms")
        dur_s = item.get("duration_sec")
        parts: list[str] = []
        if rc is not None:
            sym = (GRN + "[OK]" + R) if rc == 0 else (RED + "[X]" + R)
            parts.append(sym + " exit=" + str(rc))
        if dur_ms is not None:
            parts.append(GRY + str(int(dur_ms)) + "ms" + R)
        elif dur_s is not None:
            parts.append(GRY + str(dur_s) + "s" + R)
        if parts:
            print("  " + "  ".join(parts), flush=True)

    def emit_file_change(item: dict) -> None:
        path = item.get("path") or item.get("file") or "?"
        action = item.get("action") or ""
        label = (action + " ") if action else ""
        print(MAG + "*" + R + " " + label + B + path + R, flush=True)

    def emit_todo(item: dict) -> None:
        items = item.get("items") or []
        if not items:
            return
        print(flush=True)
        print(GRY + "  todo:" + R, flush=True)
        for t in items:
            text = t.get("text", "")
            done = t.get("completed", False)
            mark = (GRN + "[x]" + R) if done else (GRY + "[ ]" + R)
            color = GRY if done else R
            print("    " + mark + " " + color + text + R, flush=True)

    state = {"retry_count": 0, "last_retry_print": 0.0}

    def handle_event(e: dict) -> None:
        t = e.get("type", "")

        if t == "error":
            msg = str(e.get("message", ""))
            if "Reconnecting" in msg and "stream disconnected" in msg:
                state["retry_count"] += 1
                now = time.time()
                if now - state["last_retry_print"] > 0.25:
                    sys.stdout.write(
                        "\r" + YEL + spin() + R + " " + DIM
                        + "codex network retry x" + str(state["retry_count"]) + R + "  "
                    )
                    sys.stdout.flush()
                    state["last_retry_print"] = now
                return
            print()
            print(RED + "[!] error" + R + " " + msg[:500], flush=True)
            return

        if t == "turn.failed":
            print()
            print(RED + "[!] turn.failed" + R + " " + str(e.get("message", ""))[:500], flush=True)
            return

        if state["retry_count"]:
            sys.stdout.write("\r" + " " * 60 + "\r")
            sys.stdout.flush()
            print(DIM + "(recovered after " + str(state["retry_count"]) + " retries)" + R, flush=True)
            state["retry_count"] = 0

        if t == "thread.started":
            sid = e.get("thread_id", "")
            print(banner("session " + sid[:8]), flush=True)
            return

        if t == "turn.started":
            print(GRY + "-- turn start --" + R, flush=True)
            return

        if t == "turn.completed":
            u = e.get("usage", {}) or {}
            print(
                GRY + "-- turn done  in=" + str(u.get("input_tokens", 0))
                + " out=" + str(u.get("output_tokens", 0)) + " --" + R,
                flush=True,
            )
            return

        if t == "bridge.rotated":
            print(banner("ROTATED to account " + str(e.get("account", "?"))), flush=True)
            return

        if t.startswith("item."):
            item = e.get("item", {}) or {}
            it = item.get("type", "")
            if t == "item.started":
                if it in ("function_call", "tool_call", "command_execution"):
                    emit_tool_start(item)
                elif it == "todo_list":
                    emit_todo(item)
            elif t == "item.completed":
                if it == "agent_message":
                    emit_agent_message(item.get("text") or item.get("message") or "")
                elif it == "reasoning":
                    emit_reasoning(item.get("text") or item.get("message") or "")
                elif it in ("function_call", "tool_call", "command_execution"):
                    emit_tool_done(item)
                elif it == "file_change":
                    emit_file_change(item)
            return

        reason = e.get("reason") or e.get("message") or ""
        if reason:
            print(GRY + "[" + t + "] " + str(reason)[:200] + R, flush=True)

    def is_codex_internal_noise(line: str) -> bool:
        if not line:
            return False
        if re.match(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d+Z\s+(DEBUG|INFO|WARN|ERROR)\s+codex_", line):
            return True
        if line.startswith(("Wall time:", "Output:", "Exit code:", "Error:")):
            return True
        if "codex_core::" in line or "codex_cli::" in line:
            return True
        return False

    show_trace = os.environ.get("CCB_SHOW_TRACE") == "1"

    print(banner("claude-codex-bridge viewer"), flush=True)
    print(DIM + "  stream: " + stream + R, flush=True)
    print(DIM + "  Ctrl+C to close" + R, flush=True)
    print(flush=True)

    try:
        with open(stream, encoding="utf-8", errors="replace") as f:
            while True:
                line = f.readline()
                if not line:
                    time.sleep(0.15)
                    continue
                stripped = line.rstrip()
                try:
                    e = json.loads(stripped)
                except Exception:
                    if is_codex_internal_noise(stripped):
                        if show_trace:
                            print(DIM + stripped + R, flush=True)
                    else:
                        print(DIM + stripped + R, flush=True)
                    continue
                try:
                    handle_event(e)
                except Exception as exc:
                    print(RED + "[viewer error] " + type(exc).__name__ + ": " + str(exc) + R, flush=True)
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
