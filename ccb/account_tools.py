"""MCP tools for multi-account management.

Only registered when CCB_ENABLE_ROTATION=1 (env) or enable_multi_account=true
(config.json). When rotation is disabled, codex spawns use the system-default
auth from ~/.codex/auth.json with no rotation logic.
"""
from __future__ import annotations

import asyncio
import os
import shutil
from pathlib import Path

from . import accounts
from .cli import resolve_cli, resolve_node_cli
from .paths import ACCOUNTS_DIR, CODEX_AUTH_PATH, SHARED_SESSIONS_DIR  # noqa: F401
from .spawn import run_subprocess


def register(mcp) -> None:
    @mcp.tool()
    async def save_codex_account(name: str, overwrite: bool = False) -> dict:
        """Register a Codex account for rotation.

        Recommended (B-method): pre-login per-account so refresh tokens never
        leave the account home directory:
            PowerShell: $env:CODEX_HOME = "<HOME>\\.ai-bridge\\accounts\\<name>"; codex login
            Bash:       CODEX_HOME=~/.ai-bridge/accounts/<name> codex login
        Then call this tool to register + set up the sessions junction.

        Legacy: if accounts/<name>/auth.json doesn't exist but ~/.codex/auth.json
        does, the latter is copied (one-shot migration).
        """
        if not accounts.valid_account_name(name):
            return {"error": f"[FAIL] invalid account name '{name}' (allow A-Za-z0-9_.-)"}

        home = accounts.account_home(name)
        target_auth = home / "auth.json"
        home.mkdir(parents=True, exist_ok=True)

        auth_source = "missing"
        if target_auth.exists() and not overwrite:
            auth_source = "existing"
        elif CODEX_AUTH_PATH.exists():
            try:
                shutil.copy2(CODEX_AUTH_PATH, target_auth)
                auth_source = "copied_from_global"
            except Exception as e:
                return {"error": f"[FAIL] copy legacy auth failed: {type(e).__name__}: {e}"}
        else:
            return {
                "error": (
                    f"[FAIL] no auth found for '{name}'. Run first:\n"
                    f"  PowerShell: $env:CODEX_HOME = \"{home}\"; codex login\n"
                    f"  Bash:       CODEX_HOME='{home}' codex login\n"
                    f"then call save_codex_account again."
                )
            }

        ok, err = accounts.ensure_account_home(name)
        if not ok:
            return {"error": f"[FAIL] sessions junction: {err}", "auth_source": auth_source}

        data = accounts.load_accounts()
        if name not in data["rotation"]:
            data["rotation"].append(name)
        data["states"].setdefault(name, {})["status"] = "active"
        data["states"][name]["updated_at"] = accounts.now_iso() if hasattr(accounts, "now_iso") else ""
        data["states"][name].pop("blocked_until", None)
        if data.get("current") is None:
            data["current"] = name
        accounts.save_accounts(data)

        return {
            "account": name,
            "saved": True,
            "auth_source": auth_source,
            "rotation": data["rotation"],
            "current": data["current"],
            "home": str(home),
        }

    @mcp.tool()
    async def get_codex_login_cmd(name: str) -> dict:
        """Return shell commands to log in to a per-account CODEX_HOME."""
        if not accounts.valid_account_name(name):
            return {"error": f"[FAIL] invalid name '{name}'"}
        home = accounts.account_home(name)
        return {
            "account": name,
            "powershell": f'$env:CODEX_HOME = "{home}"; codex login',
            "bash": f"CODEX_HOME='{home}' codex login",
            "home": str(home),
        }

    @mcp.tool()
    async def list_codex_accounts() -> dict:
        """Return rotation order + per-account state."""
        return accounts.load_accounts()

    @mcp.tool()
    async def reset_account_state(name: str, status: str = "active") -> dict:
        """Manually set an account's status. Useful after fixing a deactivated workspace."""
        if not accounts.valid_account_name(name):
            return {"error": f"[FAIL] invalid name '{name}'"}
        if status not in ("active", "quota_exhausted", "banned", "auth_invalid", "dead"):
            return {"error": f"[FAIL] unknown status '{status}'"}
        accounts.mark_account(name, status)
        return {"account": name, "status": status}

    @mcp.tool()
    async def probe_all_accounts(timeout_sec: int = 45) -> dict:
        """Run a trivial `codex exec` per account to detect quota/ban/auth state."""
        codex_prefix = resolve_node_cli("codex") or (
            [resolve_cli("codex")] if resolve_cli("codex") else None
        )
        if not codex_prefix:
            return {"error": "[FAIL] codex not in PATH"}

        data = accounts.load_accounts()
        results: dict[str, dict] = {}

        async def _probe(name: str) -> tuple[str, dict]:
            ok, err = accounts.activate_account(name)
            if not ok:
                return name, {"activate_failed": err}
            env = {"CODEX_HOME": str(accounts.account_home(name))}
            cmd = [*codex_prefix, "exec", "--skip-git-repo-check",
                   "--dangerously-bypass-approvals-and-sandbox",
                   "Reply with just OK."]
            rc, stdout, stderr = await run_subprocess(
                cmd, timeout_sec, f"probe_{name}", extra_env=env
            )
            return name, {"rc": rc, "stdout_tail": stdout[-400:], "stderr_tail": stderr[-400:]}

        rotation = data.get("rotation") or []
        outs = await asyncio.gather(*[_probe(n) for n in rotation])
        for name, info in outs:
            results[name] = info
        return results

    @mcp.tool()
    async def remove_codex_account(name: str, delete_files: bool = False) -> dict:
        """Remove an account from rotation. Optionally also wipe accounts/<name>/."""
        if not accounts.valid_account_name(name):
            return {"error": f"[FAIL] invalid name '{name}'"}
        data = accounts.load_accounts()
        if name in data["rotation"]:
            data["rotation"].remove(name)
        data["states"].pop(name, None)
        if data.get("current") == name:
            data["current"] = data["rotation"][0] if data["rotation"] else None
        accounts.save_accounts(data)

        if delete_files:
            home = accounts.account_home(name)
            if home.exists():
                try:
                    shutil.rmtree(home, ignore_errors=True)
                except Exception as e:
                    return {"removed": True, "files_deleted": False, "error": str(e)}
        return {
            "removed": True,
            "rotation": data["rotation"],
            "current": data["current"],
            "files_deleted": delete_files,
        }
