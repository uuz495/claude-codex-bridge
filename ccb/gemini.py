"""Gemini CLI integration + cross-provider parallel spawn."""
from __future__ import annotations

import asyncio
import uuid

from .cli import resolve_cli, resolve_node_cli
from .spawn import run_subprocess

DEFAULT_TIMEOUT = 30 * 60


def register(mcp) -> None:
    @mcp.tool()
    async def spawn_gemini(prompt: str, timeout_sec: int = DEFAULT_TIMEOUT) -> str:
        """Spawn a Gemini CLI one-shot prompt (-p mode, --yolo), return stdout."""
        if not prompt or not prompt.strip():
            return "[FAIL] empty prompt"

        gemini_prefix = resolve_node_cli("gemini") or (
            [resolve_cli("gemini")] if resolve_cli("gemini") else None
        )
        if not gemini_prefix:
            return "[FAIL] gemini not in PATH. `npm i -g @google/gemini-cli` first."

        cmd = [*gemini_prefix, "-p", prompt, "--yolo"]
        rc, stdout, stderr = await run_subprocess(cmd, timeout_sec, f"gemini_{uuid.uuid4().hex[:8]}")

        if rc != 0:
            return f"[FAIL] gemini rc={rc}\nstderr: {stderr[-2000:]}\nstdout: {stdout[-2000:]}"
        return stdout.strip() or "[WARN] gemini exited OK but produced no output"

    @mcp.tool()
    async def spawn_parallel(tasks: list[dict]) -> list[dict]:
        """Fan out N codex/gemini tasks concurrently.

        tasks: [{ai: "codex"|"gemini", prompt: "...", session_id?: "...",
                 account?: "...", timeout_sec?: 1800, auto_rotate?: True}]
        Returns: [{index, ai, output|error, ...}]
        """
        if not isinstance(tasks, list) or not tasks:
            return [{"index": 0, "error": "[FAIL] tasks must be a non-empty list"}]

        # Late import to avoid circular: spawn_codex is registered on the same mcp.
        from . import sync_mode  # noqa: F401  — ensure module loaded

        async def _run(i: int, task: dict) -> dict:
            ai = task.get("ai", "").lower()
            prompt = task.get("prompt", "")
            timeout_sec = task.get("timeout_sec", DEFAULT_TIMEOUT)
            session_id = task.get("session_id")
            account = task.get("account")
            auto_rotate = task.get("auto_rotate", True)
            if ai == "codex":
                from .sync_mode import _rotate_and_spawn, _spawn_codex_once
                from .spawn import with_summary_tail
                res = await _rotate_and_spawn(
                    with_summary_tail(prompt), session_id, timeout_sec,
                    _spawn_codex_once, account, auto_rotate,
                )
                return {"index": i, "ai": ai, **res}
            elif ai == "gemini":
                out = await spawn_gemini(prompt, timeout_sec)
                key = "error" if isinstance(out, str) and out.startswith("[FAIL]") else "output"
                return {"index": i, "ai": ai, key: out}
            return {"index": i, "error": f"[FAIL] unknown ai: {ai!r}. use 'codex' or 'gemini'"}

        return await asyncio.gather(*[_run(i, t) for i, t in enumerate(tasks)])
