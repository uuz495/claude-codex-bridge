"""Fire-and-forget native-TUI spawn tools.

Spawns codex as a real interactive TUI in a new terminal window (wezterm /
Windows Terminal / bare new console / Unix x-terminal-emulator). The user
sees the actual codex CLI — same rendering, same parallel-command handling,
same everything as `codex` typed at a shell.

Why native TUI instead of `codex exec --json`:
  - --json mode emits a strictly smaller event set than the TUI's rollout
    file (no inline diffs, no streaming command output, no reasoning
    summaries in many cases).
  - --json mode on Windows hits a real codex 0.130 bug where parallel
    command_execution events can lose their item.completed signal,
    leaving the stream silent for hours even though the child shell
    processes have already exited.
  - DETACHED_PROCESS + stdout-to-file plumbing is finicky on Windows
    (especially when wrappers like model-rewrite shims are in the chain).

Tracking is via codex's own session log:
  ~/.codex/sessions/YYYY/MM/DD/rollout-<timestamp>-<session_id>.jsonl

This rollout file is written for every codex session (TUI or exec) and is a
strict superset of --json — it includes apply_patch unified diffs, full
command stdout, reasoning items, token counts, and task_complete events.

Three tools:
  - spawn_codex_window: open a new terminal window running codex --yolo
    <prompt>. Returns immediately with a job_id; bridge has no codex handle.
  - peek_codex: parse the rollout file for activity facts. Never reports
    process liveness; only "what the rollout shows".
  - wait_for_codex: block until rollout has an event_msg/task_complete or
    timeout.
"""
from __future__ import annotations

import asyncio
import os
import re
import shutil
import subprocess
import time
import uuid
from pathlib import Path

from . import accounts, config
from .activity import (
    parse_codex_activity,
    parse_rollout_activity,
    peek_last_jsonl_event,
)
from .cli import codex_safe_cwd, resolve_cli, resolve_node_cli
from .jobs import load_job, now_iso, write_job
from .paths import IS_WINDOWS, JOBS_DIR
from .spawn import with_summary_tail

DEFAULT_TIMEOUT = 30 * 60

_SID_RE = re.compile(
    r"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})"
)

_AUTH_SWAP_LOCK = asyncio.Lock()


def _rollout_root() -> Path:
    return Path.home() / ".codex" / "sessions"


def _snapshot_rollouts() -> set[Path]:
    """Return current set of rollout-*.jsonl paths under ~/.codex/sessions."""
    root = _rollout_root()
    if not root.exists():
        return set()
    try:
        return set(root.rglob("rollout-*.jsonl"))
    except Exception:
        return set()


async def _wait_for_new_rollout(
    pre_snapshot: set[Path], timeout_sec: float
) -> Path | None:
    """Poll for a rollout file that appeared after `pre_snapshot` was taken."""
    deadline = time.time() + timeout_sec
    while time.time() < deadline:
        current = _snapshot_rollouts()
        new = current - pre_snapshot
        if new:
            return max(new, key=lambda p: p.stat().st_mtime)
        await asyncio.sleep(0.1)
    return None


def _extract_sid_from_rollout_name(path: Path) -> str:
    m = _SID_RE.search(path.name)
    return m.group(1) if m else ""


def _codex_tui_flags() -> list[str]:
    """Flags for `codex` (TUI mode, not `exec`).

    --yolo is the short form of --dangerously-bypass-approvals-and-sandbox.
    `--skip-git-repo-check` is `codex exec`-only and rejected by the TUI
    entry point with "unexpected argument" → exit 2 → wezterm window flashes
    and closes; do NOT add it here.
    """
    flags = [
        "--yolo",
        "-m", str(config.get("codex_model")),
        "-c", "model_reasoning_effort=" + str(config.get("codex_reasoning_effort")),
        "-c", "model_reasoning_summary=" + str(config.get("codex_reasoning_summary")),
    ]
    if config.get("codex_fast_mode"):
        flags += ["--enable", "fast_mode"]
    return flags


