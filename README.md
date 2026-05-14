# claude-codex-bridge

> Run Codex and Gemini from Claude Code — in their own terminal windows, without pid-based liveness guesses.

<sub>[简体中文](README.zh-CN.md)</sub>

- **Sync wrappers** block the MCP call until codex finishes — Claude can't do anything else for minutes to hours.
- **Background + pid-polling wrappers** check codex's recorded pid. On Windows that pid is usually a wrapper process (`wt.exe` → `cmd.exe` → `node.exe` → codex.js) that exits early — so status tools report "codex died" while codex is still working.
- **Window mode** here detaches codex from the MCP server's lifecycle (`DETACHED_PROCESS`), opens a separate terminal window streaming codex's events, and detects completion from a file codex writes itself — never from a pid. Codex outlives the MCP server, and the bridge does not make liveness guesses.

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

Claude gets a `job_id` back immediately. A wezterm window opens streaming codex's reasoning, tool calls, and file edits. `peek_codex(job_id)` reports stream state any time; `wait_for_codex(job_id)` blocks until codex writes its final-message file. Neither talks to the codex process.

## Tools

14 tools (20 with multi-account rotation enabled).

<details>
<summary><b>Window mode</b> — recommended path, 3 tools</summary>

| Tool | Purpose |
|---|---|
| `spawn_codex_window(prompt, ...)` | Spawn codex detached, open a viewer window. Returns `job_id` immediately. |
| `peek_codex(job_id)` | Snapshot of stream state. Does not infer process liveness. |
| `wait_for_codex(job_id, timeout_sec)` | Block until `last_message_file` is written, or timeout. |

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

Codex runs inside the MCP server's process. Simpler for short blocking calls; lost if the MCP server restarts.

| Tool | Notes |
|---|---|
| `spawn_codex` | Sync block; returns codex's final output. |
| `spawn_codex_live` | Sync block, plus a live viewer window. |
| `codex_inject(session_id, prompt)` | Kill a running live session and resume it with a new prompt under the same session id. |
| `list_running_codex` | List live processes the bridge is currently tracking. |
| `spawn_codex_background` | Background asyncio task; returns a `job_id`. Orphaned on MCP restart. |
| `poll_codex_job(job_id)` | Poll background job state. |
| `list_codex_jobs` | List recent jobs (any mode). |
| `cancel_codex_job` | Cancel a background job. Window-mode jobs cannot be cancelled this way; see Caveats. |

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

Claude blocks until `last_message_file` appears, then continues with the review.

### Self-verify

Have codex end its final message with a parseable verdict:

> Implement the change. Run `pytest tests/test_X.py`. End the final message with `STATUS: PASS` or `STATUS: FAIL: <reason>`.

`peek_codex(...)["final_message"]` then carries that line directly.

### Chain phases via `session_id`

Pass the previous `session_id` into the next `spawn_codex_window` to inherit reasoning + tool history:

> Spawn codex with prompt P1. After it returns session_id S, spawn another window with `session_id=S` and prompt P2.

## Configuration

Override defaults via env var or `~/.ai-bridge/config.json`. Env wins over file wins over default.

| Setting | Env var | Default | Purpose |
|---|---|---|---|
| Codex model | `CCB_CODEX_MODEL` | `gpt-5` | The `-m` flag |
| Reasoning effort | `CCB_REASONING_EFFORT` | `medium` | `-c model_reasoning_effort=...` |
| Fast mode | `CCB_FAST_MODE` | `0` | `--enable fast_mode` |
| Default timeout | `CCB_DEFAULT_TIMEOUT` | `1800` | Seconds, applies to sync modes |
| Multi-account | `CCB_ENABLE_ROTATION` | `0` | Expose rotation tools |
| Summary tail | `CCB_SUMMARY_TAIL` | `1` | Append "summarize your run" suffix to every prompt |
| Show codex trace | `CCB_SHOW_TRACE` | `0` | Viewer surfaces codex's internal tracing log |
| Quota TTL | `CCB_QUOTA_TTL_HOURS` | `5` | Fallback block window when no "try again in X" parsed |
| Ban TTL | `CCB_BAN_TTL_HOURS` | `24` | Block window for `402 / deactivated_workspace` |
| Non-ASCII cwd remap | `CCB_CWD_REMAPS` | `""` | `src1=dst1,src2=dst2` — workaround for codex's HTTP-header encoding bug |

## How it works

```
                     +-- ~/.ai-bridge/jobs/<id>.json
                     |     job metadata
                     |
Claude Code  <----+  +-- ~/.ai-bridge/streams/<id>.jsonl
   |               \      JSONL event log (codex --json)
   | MCP            \
   v                 +-- ~/.ai-bridge/jobs/<id>.last.md
spawn_codex_window         final assistant message (completion signal)
   |
   v
codex.js exec --json --output-last-message ...
   (DETACHED_PROCESS, no parent handle retained)
   |
   v
wezterm new-window:  python tail_viewer.py <stream>
   (renders JSONL → ANSI-colored ASCII art for human eyes)
```

`peek_codex` reads the stream file's mtime + tail events. `wait_for_codex` polls for `last.md` to appear. Neither talks to the codex process.

## Caveats / known issues

- **Windows-only at the moment**. Tested only on Windows 11. Linux/macOS code branches exist (`subprocess.Popen` defaults, `mklink`→symlink, xterm/gnome-terminal/alacritty/kitty launching) but have never been exercised end-to-end. Treat Unix support as unverified.
- **`cancel_codex_job` is unreliable for stuck jobs**. The cancel path waits on the stream monitor, which can itself hang. Window-mode jobs are detached and cannot be cancelled through this tool — close the viewer or kill the PID externally.
- **Non-ASCII cwd**. Codex CLI puts the working directory into HTTP headers; non-ASCII bytes trigger an upstream retry loop. Use `CCB_CWD_REMAPS` to map the path to an ASCII junction (Windows: `mklink /J C:\ascii-alias D:\real-path`).
- **Stream is JSONL plus stderr**. Codex's internal `tracing` log (timestamps, retries, Wall-time summaries) is interleaved with the JSONL event stream. The viewer suppresses these lines by default; set `CCB_SHOW_TRACE=1` to surface them.
- **Window mode needs a terminal emulator**. On Windows: wezterm, Windows Terminal (`wt`), or a bare new console as last fallback. Wezterm is preferred for UTF-8 / ANSI handling.

## Development

```bash
git clone https://github.com/uuz495/claude-codex-bridge
cd claude-codex-bridge
pip install -e .[dev]
ruff check .
```

## License

MIT — see [LICENSE](LICENSE).
