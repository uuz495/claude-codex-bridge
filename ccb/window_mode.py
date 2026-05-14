"""Fire-and-forget window-mode tools.

This is the recommended path. Three tools:
  - spawn_codex_window: detach codex into its own process + open viewer window.
    Returns immediately with a job_id. Claude never inspects codex's pid; that
    eliminates the false 'codex died' verdict caused by pid wrappers (wt → cmd
    → node → codex.js) exiting before the inner codex finishes.
  - peek_codex: report stream facts (silent for Xs / tool count / last event).
    Never says 'codex is alive/dead' — only what the file shows.
  - wait_for_codex: block until last_message_file is written or timeout.
"""
from __future__ import annotations

import asyncio
import time
import uuid
from pathlib import Path

from . import accounts, config
from .activity import parse_codex_activity, peek_last_jsonl_event
from .classify import classify_codex_result, parse_quota_reset
from .cli import codex_safe_cwd, resolve_cli, resolve_node_cli
from .jobs import load_job, now_iso, write_job
from .paths import JOBS_DIR, STREAM_DIR
from .spawn import (
    SUMMARY_TAIL,  # noqa: F401  (re-exported for tests)
    codex_pinned_flags,
    extract_session_id_from_jsonl,
    spawn_codex_detached,
    with_summary_tail,
)
from .viewer import open_tail_window

DEFAULT_TIMEOUT = 30 * 60

_AUTH_SWAP_LOCK = asyncio.Lock()