def _spawn_codex_in_new_terminal(
    codex_argv: list[str],
    cwd: str,
    env: dict,
) -> tuple[bool, str]:
    """Open a new terminal window running `codex_argv` with a real TTY.

    Order of preference: wezterm > Windows Terminal (wt) > xterm-family on Unix
    > bare new console as last fallback. Returns (success, terminal_name).
    """
    if IS_WINDOWS:
        CREATE_NEW_CONSOLE = 0x00000010

        wezterm = shutil.which("wezterm") or shutil.which("wezterm.exe")
        if wezterm:
            try:
                subprocess.Popen(
                    [wezterm, "start", "--always-new-process", "--cwd", cwd, "--", *codex_argv],
                    env=env,
                )
                return True, "wezterm"
            except Exception:
                pass

        wt = shutil.which("wt") or shutil.which("wt.exe")
        if wt:
            try:
                subprocess.Popen(
                    [wt, "new-tab", "--title", "codex", "--startingDirectory", cwd, *codex_argv],
                    env=env,
                )
                return True, "wt"
            except Exception:
                pass

        # Bare new console — works but no UTF-8 codepage guarantee
        try:
            subprocess.Popen(
                codex_argv, cwd=cwd, env=env,
                creationflags=CREATE_NEW_CONSOLE,
            )
            return True, "new_console"
        except Exception as e:
            return False, f"failed: {e}"

    # Unix
    for term in ("x-terminal-emulator", "gnome-terminal", "xterm",
                 "alacritty", "kitty", "wezterm"):
        path = shutil.which(term)
        if not path:
            continue
        try:
            if term == "gnome-terminal":
                subprocess.Popen([path, "--working-directory", cwd, "--", *codex_argv], env=env)
            elif term == "wezterm":
                subprocess.Popen([path, "start", "--always-new-process",
                                  "--cwd", cwd, "--", *codex_argv], env=env)
            else:
                subprocess.Popen([path, "-e", *codex_argv], cwd=cwd, env=env)
            return True, term
        except Exception:
            continue

    return False, "no terminal found"


