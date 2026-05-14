# claude-codex-bridge

> An MCP server that lets [Claude Code](https://github.com/anthropics/claude-code) dispatch tasks to [OpenAI Codex](https://github.com/openai/codex) and [Google Gemini](https://github.com/google-gemini/gemini-cli) CLIs — with **fire-and-forget windowed execution** and **zero false "codex died" verdicts**.

Status: **early — Phase 1 (refactor + open-source skeleton). README will expand with examples, screenshots, and a demo gif in Phase 2.**

---

## Why

Existing MCP wrappers for Codex / Gemini all have one of two failure modes:

1. **Synchronous block**: Claude calls the tool and is stuck for 30+ minutes waiting for Codex to finish. You can't ask Claude anything else, and if Codex hangs mid-run you have no way to tell what's wrong.
2. **Background + pid polling**: the wrapper polls `pid_exists()` to check whether Codex is alive. But on Windows the pid recorded is usually a wrapper process (`wt.exe` → `cmd.exe` → `node.exe` → `codex.js`); the wrapper exits while the real Codex is still working, and the MCP wrongly reports **"codex died"** to Claude. Claude then tells you Codex is dead while Codex is busily fixing a bug in another window.

`claude-codex-bridge` introduces **window mode**:

- Codex runs in a `DETACHED_PROCESS` (Windows) / fork-detach (Unix) — completely independent of the MCP server's lifecycle.
- A separate terminal window (wezterm > Windows Terminal > xterm) opens with a tail viewer rendering Codex's JSONL event stream in real time (ANSI colors + ASCII box-drawing, no Unicode dependency).
- The MCP **never inspects Codex's pid**. Completion is detected by Codex writing its `--output-last-message` file.
- `peek_codex(job_id)` reports **facts about the stream file** (silent for Xs / last event type / tool-call count / retry count) and never says "Codex is alive" or "Codex died" — that judgment is yours, from the viewer window.

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

Restart Claude Code, then ask Claude to dispatch a task:

> Use `spawn_codex_window` to have Codex write a tail-recursive Fibonacci in `fib.py` and run 5 test cases.

A new terminal window opens; Codex works in it. Claude returns the `job_id` immediately. Ask Claude `peek the codex job` whenever you want progress.

---

## Tools

### Recommended — Window mode

| Tool | Purpose |
|---|---|
| `spawn_codex_window(prompt, ...)` | Fire-and-forget Codex spawn + auto-opened viewer window. Returns `job_id` immediately. |
| `peek_codex(job_id)` | Snapshot of stream state (no liveness verdict). |
| `wait_for_codex(job_id, timeout_sec)` | Block until `last_message_file` appears, or timeout. |

### Gemini + parallel

| Tool | Purpose |
|---|---|
| `spawn_gemini(prompt)` | One-shot Gemini CLI call. |
| `spawn_parallel(tasks)` | Fan out N codex/gemini tasks concurrently. |

### Legacy — synchronous / background

These work but have the failure modes described above. Use window mode unless you have a specific reason.

| Tool | Notes |
|---|---|
| `spawn_codex` | Sync block on result. |
| `spawn_codex_live` | Sync + live viewer window, but caller still blocks. |
| `codex_inject(session_id, prompt)` | Interrupt a live session + resume with new prompt. |
| `list_running_codex` | Enumerate live processes the bridge tracks. |
| `spawn_codex_background` | Background asyncio task. Orphaned on MCP restart. |
| `poll_codex_job(job_id)` | Poll background job state. |
| `list_codex_jobs` | List recent jobs. |
| `cancel_codex_job` | Best-effort cancel (may hang — see Caveats). |

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

> ⚠ **Heads-up**: pooling multiple ChatGPT Plus/Pro accounts to bypass rate limits may violate provider Terms of Service. The mechanism exists because it's useful for owning multiple legitimate accounts (e.g. personal + work). Don't use it to abuse the service.

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

- **Windows-first development**: most code paths assume Windows. Linux/macOS are supported but less tested. PRs welcome.
- **`cancel_codex_job` can hang**: the underlying stream monitor doesn't always notice the process is dead. Window-mode jobs cannot be cancelled via this tool at all — close the viewer window or kill the PID externally.
- **Non-ASCII cwd**: Codex CLI puts cwd into HTTP headers; non-ASCII bytes trigger a 5-retry-then-fail loop. Use `CCB_CWD_REMAPS` to map your path to an ASCII junction (Windows: `mklink /J C:\\ascii-alias D:\\real-path`).
- **Stream JSONL is mixed with stderr**: Codex's internal `tracing` log (ERRORs, retries, Wall-time summaries) is interleaved with the JSONL event stream. The viewer suppresses noise by default; set `CCB_SHOW_TRACE=1` to see it.
- **Window mode requires a terminal emulator**: wezterm, Windows Terminal (`wt`), or xterm/gnome-terminal. Falls back to a bare new console if none found.

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

- **Phase 1 (current)**: refactor monolith → package, config-ize hardcoded values, MIT license, baseline README.
- **Phase 2**: smoke tests, GitHub Actions CI, full README with screenshots + demo gif, examples/ folder.

Not aiming for HN front page; built for personal use and shared in case it's useful to someone else.
