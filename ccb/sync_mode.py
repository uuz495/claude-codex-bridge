"""Legacy synchronous + live spawn modes.

Window mode (window_mode.py) is recommended for most use cases. These tools are
retained for advanced workflows:
  - spawn_codex: short fully-blocking call (Claude waits for the result)
  - spawn_codex_live: blocking but with a live viewer window + stream events
  - codex_inject: interrupt an in-flight live session + resume with new prompt
  - list_running_codex: enumerate live processes the bridge currently knows of
"""
from __future__ import annotations

import asyncio
import os
import signal
import tempfile
import uuid
from pathlib import Path

from . import accounts, config
from .classify import classify_codex_result, parse_quota_reset
from .cli import codex_safe_cwd, resolve_cli, resolve_node_cli
from .jobs import RUNNING_CODEX_PROCS
from .paths import LOG_DIR, RUNNING_DIR, STREAM_DIR
from .spawn import (
    codex_pinned_flags,
    extract_session_id_from_jsonl,
    run_subprocess,
    wait_pid_exit,
    with_summary_tail,
)
from .viewer import open_tail_window

DEFAULT_TIMEOUT = 30 * 60
_AUTH_SWAP_LOCK = asyncio.Lock()


async def _spawn_codex_once(
    prompt: str,
    session_id: str | None,
    timeout_sec: int,
    codex_prefix: list[str],
    account: str | None = None,
) -> dict:
    """Sync exec: returns {rc, stdout, stderr, result, session_id}."""
    with tempfile.TemporaryDirectory(prefix="codex_out_") as tmp:
        out_file = Path(tmp) / "last.md"
        out_file.touch()
        flags = [
            *codex_pinned_flags(),
            "--skip-git-repo-check",
            "--dangerously-bypass-approvals-and-sandbox",
            "--output-last-message", str(out_file),
        ]
        if session_id:
            cmd = [*codex_prefix, "exec", "resume", session_id, *flags, prompt]
        else:
            cmd = [*codex_prefix, "exec", "--cd", codex_safe_cwd(), *flags, prompt]
        extra_env: dict = {}
        if account:
            extra_env["CODEX_HOME"] = str(accounts.account_home(account))
        rc, stdout, stderr = await run_subprocess(
            cmd, timeout_sec, f"codex_{uuid.uuid4().hex[:8]}", extra_env=extra_env
        )
        try:
            result_text = out_file.read_text(encoding="utf-8", errors="replace").strip()
        except Exception:
            result_text = ""
        if not result_text and stdout:
            result_text = stdout.strip()
        sid = ""
        try:
            for line in stdout.splitlines():
                sid = extract_session_id_from_jsonl(line) or ""
                if sid:
                    break
        except Exception:
            pass
        return {"rc": rc, "stdout": stdout, "stderr": stderr, "result": result_text, "session_id": sid}