def register(mcp) -> None:
    @mcp.tool()
    async def spawn_codex_window(
        prompt: str,
        session_id: str | None = None,
        account: str | None = None,
        auto_rotate: bool = True,
        viability_check_sec: float = 3.0,
    ) -> dict:
        """Fire-and-forget Codex spawn: detached process + auto-opened viewer window.

        Key properties vs spawn_codex_background:
          - DETACHED_PROCESS on Windows: survives MCP server restart.
          - No process handle retained: Claude never judges codex liveness by pid.
          - Completion is detected by --output-last-message file appearance, not pid.
          - Progress queries: peek_codex(job_id) or wait_for_codex(job_id, timeout_sec).

        Multi-account rotation (if enabled in config): on early death (within
        viability_check_sec) classified as quota/banned/auth_invalid, the account
        is marked and the next eligible account is tried. On success, the account
        is marked active and the call returns.

        Returns:
            {job_id, session_id, stream_file, last_message_file,
             window_opened, window_terminal, account_used, note}
        """
        if not prompt or not prompt.strip():
            return {"error": "[FAIL] empty prompt"}
        prompt = with_summary_tail(prompt)

        codex_prefix = resolve_node_cli("codex") or (
            [resolve_cli("codex")] if resolve_cli("codex") else None
        )
        if not codex_prefix:
            return {"error": "[FAIL] codex not in PATH. `npm i -g @openai/codex` first."}

        job_id = "j-" + uuid.uuid4().hex[:10]
        stream_path = STREAM_DIR / f"{job_id}.jsonl"
        last_msg_path = JOBS_DIR / f"{job_id}.last.md"
        stream_path.touch()

        rotation_on = accounts.is_rotation_enabled()
        tried: set[str] = set()
        last_error: str | None = None

        base_flags = [
            *codex_pinned_flags(),
            "--skip-git-repo-check",
            "--dangerously-bypass-approvals-and-sandbox",
            "--json",
            "--output-last-message", str(last_msg_path),
        ]

        while True:
            chosen: str | None = None
            if account:
                chosen = account
            elif rotation_on:
                chosen = accounts.pick_next_eligible_account(tried)
                if not chosen:
                    return {
                        "error": "[FAIL] all accounts exhausted/banned",
                        "tried_accounts": sorted(tried),
                        "last_error": last_error,
                    }

            async with _AUTH_SWAP_LOCK:
                extra_env: dict = {}
                if chosen:
                    ok, err = accounts.activate_account(chosen)
                    if not ok:
                        tried.add(chosen)
                        last_error = f"activate {chosen} failed: {err}"
                        if account or not auto_rotate:
                            return {"error": f"[FAIL] {last_error}", "account_used": chosen}
                        continue
                    extra_env["CODEX_HOME"] = str(accounts.account_home(chosen))

                if session_id:
                    cmd = [*codex_prefix, "exec", "resume", session_id, *base_flags, prompt]
                else:
                    cmd = [*codex_prefix, "exec", "--cd", codex_safe_cwd(), *base_flags, prompt]

                try:
                    proc = spawn_codex_detached(cmd, stream_path, codex_safe_cwd(), extra_env)
                except Exception as e:
                    if chosen:
                        tried.add(chosen)
                    last_error = f"spawn exception: {type(e).__name__}: {e}"
                    if account or not auto_rotate:
                        return {"error": f"[FAIL] {last_error}", "account_used": chosen}
                    continue

            await asyncio.sleep(viability_check_sec)
            if proc.poll() is not None:
                rc = proc.returncode
                try:
                    stream_text = stream_path.read_text(encoding="utf-8", errors="replace")[-4000:]
                except Exception:
                    stream_text = ""
                classification = classify_codex_result(rc, stream_text, "")
                if chosen:
                    if classification == "quota_exhausted":
                        reset_at = parse_quota_reset(stream_text)
                        accounts.mark_account(
                            chosen, "quota_exhausted",
                            blocked_until=reset_at,
                            ttl_hours=config.get("default_quota_ttl_hours") if reset_at is None else None,
                        )
                    elif classification == "banned":
                        accounts.mark_account(chosen, "banned", ttl_hours=config.get("default_ban_ttl_hours"))
                    elif classification == "auth_invalid":
                        accounts.mark_account(chosen, "auth_invalid")
                    tried.add(chosen)
                last_error = (
                    f"codex died in {viability_check_sec}s "
                    f"(rc={rc}, class={classification}): {stream_text[-500:]}"
                )
                if not auto_rotate or account:
                    return {
                        "error": f"[FAIL] {last_error}",
                        "account_used": chosen,
                        "tried_accounts": sorted(tried),
                    }
                continue

            if chosen:
                accounts.mark_account(chosen, "active")

            actual_sid = ""
            try:
                with stream_path.open(encoding="utf-8", errors="replace") as f:
                    for _ in range(20):
                        line = f.readline()
                        if not line:
                            break
                        sid = extract_session_id_from_jsonl(line)
                        if sid:
                            actual_sid = sid
                            break
            except Exception:
                pass

            job = {
                "job_id": job_id,
                "status": "running",
                "mode": "window",
                "prompt": prompt,
                "session_id": actual_sid or (session_id or ""),
                "stream_file": str(stream_path),
                "last_message_file": str(last_msg_path),
                "codex_pid": proc.pid,
                "cwd": codex_safe_cwd(),
                "account_used": chosen,
                "started_at": now_iso(),
            }
            write_job(job)

            tail_opened, tail_terminal = open_tail_window(stream_path)

            return {
                "job_id": job_id,
                "session_id": actual_sid,
                "stream_file": str(stream_path),
                "last_message_file": str(last_msg_path),
                "window_opened": tail_opened,
                "window_terminal": tail_terminal,
                "account_used": chosen,
                "note": (
                    "Fire-and-forget. Claude does not judge codex liveness. "
                    "Use peek_codex(job_id) for progress, "
                    "wait_for_codex(job_id, timeout_sec) to block until complete. "
                    "Completion is detected via last_message_file appearance."
                ),
            }

    @mcp.tool()
    async def peek_codex(job_id: str, tail_n: int = 20) -> dict:
        """Report fire-and-forget codex progress. No pid checks; only file facts.

        Returns:
            completed: last_message_file exists and is non-empty
            final_message: final codex reply if completed
            stream_static_for_sec: seconds since last write to stream file
            last_event_type / last_item_type / last_item_status / last_item_summary
            tool_calls_count / file_changes_count / agent_messages_count / retry_count
            recent_events: tail of stream events
            errors: accumulated (excluding Reconnecting retry noise)
            status_summary: 'completed' / 'active' / 'silent for Xs' — never says 'died'
        """
        job = load_job(job_id)
        if job is None:
            return {"error": f"[FAIL] no such job '{job_id}'"}

        stream_file = job.get("stream_file")
        last_msg_file = job.get("last_message_file")

        result: dict = {
            "job_id": job_id,
            "session_id": job.get("session_id", ""),
            "account_used": job.get("account_used"),
            "started_at": job.get("started_at"),
            "stream_file": stream_file,
            "last_message_file": last_msg_file,
            "mode": job.get("mode", ""),
        }

        completed = False
        final_message = None
        if last_msg_file and Path(last_msg_file).exists():
            try:
                content = Path(last_msg_file).read_text(encoding="utf-8", errors="replace")
                if content.strip():
                    completed = True
                    final_message = content
            except Exception:
                pass
        result["completed"] = completed
        if final_message:
            result["final_message"] = final_message

        if stream_file and Path(stream_file).exists():
            sp = Path(stream_file)
            try:
                mtime = sp.stat().st_mtime
                result["stream_static_for_sec"] = round(time.time() - mtime, 1)
            except Exception:
                pass

            try:
                activity = parse_codex_activity(sp)
                result["tool_calls_count"] = len(activity["tool_calls"])
                result["file_changes_count"] = len(activity["file_changes"])
                result["agent_messages_count"] = len(activity["agent_messages"])
                result["retry_count"] = activity["retry_count"]
                result["errors"] = activity["errors"][-5:]

                recent: list[dict] = []
                for tc in activity["tool_calls"][-tail_n:]:
                    recent.append(
                        {"kind": "tool", "name": tc["name"], "summary": tc["summary"][:200]}
                    )
                for am in activity["agent_messages"][-3:]:
                    recent.append({"kind": "msg", "summary": am[:300]})
                result["recent_events"] = recent
            except Exception as e:
                result["activity_parse_error"] = f"{type(e).__name__}: {e}"

            result.update(peek_last_jsonl_event(sp))

        silent = result.get("stream_static_for_sec", 0) or 0
        if completed:
            result["status_summary"] = "completed"
        elif silent > 300:
            result["status_summary"] = (
                f"stream silent for {int(silent)}s — codex may be in long reasoning / "
                "stuck on a tool / network blip (no liveness verdict; check viewer window)"
            )
        elif silent > 60:
            result["status_summary"] = f"stream silent for {int(silent)}s"
        else:
            result["status_summary"] = "active"

        return result

    @mcp.tool()
    async def wait_for_codex(
        job_id: str,
        timeout_sec: int = 600,
        poll_interval_sec: float = 1.0,
    ) -> dict:
        """Block until fire-and-forget codex job completes (last_message_file appears).

        On timeout returns the current peek_codex snapshot with timed_out=True.
        No pid checks.
        """
        job = load_job(job_id)
        if job is None:
            return {"error": f"[FAIL] no such job '{job_id}'"}

        last_msg_file = job.get("last_message_file")
        if not last_msg_file:
            return {"error": f"[FAIL] job {job_id} has no last_message_file (not a window-mode job?)"}

        lmp = Path(last_msg_file)
        deadline = time.time() + timeout_sec
        while time.time() < deadline:
            if lmp.exists():
                try:
                    content = lmp.read_text(encoding="utf-8", errors="replace")
                    if content.strip():
                        res = await peek_codex(job_id)
                        res["timed_out"] = False
                        return res
                except Exception:
                    pass
            await asyncio.sleep(poll_interval_sec)

        res = await peek_codex(job_id)
        res["timed_out"] = True
        return res
