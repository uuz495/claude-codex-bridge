# claude-codex-bridge

> Spawn Codex (and Gemini) from Claude Code as real interactive sessions in their own terminal windows.

<sub>[简体中文](README.zh-CN.md)</sub>

- **Sync wrappers** block the MCP call until codex finishes — Claude can't do anything else for minutes to hours.
- **Background + pid-polling wrappers** check codex's recorded pid. On Windows that pid is usually a wrapper process (`wt.exe` → `cmd.exe` → `node.exe` → codex.js) that exits early — so status tools report "codex died" while codex is still working.
- **Window mode** (recommended) opens a new terminal window (wezterm / Windows Terminal / bare console / Unix x-terminal-emulator) and runs `codex --yolo "<prompt>"` directly with a real TTY. What you see IS codex's native TUI — `apply_patch` blocks, inline diffs, command output, reasoning summaries — all rendered by codex itself, not by a re-rendering layer. The bridge tracks completion via codex's own session rollout file at `~/.codex/sessions/YYYY/MM/DD/rollout-<ts>-<sid>.jsonl`. Never queries process pid.

## Quick start

```bash
npm i -g @openai/codex                  # or @google/gemini-cli, or both
git clone https://github.com/uuz495/claude-codex-bridge
```

Register with Claude Code — add to `~/.claude.json` under `mcpServers`:

```json
{
  "mcpServers": {
    "claude-codex-bridge": {
      "command": "python",
      "args": ["/path/to/claude-codex-bridge/run.py"]
    }
  }
}
```

Restart Claude Code. 14 tools appear under the `mcp__claude-codex-bridge__` prefix.

## Example

Ask Claude:

> Use `spawn_codex_window` to have Codex write a binary-search Fibonacci to `fib.py` and run 10 tests.

Claude gets a `job_id` back immediately. A new terminal window opens running codex's native TUI — you see codex's reasoning, tool calls, file diffs, and command output rendered by codex itself. `peek_codex(job_id)` reports activity parsed from codex's session rollout file; `wait_for_codex(job_id)` blocks until codex writes a `task_complete` event. Neither talks to the codex process.

## Tools

14 tools (20 with multi-account rotation enabled).

<details>
<summary><b>Window mode</b> — recommended path, 3 tools</summary>

| Tool | Purpose |
|---|---|
| `spawn_codex_window(prompt, ...)` | Open codex as a native TUI in a new terminal window. Returns `job_id` + `rollout_file` path immediately. |
| `peek_codex(job_id)` | Snapshot of activity parsed from the rollout file (tool calls / file changes / agent messages / completion). Does not infer process liveness. |
| `wait_for_codex(job_id, timeout_sec)` | Block until rollout contains an `event_msg/task_complete` event, or timeout. |

</details>

<details>
<summary><b>Gemini + parallel</b> — 2 tools</summary>

| Tool | Purpose |
|---|---|
| `spawn_gemini(prompt)` | One-shot Gemini CLI call. |
| `spawn_parallel(tasks)` | Run N codex/gemini tasks concurrently. |

</details>

<details>
<summary><b>Other modes</b> — sync / background, 8 tools</summary>

These still use `codex exec --json` and run codex inside the MCP server's process. Simpler for short blocking calls but more limited rendering than window mode.

| Tool | Notes |
|---|---|
| `spawn_codex` | Sync block; returns codex's final output. |
| `spawn_codex_live` | Sync block, plus a live viewer window that re-renders the `--json` event stream. |
| `codex_inject(session_id, prompt)` | Kill a running live session and resume it with a new prompt under the same session id. |
| `list_running_codex` | List live processes the bridge is currently tracking. |
| `spawn_codex_background` | Background asyncio task; returns a `job_id`. Orphaned on MCP restart. |
| `poll_codex_job(job_id)` | Poll background job state. |
| `list_codex_jobs` | List recent jobs (any mode). |
| `cancel_codex_job` | Cancel a background job. Window-mode jobs are detached from the bridge — close the terminal window instead. |

</details>

<details>
<summary><b>Multi-account rotation</b> — optional, off by default, 6 tools</summary>

Set `CCB_ENABLE_ROTATION=1` to expose these.

| Tool | Purpose |
|---|---|
| `save_codex_account(name)` | Register an account from `~/.ai-bridge/accounts/<name>/`. |
| `list_codex_accounts` | View rotation order + per-account state. |
| `get_codex_login_cmd(name)` | Get the `CODEX_HOME=... codex login` command for a specific account. |
| `reset_account_state(name, status)` | Manually flip an account's status. |
| `probe_all_accounts` | Run a trivial codex call per account to detect quota/ban state. |
| `remove_codex_account(name)` | Remove from rotation. |

> Window mode auto-rotation is limited: mid-task account swap isn't feasible once a real TUI is attached to the terminal. Pass an explicit `account` if you need a specific one; otherwise the current `CODEX_HOME` (default account) is used. The sync / background modes still rotate fully.

> Note: using multiple ChatGPT Plus/Pro accounts to extend rate limits may violate OpenAI's Terms of Service. Read your provider's terms before enabling this.

</details>

<details>
<summary><b>Utility</b> — 1 tool</summary>

| Tool | Purpose |
|---|---|
| `list_logs(n)` | Recent subprocess log paths. |

</details>

## Recommended patterns

### Auto-poll with `/loop`

```
/loop 10m peek codex job j-xxxxxxxxxx and tell me what changed since last time
```