async def _spawn_codex_live_once(
    prompt: str,
    session_id: str | None,
    timeout_sec: int,
    codex_prefix: list[str],
    account: str | None = None,
) -> dict:
    """Live exec: stream JSONL to STREAM_DIR/<sid>.jsonl, open viewer, await completion."""
    with tempfile.TemporaryDirectory(prefix="codex_live_out_") as tmp:
        out_file = Path(tmp) / "last.md"
        out_file.touch()
        flags = [
            *codex_pinned_flags(),
            "--skip-git-repo-check",
            "--dangerously-bypass-approvals-and-sandbox",
            "--json",
            "--output-last-message", str(out_file),
        ]
        if session_id:
            cmd = [*codex_prefix, "exec", "resume", session_id, *flags, prompt]
        else:
            cmd = [*codex_prefix, "exec", "--cd", codex_safe_cwd(), *flags, prompt]

        env = os.environ.copy()
        if account:
            env["CODEX_HOME"] = str(accounts.account_home(account))

        tmp_stream = STREAM_DIR / f"live_{uuid.uuid4().hex[:10]}.jsonl"
        tmp_stream.touch()

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
            )
        except Exception as e:
            return {"rc": 127, "stdout": "", "stderr": f"spawn: {e}", "result": "", "session_id": ""}

        opened_viewer = False
        actual_sid = ""

        async def _drain_stdout():
            nonlocal opened_viewer, actual_sid
            assert proc.stdout is not None
            with tmp_stream.open("ab") as f:
                while True:
                    line = await proc.stdout.readline()
                    if not line:
                        break
                    f.write(line)
                    f.flush()
                    if not actual_sid:
                        try:
                            sid = extract_session_id_from_jsonl(line.decode("utf-8", errors="replace"))
                            if sid:
                                actual_sid = sid
                                RUNNING_CODEX_PROCS[actual_sid] = proc
                                (RUNNING_DIR / f"{actual_sid}.pid").write_text(str(proc.pid))
                                opened_viewer, _ = open_tail_window(tmp_stream)
                        except Exception:
                            pass

        async def _drain_stderr() -> str:
            assert proc.stderr is not None
            chunks: list[bytes] = []
            while True:
                line = await proc.stderr.readline()
                if not line:
                    break
                chunks.append(line)
            return b"".join(chunks).decode("utf-8", errors="replace")

        try:
            stderr_task = asyncio.create_task(_drain_stderr())
            stdout_task = asyncio.create_task(_drain_stdout())
            try:
                await asyncio.wait_for(proc.wait(), timeout=timeout_sec)
            except asyncio.TimeoutError:
                try:
                    proc.kill()
                except Exception:
                    pass
                await proc.wait()
            await stdout_task
            stderr = await stderr_task
        finally:
            if actual_sid:
                RUNNING_CODEX_PROCS.pop(actual_sid, None)
                try:
                    (RUNNING_DIR / f"{actual_sid}.pid").unlink(missing_ok=True)
                except Exception:
                    pass

        try:
            result_text = out_file.read_text(encoding="utf-8", errors="replace").strip()
        except Exception:
            result_text = ""

        return {
            "rc": proc.returncode if proc.returncode is not None else -1,
            "stdout": "",  # stream goes to file
            "stderr": stderr,
            "result": result_text,
            "session_id": actual_sid,
            "stream_file": str(tmp_stream),
        }


async def _rotate_and_spawn(
    prompt: str,
    session_id: str | None,
    timeout_sec: int,
    spawner,
    account: str | None,
    auto_rotate: bool,
) -> dict:
    """Shared rotation loop for spawn_codex / spawn_codex_live."""
    codex_prefix = resolve_node_cli("codex") or (
        [resolve_cli("codex")] if resolve_cli("codex") else None
    )
    if not codex_prefix:
        return {"error": "[FAIL] codex not in PATH. `npm i -g @openai/codex` first."}

    rotation_on = accounts.is_rotation_enabled()
    tried: set[str] = set()

    while True:
        chosen: str | None = None
        if account:
            chosen = account
        elif rotation_on:
            chosen = accounts.pick_next_eligible_account(tried)
            if not chosen:
                return {"error": "[FAIL] all accounts exhausted/banned", "tried_accounts": sorted(tried)}

        async with _AUTH_SWAP_LOCK:
            if chosen:
                ok, err = accounts.activate_account(chosen)
                if not ok:
                    tried.add(chosen)
                    if account or not auto_rotate:
                        return {"error": f"[FAIL] activate {chosen}: {err}", "account_used": chosen}
                    continue
            res = await spawner(prompt, session_id, timeout_sec, codex_prefix, account=chosen)

        combined = (res.get("stderr") or "") + "\n" + (res.get("stdout") or "")
        classification = classify_codex_result(res["rc"], combined, res["result"])

        if chosen:
            if classification == "ok":
                accounts.mark_account(chosen, "active")
            elif classification == "quota_exhausted":
                reset_at = parse_quota_reset(combined + "\n" + (res.get("result") or ""))
                accounts.mark_account(
                    chosen, "quota_exhausted",
                    blocked_until=reset_at,
                    ttl_hours=config.get("default_quota_ttl_hours") if reset_at is None else None,
                )
                tried.add(chosen)
                if auto_rotate and not account:
                    continue
            elif classification == "banned":
                accounts.mark_account(chosen, "banned", ttl_hours=config.get("default_ban_ttl_hours"))
                tried.add(chosen)
                if auto_rotate and not account:
                    continue
            elif classification == "auth_invalid":
                accounts.mark_account(chosen, "auth_invalid")
                tried.add(chosen)
                if auto_rotate and not account:
                    continue

        if res["rc"] != 0:
            return {
                "session_id": res["session_id"],
                "error": f"[FAIL] codex rc={res['rc']} ({classification})\n"
                         f"stderr: {res['stderr'][-1500:]}\noutput: {res['result'][-500:]}",
                "account_used": chosen,
                "tried_accounts": sorted(tried),
            }
        out = {
            "session_id": res["session_id"],
            "output": res["result"] or "[WARN] codex exited OK but produced no output",
        }
        if "stream_file" in res:
            out["stream_file"] = res["stream_file"]
        if chosen:
            out["account_used"] = chosen
        return out


