# claude-codex-bridge

> Spawn Codex (and Gemini) from Claude Code as real interactive sessions in their own terminal windows.

<sub>[简体中文](README.zh-CN.md)</sub>

- One tool to spawn (`spawn_codex`), with two flags (`wait` / `with_window`) for mode selection — no juggling four near-identical entry points.
- Default mode opens a new terminal tab (Windows Terminal / wezterm / Unix x-terminal-emulator) and runs `codex --yolo "<prompt>"` directly with a real TTY. What you see IS codex's native TUI — `apply_patch` blocks, inline diffs, command output, reasoning summaries — all rendered by codex itself, not by a re-rendering layer.
- Parallel spawns share one terminal window via tabs (`wt new-tab` is preferred on Windows because it joins the most-recent window without an IPC handshake; wezterm is fallback).
- The bridge tracks completion via codex's own session rollout file at `~/.codex/sessions/YYYY/MM/DD/rollout-<ts>-<sid>.jsonl`. Never queries process pid; codex outlives the MCP server.

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

Restart Claude Code. 8 tools appear under the `mcp__claude-codex-bridge__` prefix (9 with multi-account rotation enabled).

## Example

Ask Claude:

> Use `spawn_codex` to have Codex write a binary-search Fibonacci to `fib.py` and run 10 tests.

Claude gets a `job_id` back immediately. A new terminal tab opens running codex's native TUI — you see codex's reasoning, tool calls, file diffs, and command output rendered by codex itself. `peek_codex(job_id)` reports activity parsed from the rollout file; `wait_for_codex(job_id)` blocks until codex emits `task_complete`. Neither talks to the codex process.

## Tools

8 tools (9 with multi-account rotation). The surface is intentionally small — one spawn entry point with flags, plus thin orthogonal helpers.

| Tool | Purpose |
|---|---|
| `spawn_codex(prompt, wait=False, with_window=True, session_id, account, timeout_sec)` | Run codex with a prompt. **Default** (`wait=False, with_window=True`): native TUI in new terminal tab, returns `job_id` + `rollout_file` immediately. `wait=True, with_window=False`: legacy `codex exec --json` blocking call, returns final message text. `wait=True, with_window=True`: opens window AND blocks until `task_complete`. `wait=False, with_window=False`: rejected. |
| `peek_codex(job_id)` | Snapshot of activity parsed from the rollout (or legacy --json) file: tool calls / file changes / agent messages / completion + final_message. Does not infer process liveness. |
| `wait_for_codex(job_id, timeout_sec)` | Block until rollout contains an `event_msg/task_complete` event, or timeout. |
| `cancel_codex_job(job_id)` | Best-effort cancel. Native TUI jobs are detached from the bridge — only the on-disk job status is flipped to `cancelled`; close the terminal window to actually stop codex. |
| `list_codex_jobs(limit=20)` | List recent jobs newest-first. |
| `spawn_gemini(prompt)` | One-shot Gemini CLI call. Returns stdout. |
| `spawn_parallel(tasks)` | Fan out N codex/gemini tasks concurrently. Codex tasks default to `wait=True, with_window=False` so the caller gets final messages back without opening N terminal tabs. |
| `list_logs(n)` | Recent subprocess log paths. |

<details>
<summary><b>Multi-account rotation</b> — optional, off by default, 1 tool</summary>

Set `CCB_ENABLE_ROTATION=1` to expose `manage_codex_accounts(action, ...)` with these actions:

| Action | Purpose |
|---|---|
| `list` | Return rotation order + per-account state. |
| `get_login_cmd(name)` | Return the `CODEX_HOME=... codex login` shell commands for `name`. |
| `add(name, overwrite=False)` | Register `name` after you've pre-logged-in to its `CODEX_HOME`. |
| `reset(name, status)` | Set `name`'s status (`active` / `quota_exhausted` / `banned` / `auth_invalid` / `dead`). |
| `probe(timeout_sec=45)` | Run a trivial `codex exec` per account to detect quota / ban / auth state. |
| `remove(name, delete_files=False)` | Drop `name` from rotation, optionally also wipe `accounts/<name>/`. |