Claude wakes every 10 minutes, calls `peek_codex`, reports new tool calls / file changes / completion.

### Block until done

Dispatch + wait in one turn when Claude has follow-up work after codex finishes:

> Spawn codex with this HANDOFF, then `wait_for_codex` with timeout 5400s. When it returns, read the final message and tell me whether the acceptance criteria are met.

Claude blocks until rollout shows `event_msg/task_complete`, then continues with the review.

### Self-verify

Have codex end its final message with a parseable verdict:

> Implement the change. Run `pytest tests/test_X.py`. End the final message with `STATUS: PASS` or `STATUS: FAIL: <reason>`.

`peek_codex(...)["final_message"]` then carries that line directly (extracted from `task_complete.last_agent_message`).

### Chain phases via `session_id`

Pass the previous `session_id` into the next `spawn_codex_window` to inherit reasoning + tool history:

> Spawn codex with prompt P1. After it returns session_id S, spawn another window with `session_id=S` and prompt P2.

## Configuration

Override defaults via env var or `~/.ai-bridge/config.json`. Env wins over file wins over default.

| Setting | Env var | Default | Purpose |
|---|---|---|---|
| Codex model | `CCB_CODEX_MODEL` | `gpt-5.5` | The `-m` flag |
| Reasoning effort | `CCB_REASONING_EFFORT` | `medium` | `-c model_reasoning_effort=...` |
| Reasoning summary | `CCB_REASONING_SUMMARY` | `auto` | `-c model_reasoning_summary=...` — `none` / `auto` / `concise` / `detailed`. The default override ensures the rollout has a visible "what I'm about to do" block even if your `~/.codex/config.toml` pins `summary=none`. |
| Fast mode | `CCB_FAST_MODE` | `1` | `--enable fast_mode` |
| Default timeout | `CCB_DEFAULT_TIMEOUT` | `1800` | Seconds, applies to sync modes |
| Multi-account | `CCB_ENABLE_ROTATION` | `0` | Expose rotation tools |
| Summary tail | `CCB_SUMMARY_TAIL` | `1` | Append "summarize your run" suffix to every prompt |
| Show codex trace | `CCB_SHOW_TRACE` | `0` | Legacy viewer (sync/background) surfaces codex's internal tracing log |
| Quota TTL | `CCB_QUOTA_TTL_HOURS` | `5` | Fallback block window when no "try again in X" parsed |
| Ban TTL | `CCB_BAN_TTL_HOURS` | `24` | Block window for `402 / deactivated_workspace` |
| Non-ASCII cwd remap | `CCB_CWD_REMAPS` | `""` | `src1=dst1,src2=dst2` — workaround for codex's HTTP-header encoding bug |

## How it works (window mode)

```
                     +-- ~/.ai-bridge/jobs/<id>.json
                     |     job metadata (job_id, session_id, rollout path)
                     |
Claude Code  <----+  +-- ~/.codex/sessions/YYYY/MM/DD/rollout-<ts>-<sid>.jsonl
   |               \      codex's own session log
   | MCP            \     (event_msg/task_complete = completion signal,
   v                       patch_apply_end carries unified_diff,
spawn_codex_window         response_item/function_call_output carries
   |                       full captured stdout)
   v
wezterm / wt / new-console:
   node <codex>/bin/codex.js --yolo -m gpt-5.5 -c ... "<prompt>"
   (real PTY, codex's native TUI in the foreground)
```

`peek_codex` parses the rollout file. `wait_for_codex` polls for a `task_complete` event. The bridge has no handle on the codex process — closing the terminal window kills codex, but the bridge never queries codex's pid.

## Caveats / known issues

- **Windows-only at the moment**. Tested only on Windows 11. Linux/macOS code branches exist (`subprocess.Popen` defaults, x-terminal-emulator / gnome-terminal / xterm / alacritty / kitty launching) but have never been exercised end-to-end. Treat Unix support as unverified.
- **Codex TUI doesn't auto-exit on task complete**. After the task finishes the TUI sits at the next-input prompt waiting for follow-up — `wait_for_codex` returns as soon as `task_complete` appears in the rollout, but the window stays open. Close it manually (Ctrl+C / close window).
- **`cancel_codex_job` is unreliable for stuck jobs** in the legacy sync/background paths. Window-mode jobs are detached and not cancellable through the bridge at all — close the terminal window or kill the PID externally.
- **Non-ASCII cwd**. Codex CLI puts the working directory into HTTP headers; non-ASCII bytes trigger an upstream retry loop. Use `CCB_CWD_REMAPS` to map the path to an ASCII junction (Windows: `mklink /J C:\ascii-alias D:\real-path`).
- **Window mode needs a terminal emulator on PATH**. Detection order: wezterm > Windows Terminal (`wt`) > bare new console (Windows) / `x-terminal-emulator` / `gnome-terminal` / `xterm` / `alacritty` / `kitty` / `wezterm` (Unix). wezterm is preferred for UTF-8 / ANSI handling.
- **Sync/background modes still use `codex exec --json`** and inherit its quirks (no inline diffs in the stream, a real codex 0.130 bug where parallel `command_execution` events can lose their `item.completed` signal). If you hit those issues, switch the workflow to window mode.

## Development

```bash
git clone https://github.com/uuz495/claude-codex-bridge
cd claude-codex-bridge
pip install -e .[dev]
ruff check .
```

## License

MIT — see [LICENSE](LICENSE).