def register(mcp) -> None:
    @mcp.tool()
    async def spawn_codex(
        prompt: str,
        session_id: str | None = None,
        timeout_sec: int = DEFAULT_TIMEOUT,
        account: str | None = None,
        auto_rotate: bool = True,
    ) -> dict:
        """Synchronous Codex spawn (blocks until codex exits)."""
        if not prompt or not prompt.strip():
            return {"error": "[FAIL] empty prompt"}
        return await _rotate_and_spawn(
            with_summary_tail(prompt), session_id, timeout_sec,
            _spawn_codex_once, account, auto_rotate,
        )

    @mcp.tool()
    async def spawn_codex_live(
        prompt: str,
        session_id: str | None = None,
        timeout_sec: int = DEFAULT_TIMEOUT,
        account: str | None = None,
        auto_rotate: bool = True,
    ) -> dict:
        """Synchronous Codex spawn with a live viewer window. Caller still blocks."""
        if not prompt or not prompt.strip():
            return {"error": "[FAIL] empty prompt"}
        return await _rotate_and_spawn(
            with_summary_tail(prompt), session_id, timeout_sec,
            _spawn_codex_live_once, account, auto_rotate,
        )

    @mcp.tool()
    async def codex_inject(session_id: str, prompt: str) -> dict:
        """Interrupt a running live codex session, then resume with a new prompt."""
        if not session_id or not session_id.strip():
            return {"error": "[FAIL] empty session_id"}
        if not prompt or not prompt.strip():
            return {"session_id": session_id, "error": "[FAIL] empty prompt"}

        pid_path = RUNNING_DIR / f"{session_id}.pid"
        if not pid_path.exists():
            return {"session_id": session_id, "error": f"[FAIL] no running codex for {session_id}"}

        try:
            pid = int(pid_path.read_text(encoding="utf-8").strip())
        except Exception as e:
            pid_path.unlink(missing_ok=True)
            return {"session_id": session_id, "error": f"[FAIL] invalid pid file: {e}"}

        proc = RUNNING_CODEX_PROCS.get(session_id)
        try:
            if proc and proc.returncode is None:
                proc.terminate()
                try:
                    await asyncio.wait_for(proc.wait(), timeout=10)
                except asyncio.TimeoutError:
                    proc.kill()
                    await proc.wait()
            else:
                os.kill(pid, signal.SIGTERM)
                exited = await wait_pid_exit(pid, 10)
                if not exited:
                    return {"session_id": session_id, "error": f"[FAIL] pid {pid} did not exit within 10s"}
        except ProcessLookupError:
            pass
        finally:
            pid_path.unlink(missing_ok=True)
            RUNNING_CODEX_PROCS.pop(session_id, None)

        return await spawn_codex_live(prompt, session_id=session_id)

    @mcp.tool()
    async def list_running_codex() -> list[dict]:
        """List currently tracked live codex processes."""
        rows: list[dict] = []
        try:
            for pid_file in sorted(RUNNING_DIR.glob("*.pid"), key=lambda p: p.stat().st_mtime, reverse=True):
                session_id = pid_file.stem
                try:
                    pid = int(pid_file.read_text(encoding="utf-8").strip())
                except Exception:
                    continue
                rows.append({
                    "session_id": session_id,
                    "pid": pid,
                    "stream_file": str(STREAM_DIR / f"{session_id}.jsonl"),
                })
        except Exception as e:
            return [{"error": f"[FAIL] {type(e).__name__}: {e}"}]
        return rows
