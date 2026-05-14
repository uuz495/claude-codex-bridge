# claude-codex-bridge

An MCP server that lets [Claude Code](https://github.com/anthropics/claude-code) dispatch tasks to [OpenAI Codex](https://github.com/openai/codex) and [Google Gemini](https://github.com/google-gemini/gemini-cli) CLIs. Window mode runs the codex process detached from the MCP server's lifecycle, so the bridge does not make pid-based liveness guesses about whether codex is still working.

Status: Phase 1 — refactor + open-source skeleton. Tests, CI, examples, and screenshots are planned for Phase 2.

---

## Why

Two patterns are common in MCP wrappers around long-running CLIs like codex:

1. **Synchronous block**. The wrapper awaits codex's exit before returning. The caller (e.g. Claude Code) blocks for the full duration of the task and cannot do anything else. If codex hangs there is no progress signal.
2. **Background + pid polling**. The wrapper spawns codex in the background and a status tool polls the recorded pid with `pid_exists()`. On Windows the pid recorded is often a wrapper process (`wt.exe` → `cmd.exe` → `node.exe` → codex.js); the outer wrapper can exit while codex is still working. The status tool then reports codex as dead even though it is not.

This server uses a third path, called *window mode*:

- Codex runs as a Windows `DETACHED_PROCESS` child. The MCP server drops the process handle after spawning and does not supervise it.
- A separate terminal window (wezterm or Windows Terminal) opens running a tail viewer that renders codex's JSONL event stream — ANSI colors with ASCII box-drawing (no Unicode dependency).
- The MCP server does not call `pid_exists()` or any equivalent. Completion is signalled by codex writing its `--output-last-message` file.
- `peek_codex(job_id)` returns what the stream file shows: seconds since last write, last event type, tool-call count, retry count, accumulated errors. It does not report "codex is alive" or "codex died" — those judgements are left to the user looking at the viewer window.

---

## Quick start

```bash
# Install Codex / Gemini CLIs (pick whichever you want to bridge)
npm i -g @openai/codex
npm i -g @google/gemini-cli

# Install this MCP server
pip install claude-codex-bridge

# Register with Claude Code (add to ~/.claude.json mcpServers)
{
  "mcpServers": {
    "claude-codex-bridge": {
      "command": "claude-codex-bridge"
    }
  }
}
```

Or, if you'd rather not `pip install`, point at the bundled wrapper:

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

Restart Claude Code, then ask Claude to dispatch a task:

> Use `spawn_codex_window` to have Codex write a tail-recursive Fibonacci in `fib.py` and run 5 test cases.

A new terminal window opens; codex runs there. Claude returns the `job_id` immediately. Ask Claude to peek the job whenever you want progress.

---

## Tools

### Window mode

| Tool | Purpose |
|---|---|
| `spawn_codex_window(prompt, ...)` | Spawn codex detached and open a viewer window. Returns `job_id` immediately. |
| `peek_codex(job_id)` | Snapshot of stream state. Does not infer process liveness. |
| `wait_for_codex(job_id, timeout_sec)` | Block until `last_message_file` is written, or timeout. |

### Gemini + parallel

| Tool | Purpose |
|---|---|
| `spawn_gemini(prompt)` | One-shot Gemini CLI call. |
| `spawn_parallel(tasks)` | Run N codex/gemini tasks concurrently. |

### Other modes

These hold the codex process inside the MCP server's lifecycle. They are kept because they are simpler for short blocking calls and for interactive interruption of an in-flight session. If the MCP server restarts while one of these is running, the job is lost.

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

### Multi-account rotation (optional, gated)

Set `CCB_ENABLE_ROTATION=1` to expose these. Disabled by default.

| Tool | Purpose |
|---|---|
| `save_codex_account(name)` | Register an account from `~/.ai-bridge/accounts/<name>/`. |
| `list_codex_accounts` | View rotation order + per-account state. |
| `get_codex_login_cmd(name)` | Get the `CODEX_HOME=... codex login` command for a specific account. |
| `reset_account_state(name, status)` | Manually flip an account's status. |
| `probe_all_accounts` | Run a trivial codex call per account to detect quota/ban state. |
| `remove_codex_account(name)` | Remove from rotation. |

> Note: using multiple ChatGPT Plus/Pro accounts to extend rate limits may violate OpenAI's Terms of Service. Read your provider's terms before enabling this.

### Utility

| Tool | Purpose |
|---|---|
| `list_logs(n)` | Recent subprocess log paths. |

---

## Configuration

All settings have built-in defaults. Override via env var or `~/.ai-bridge/config.json`. Env wins over file wins over default.

| Setting | Env var | Default | Purpose |
|---|---|---|---|
| Codex model | `CCB_CODEX_MODEL` | `gpt-5` | The `-m` flag |
| Reasoning effort | `CCB_REASONING_EFFORT` | `medium` | `-c model_reasoning_effort=...` |
| Fast mode | `CCB_FAST_MODE` | `0` | `--enable fast_mode` |
| Default timeout | `CCB_DEFAULT_TIMEOUT` | `1800` | Seconds, applies to sync modes |
| Multi-account | `CCB_ENABLE_ROTATION` | `0` | Expose rotation tools |
| Summary tail | `CCB_SUMMARY_TAIL` | `1` | Append a "summarize your run" suffix to every prompt |
| Show codex trace | `CCB_SHOW_TRACE` | `0` | Viewer surfaces codex's internal tracing log noise |
| Quota TTL | `CCB_QUOTA_TTL_HOURS` | `5` | Fallback block window when no "try again in X" parsed |
| Ban TTL | `CCB_BAN_TTL_HOURS` | `24` | Block window for `402 / deactivated_workspace` |
| Non-ASCII cwd remap | `CCB_CWD_REMAPS` | `""` | `src1=dst1,src2=dst2` — workaround for Codex's HTTP-header encoding bug |

---

## Caveats / known issues

- **Windows-only at the moment**. Tested only on Windows 11. Linux/macOS code branches exist in the source (for `subprocess.Popen` defaults, `mklink`-style symlinks, xterm/gnome-terminal/alacritty/kitty launching) but have never been exercised end-to-end. Treat Unix support as unverified.
- **`cancel_codex_job` is unreliable for stuck jobs**. The cancel path waits on the stream monitor, which can itself hang. Window-mode jobs are detached and cannot be cancelled through this tool — close the viewer or kill the PID externally.
- **Non-ASCII cwd**. Codex CLI puts the working directory into HTTP headers; non-ASCII bytes trigger an upstream retry loop. Use `CCB_CWD_REMAPS` to map the path to an ASCII junction (Windows: `mklink /J C:\ascii-alias D:\real-path`).
- **Stream is JSONL plus stderr**. Codex's internal `tracing` log (timestamps, retries, Wall-time summaries) is interleaved with the JSONL event stream. The viewer suppresses these lines by default; set `CCB_SHOW_TRACE=1` to surface them.
- **Window mode needs a terminal emulator**. On Windows: wezterm, Windows Terminal (`wt`), or a bare new console as last fallback. Wezterm is preferred for its UTF-8 / ANSI handling.

---

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

`peek_codex` reads the stream file's mtime + tail events. `wait_for_codex` polls for `last.md` to appear. Neither talks to the Codex process.

---

## Development

```bash
git clone https://github.com/uuz495/claude-codex-bridge
cd claude-codex-bridge
pip install -e .[dev]
pytest                       # (Phase 2: tests coming soon)
ruff check .
```

---

## License

MIT — see [LICENSE](LICENSE).

---

## Roadmap

- Phase 1 (current): refactor monolith into a package, move hardcoded values to config, MIT license, baseline README.
- Phase 2: smoke tests, GitHub Actions CI, examples folder, screenshots and a demo recording in the README.