def register(mcp) -> None:
    @mcp.tool()
    async def spawn_codex_window(
        prompt: str,
        session_id: str | None = None,
        account: str | None = None,
        auto_rotate: bool = False,
        viability_check_sec: float = 5.0,
    ) -> dict:
        """Open codex as a native TUI in a new terminal window.

        The window runs `codex --yolo "prompt"` directly with a real TTY —
        what the user sees IS the codex TUI (apply_patch blocks, inline
        diffs, command output, reasoning summaries — all rendered by codex
        itself, not by the bridge).

        For tracking, the bridge locates the codex session's rollout file at
        ~/.codex/sessions/YYYY/MM/DD/rollout-<ts>-<session_id>.jsonl and uses
        it for peek_codex / wait_for_codex.

        Note: auto_rotate defaults to False here because mid-task account
        switch is not feasible once codex is running interactively. Pass an
        explicit `account` if you need a specific account; otherwise the
        current CODEX_HOME (typically the default account) is used.

        Returns:
            {job_id, session_id, rollout_file, window_opened, window_terminal,
             account_used, note}
        """
        if not prompt or not prompt.strip():
            return {"error": "[FAIL] empty prompt"}
        prompt = with_summary_tail(prompt)

        # Prefer node + entry.js/.mjs over the .cmd shim — wezterm-as-launcher
        # cannot execute a .cmd file directly on Windows (it needs cmd.exe in
        # the chain). resolve_node_cli returns [node.exe, entry] for that path;
        # falling back to resolve_cli is OK on Unix where the binary is a real
        # executable.
        codex_prefix = resolve_node_cli("codex")
        if not codex_prefix:
            bin_path = resolve_cli("codex")
            codex_prefix = [bin_path] if bin_path else None
        if not codex_prefix:
            return {"error": "[FAIL] codex not in PATH. `npm i -g @openai/codex` first."}

        chosen: str | None = None
        env = os.environ.copy()
        if account:
            async with _AUTH_SWAP_LOCK:
                ok, err = accounts.activate_account(account)
                if not ok:
                    return {"error": f"[FAIL] activate {account}: {err}"}
                chosen = account
                env["CODEX_HOME"] = str(accounts.account_home(account))

        cwd = codex_safe_cwd()
        flags = _codex_tui_flags()
        if session_id:
            codex_argv = [*codex_prefix, "resume", session_id, *flags, prompt]
        else:
            codex_argv = [*codex_prefix, *flags, "--cd", cwd, prompt]

        pre_snapshot = _snapshot_rollouts()

        window_opened, window_terminal = _spawn_codex_in_new_terminal(
            codex_argv, cwd, env
        )
        if not window_opened:
            return {"error": f"[FAIL] could not open terminal: {window_terminal}"}

        rollout_path = await _wait_for_new_rollout(pre_snapshot, viability_check_sec)

        actual_sid = ""
        if rollout_path:
            actual_sid = _extract_sid_from_rollout_name(rollout_path)

        job_id = "j-" + uuid.uuid4().hex[:10]
        job = {
            "job_id": job_id,
            "status": "running",
            "mode": "native_tui",
            "prompt": prompt,
            "session_id": actual_sid or (session_id or ""),
            "rollout_file": str(rollout_path) if rollout_path else "",
            "cwd": cwd,
            "account_used": chosen,
            "started_at": now_iso(),
            "window_terminal": window_terminal,
        }
        write_job(job)

        if chosen:
            accounts.mark_account(chosen, "active")

        return {
            "job_id": job_id,
            "session_id": actual_sid,
            "rollout_file": str(rollout_path) if rollout_path else "",
            "window_opened": window_opened,
            "window_terminal": window_terminal,
            "account_used": chosen,
            "note": (
                "Native TUI in a real terminal — what you see is codex itself, "
                "not a viewer. Use peek_codex(job_id) for activity (parsed from "
                "rollout file), wait_for_codex(job_id, timeout_sec) to block "
                "until task_complete. The window stays open after task complete "
                "for follow-up input — close it manually when done."
                + ("" if rollout_path else "  ⚠ rollout file not found within "
                   f"{viability_check_sec}s; codex may have failed to start.")
            ),
        }

    @mcp.tool()
    async def peek_codex(job_id: str, tail_n: int = 20) -> dict:
        """Report codex job progress from the rollout file.

        Returns:
            completed: rollout has an event_msg/task_complete event
            final_message: last_agent_message from task_complete
            stream_static_for_sec: seconds since rollout file last grew
            tool_calls_count / file_changes_count / agent_messages_count
            recent_events: tail of structured activity
            status_summary: 'completed' / 'active' / 'silent for Xs'
        """
        job = load_job(job_id)
        if job is None:
            return {"error": f"[FAIL] no such job '{job_id}'"}

        rollout_file = job.get("rollout_file")
        stream_file = job.get("stream_file")
        source_file = rollout_file or stream_file
        is_rollout = bool(rollout_file)

        result: dict = {
            "job_id": job_id,
            "session_id": job.get("session_id", ""),
            "account_used": job.get("account_used"),
            "started_at": job.get("started_at"),
            "rollout_file": rollout_file,
            "stream_file": stream_file,
            "mode": job.get("mode", ""),
            "window_terminal": job.get("window_terminal"),
        }

        if not source_file or not Path(source_file).exists():
            result["completed"] = False
            result["status_summary"] = "no rollout/stream file yet"
            return result

        sp = Path(source_file)
        try:
            mtime = sp.stat().st_mtime
            result["stream_static_for_sec"] = round(time.time() - mtime, 1)
        except Exception:
            pass

        try:
            if is_rollout:
                activity = parse_rollout_activity(sp)
            else:
                activity = parse_codex_activity(sp)
            result["tool_calls_count"] = len(activity["tool_calls"])
            result["file_changes_count"] = len(activity["file_changes"])
            result["agent_messages_count"] = len(activity["agent_messages"])
            result["retry_count"] = activity["retry_count"]
            result["errors"] = activity["errors"][-5:]
            result["completed"] = activity.get("completed", False)
            if activity.get("final_message"):
                result["final_message"] = activity["final_message"]

            recent: list[dict] = []
            for tc in activity["tool_calls"][-tail_n:]:
                recent.append({
                    "kind": "tool",
                    "name": tc.get("name", ""),
                    "summary": tc.get("summary", "")[:200],
                })
            for am in activity["agent_messages"][-3:]:
                recent.append({"kind": "msg", "summary": am[:300]})
            result["recent_events"] = recent
        except Exception as e:
            result["activity_parse_error"] = f"{type(e).__name__}: {e}"

        if not is_rollout:
            # Legacy --json mode: also probe last raw event for visibility
            try:
                result.update(peek_last_jsonl_event(sp))
            except Exception:
                pass

        silent = result.get("stream_static_for_sec", 0) or 0
        if result.get("completed"):
            result["status_summary"] = "completed"
        elif silent > 300:
            result["status_summary"] = (
                f"rollout silent for {int(silent)}s — codex may be in long "
                "reasoning / stuck on a tool / network blip / window closed"
            )
        elif silent > 60:
            result["status_summary"] = f"rollout silent for {int(silent)}s"
        else:
            result["status_summary"] = "active"

        return result

    @mcp.tool()
    async def wait_for_codex(
        job_id: str,
        timeout_sec: int = 600,
        poll_interval_sec: float = 1.0,
    ) -> dict:
        """Block until codex job's rollout file shows event_msg/task_complete.

        For legacy --json jobs (mode != native_tui), falls back to
        last_message_file detection. On timeout returns current peek_codex
        with timed_out=True. No pid checks.
        """
        job = load_job(job_id)
        if job is None:
            return {"error": f"[FAIL] no such job '{job_id}'"}

        deadline = time.time() + timeout_sec
        while time.time() < deadline:
            snapshot = await peek_codex(job_id)
            if snapshot.get("completed"):
                snapshot["timed_out"] = False
                return snapshot
            await asyncio.sleep(poll_interval_sec)

        snapshot = await peek_codex(job_id)
        snapshot["timed_out"] = True
        return snapshot