> Window mode auto-rotation is limited: mid-task account swap isn't feasible once a real TUI is attached to the terminal. Pass an explicit `account` to `spawn_codex` for a specific one; otherwise the current `CODEX_HOME` (default account) is used. Legacy sync mode (`wait=True, with_window=False`) still rotates fully.

> Note: using multiple ChatGPT Plus/Pro accounts to extend rate limits may violate OpenAI's Terms of Service. Read your provider's terms before enabling this.

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

Pass the previous `session_id` into the next `spawn_codex` to inherit reasoning + tool history:

> Spawn codex with prompt P1. After it returns session_id S, spawn another window with `session_id=S` and prompt P2.

## Configuration

Override defaults via env var or `~/.ai-bridge/config.json`. Env wins over file wins over default.

| Setting | Env var | Default | Purpose |
|---|---|---|---|
| Codex model | `CCB_CODEX_MODEL` | `gpt-5.5` | The `-m` flag |
| Reasoning effort | `CCB_REASONING_EFFORT` | `high` | `-c model_reasoning_effort=...` |
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
spawn_codex                response_item/function_call_output carries
(with_window=True)         full captured stdout)
   |
   v
wt new-tab / wezterm tab / new-console:
   node <codex>/bin/codex.js --yolo -m gpt-5.5 -c ... "<prompt>"
   (real PTY, codex's native TUI in the foreground)
```

`peek_codex` parses the rollout file. `wait_for_codex` polls for a `task_complete` event. The bridge has no handle on the codex process — closing the terminal window kills codex, but the bridge never queries codex's pid.

## Caveats / known issues

- **Windows-only at the moment**. Tested only on Windows 11. Linux/macOS code branches exist (`subprocess.Popen` defaults, x-terminal-emulator / gnome-terminal / xterm / alacritty / kitty launching) but have never been exercised end-to-end. Treat Unix support as unverified.
- **Codex TUI doesn't auto-exit on task complete**. After the task finishes the TUI sits at the next-input prompt waiting for follow-up — `wait_for_codex` returns as soon as `task_complete` appears in the rollout, but the window stays open. Close it manually (Ctrl+C / close window).
- **`cancel_codex_job` only flips metadata for window-mode jobs**. The codex process is detached and the bridge has no handle — close the terminal window to actually stop codex.
- **Non-ASCII cwd**. Codex CLI puts the working directory into HTTP headers; non-ASCII bytes trigger an upstream retry loop. Use `CCB_CWD_REMAPS` to map the path to an ASCII junction (Windows: `mklink /J C:\ascii-alias D:\real-path`).
- **Needs a terminal emulator on PATH** (when `with_window=True`). Detection order on Windows: **Windows Terminal (`wt`)** → wezterm → bare new console. `wt` is preferred because its `new-tab` reliably attaches to the most-recent window without IPC, so parallel spawns share one window cleanly. wezterm's tab attach requires `wezterm-mux-server` and a registered GUI; standalone wezterm GUIs (the common case) end up spawning new windows. Unix detection: `x-terminal-emulator` / `gnome-terminal` / `xterm` / `alacritty` / `kitty` / `wezterm`.
- **Legacy sync mode (`wait=True, with_window=False`) still uses `codex exec --json`** and inherits its quirks (no inline diffs, a real codex 0.130 bug where parallel `command_execution` events can lose their `item.completed` signal). Prefer the default window mode for non-trivial work.

## Development

```bash
git clone https://github.com/uuz495/claude-codex-bridge
cd claude-codex-bridge
pip install -e .[dev]
ruff check .
```

## License

MIT — see [LICENSE](LICENSE).
