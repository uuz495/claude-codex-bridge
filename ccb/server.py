"""claude-codex-bridge MCP server entry point.

Usage:
    python -m ccb.server
or via the installed entry point:
    claude-codex-bridge

Tools are registered conditionally based on config (e.g. multi-account
management only appears if CCB_ENABLE_ROTATION=1).
"""
from __future__ import annotations

import sys

from mcp.server.fastmcp import FastMCP

from . import (
    account_tools,
    accounts,
    background_mode,
    gemini,
    sync_mode,
    util_tools,
    window_mode,
)


def build_server() -> FastMCP:
    mcp = FastMCP("claude-codex-bridge")

    # Always-on tools
    window_mode.register(mcp)
    sync_mode.register(mcp)
    background_mode.register(mcp)
    gemini.register(mcp)
    util_tools.register(mcp)

    # Multi-account tools: gated by config
    if accounts.is_rotation_enabled():
        account_tools.register(mcp)
        print(
            "[ccb] multi-account rotation: ENABLED "
            "(disable: unset CCB_ENABLE_ROTATION or set enable_multi_account=false)",
            file=sys.stderr,
        )
    else:
        print(
            "[ccb] multi-account rotation: disabled (default). "
            "Enable: CCB_ENABLE_ROTATION=1",
            file=sys.stderr,
        )

    return mcp


def main() -> None:
    mcp = build_server()
    mcp.run()


if __name__ == "__main__":
    main()
