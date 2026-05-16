"""Runtime config: env vars + ~/.ai-bridge/config.json + defaults.

Precedence: env var > config.json > built-in default.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from .paths import CONFIG_FILE

_DEFAULTS: dict[str, Any] = {
    "codex_model": "gpt-5",
    "codex_reasoning_effort": "medium",
    "codex_reasoning_summary": "auto",
    "codex_fast_mode": False,
    "default_timeout_sec": 1800,
    "enable_multi_account": False,
    "summary_tail_enabled": True,
    "cwd_remaps": [],
    "default_quota_ttl_hours": 5,
    "default_ban_ttl_hours": 24,
    "show_codex_trace": False,
}

_ENV_MAP = {
    "codex_model": "CCB_CODEX_MODEL",
    "codex_reasoning_effort": "CCB_REASONING_EFFORT",
    "codex_reasoning_summary": "CCB_REASONING_SUMMARY",
    "codex_fast_mode": "CCB_FAST_MODE",
    "default_timeout_sec": "CCB_DEFAULT_TIMEOUT",
    "enable_multi_account": "CCB_ENABLE_ROTATION",
    "summary_tail_enabled": "CCB_SUMMARY_TAIL",
    "default_quota_ttl_hours": "CCB_QUOTA_TTL_HOURS",
    "default_ban_ttl_hours": "CCB_BAN_TTL_HOURS",
    "show_codex_trace": "CCB_SHOW_TRACE",
}


def _coerce(default: Any, raw: str) -> Any:
    if isinstance(default, bool):
        return raw.strip().lower() in ("1", "true", "yes", "on")
    if isinstance(default, int):
        try:
            return int(raw)
        except ValueError:
            return default
    if isinstance(default, list):
        return [s.strip() for s in raw.split(",") if s.strip()]
    return raw


def _load_file_cfg() -> dict[str, Any]:
    if not CONFIG_FILE.exists():
        return {}
    try:
        return json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


_FILE_CFG = _load_file_cfg()


def get(key: str) -> Any:
    """Resolve a config value: env > config.json > default."""
    default = _DEFAULTS[key]
    env_name = _ENV_MAP.get(key)
    if env_name:
        raw = os.environ.get(env_name)
        if raw is not None:
            return _coerce(default, raw)
    if key in _FILE_CFG:
        return _FILE_CFG[key]
    return default


def cwd_remaps() -> list[tuple[str, str]]:
    """List of (src_prefix, dst_prefix) for codex CWD ASCII-junction remapping.

    Codex CLI passes cwd in HTTP headers; non-ASCII characters trigger upstream
    retry-5-times-then-give-up. Users with non-ASCII paths can map them to a
    pre-created ASCII junction (e.g. mklink /J C:\\plywork D:\\my-work).

    Env: CCB_CWD_REMAPS="C:\\src1=C:\\dst1,C:\\src2=C:\\dst2"
    """
    env = os.environ.get("CCB_CWD_REMAPS", "")
    pairs: list[tuple[str, str]] = []
    if env:
        for token in env.split(","):
            if "=" in token:
                s, d = token.split("=", 1)
                s, d = s.strip(), d.strip()
                if s and d:
                    pairs.append((s, d))
    file_pairs = _FILE_CFG.get("cwd_remaps") or []
    for entry in file_pairs:
        if isinstance(entry, list) and len(entry) == 2:
            pairs.append((entry[0], entry[1]))
        elif isinstance(entry, dict) and "src" in entry and "dst" in entry:
            pairs.append((entry["src"], entry["dst"]))
    return pairs
