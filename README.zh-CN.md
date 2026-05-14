# claude-codex-bridge

[English](README.md) | **简体中文**

一个 MCP server，让 [Claude Code](https://github.com/anthropics/claude-code) 通过 MCP 把任务派给 [OpenAI Codex](https://github.com/openai/codex) 和 [Google Gemini](https://github.com/google-gemini/gemini-cli) CLI。Window 模式下 codex 进程脱离 MCP server 生命周期独立运行，桥不会基于 pid 猜测 codex 是否还在工作。

状态：Phase 1 —— 重构 + 开源骨架。测试、CI、示例、截图计划在 Phase 2 完成。

---

## 为什么写这个

围绕 codex 这类长跑 CLI 做 MCP wrapper，有两种常见做法都各有摩擦：

1. **同步阻塞**。Wrapper 一直 await 直到 codex 退出才返回。调用方（比如 Claude Code）整段时间被占用做不了别的。codex 卡住时也没有进度信号。
2. **后台 + pid 轮询**。Wrapper 在后台 spawn codex，status 工具用 `pid_exists()` 检查那个 pid 是否还活。Windows 上记录的 pid 通常是外层包装进程（`wt.exe` → `cmd.exe` → `node.exe` → codex.js），外层退出时内层 codex 还在干活，status 工具就会**误报 codex 已死**，但其实没死。

这个 server 走第三条路，叫 *window mode*：

- Codex 作为 Windows `DETACHED_PROCESS` 子进程跑。MCP server spawn 后立刻丢掉 process handle，不再监管它。
- 单独开一个终端窗口（wezterm 或 Windows Terminal）跑 tail viewer，把 codex 的 JSONL 事件流渲染出来 —— ANSI 颜色 + ASCII box-drawing，不依赖 Unicode 高位字符。
- MCP server 不调 `pid_exists()` 也不调任何等价检测。完成信号来自 codex 写入 `--output-last-message` 文件。
- `peek_codex(job_id)` 返回 stream 文件能给出的事实：距离上次写入多少秒、最后一个事件类型、tool call 计数、retry 次数、累积错误。它不报告 "codex 还活着" 或 "codex 死了" —— 那些判断留给看 viewer 窗口的用户。

---

## 快速开始

```bash
# 装 Codex / Gemini CLI（按需选）
npm i -g @openai/codex
npm i -g @google/gemini-cli

# 装这个 MCP server
pip install claude-codex-bridge

# 注册到 Claude Code（写入 ~/.claude.json 的 mcpServers）
{
  "mcpServers": {
    "claude-codex-bridge": {
      "command": "claude-codex-bridge"
    }
  }
}
```

或者，不想 `pip install` 的话，直接指向自带的 wrapper：

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

重启 Claude Code，让 Claude 派活：

> 用 `spawn_codex_window` 让 Codex 在 `fib.py` 写一个尾递归 Fibonacci 然后跑 5 个测试用例。

新终端窗口会弹出，codex 在那里跑。Claude 立刻拿到 `job_id`。想看进度就让 Claude peek 一下。

---

## 工具

### Window 模式

| 工具 | 用途 |
|---|---|
| `spawn_codex_window(prompt, ...)` | Detach spawn codex 并开 viewer 窗口。立刻返回 `job_id`。 |
| `peek_codex(job_id)` | 当前 stream 状态快照。不推断进程生死。 |
| `wait_for_codex(job_id, timeout_sec)` | 阻塞等 `last_message_file` 写出，或超时。 |

### Gemini + 并行

| 工具 | 用途 |
|---|---|
| `spawn_gemini(prompt)` | 一次性 Gemini CLI 调用。 |
| `spawn_parallel(tasks)` | 并发跑 N 个 codex/gemini 任务。 |

### 其他模式

这些工具把 codex 进程握在 MCP server 自己的生命周期里。保留它们是因为短阻塞调用、交互式打断在飞 session 这种用法更简单。MCP server 重启时这类 job 会丢失。

| 工具 | 备注 |
|---|---|
| `spawn_codex` | 同步阻塞，返回 codex 最终输出。 |
| `spawn_codex_live` | 同步阻塞 + live viewer 窗口。 |
| `codex_inject(session_id, prompt)` | 干掉正在跑的 live session，再用同 session id resume 新 prompt。 |
| `list_running_codex` | 列当前 bridge 跟踪的 live 进程。 |
| `spawn_codex_background` | 后台 asyncio task，返回 `job_id`。MCP 重启时会 orphan。 |
| `poll_codex_job(job_id)` | 查后台 job 状态。 |
| `list_codex_jobs` | 列最近 jobs（任意模式）。 |
| `cancel_codex_job` | 取消后台 job。Window 模式的 job 不能这样取消，见 Caveats。 |

### 多账号轮换（可选，默认关）

设 `CCB_ENABLE_ROTATION=1` 才暴露这些工具。默认禁用。

| 工具 | 用途 |
|---|---|
| `save_codex_account(name)` | 把 `~/.ai-bridge/accounts/<name>/` 注册成账号。 |
| `list_codex_accounts` | 看轮换次序 + 每个账号状态。 |
| `get_codex_login_cmd(name)` | 拿到 `CODEX_HOME=... codex login` 命令串。 |
| `reset_account_state(name, status)` | 手动改账号状态。 |
| `probe_all_accounts` | 每个账号跑一次 trivial 调用，识别 quota/ban 状态。 |
| `remove_codex_account(name)` | 从轮换里删除。 |

> 注意：用多个 ChatGPT Plus/Pro 账号绕开速率限制可能违反 OpenAI 服务条款。启用前请阅读你所在服务商的条款。

### 杂项

| 工具 | 用途 |
|---|---|
| `list_logs(n)` | 最近 N 条子进程日志路径。 |

---

## 配置

所有设置都有内置默认值。可通过环境变量或 `~/.ai-bridge/config.json` 覆盖。优先级：env > config 文件 > 默认。

| 设置 | 环境变量 | 默认 | 用途 |
|---|---|---|---|
| Codex 模型 | `CCB_CODEX_MODEL` | `gpt-5` | `-m` flag |
| 推理强度 | `CCB_REASONING_EFFORT` | `medium` | `-c model_reasoning_effort=...` |
| Fast mode | `CCB_FAST_MODE` | `0` | `--enable fast_mode` |
| 默认超时 | `CCB_DEFAULT_TIMEOUT` | `1800` | 秒，仅同步模式生效 |
| 多账号 | `CCB_ENABLE_ROTATION` | `0` | 暴露轮换工具 |
| Summary tail | `CCB_SUMMARY_TAIL` | `1` | 自动在每条 prompt 末尾追加"总结你这次做了什么"指令 |
| 显示 codex 内部日志 | `CCB_SHOW_TRACE` | `0` | viewer 显示 codex 自己的 tracing 噪音 |
| Quota TTL | `CCB_QUOTA_TTL_HOURS` | `5` | 没解析出 "try again in X" 时的兜底冻结时长 |
| Ban TTL | `CCB_BAN_TTL_HOURS` | `24` | `402 / deactivated_workspace` 冻结时长 |
| 非 ASCII cwd 重映射 | `CCB_CWD_REMAPS` | `""` | `src1=dst1,src2=dst2` 绕过 codex 的 HTTP-header 编码 bug |

---

## Caveats / 已知问题

- **目前只在 Windows 上跑过**。只在 Windows 11 测过。源码里有 Linux/macOS 代码分支（`subprocess.Popen` 默认值、`mklink`-风格 symlink、xterm/gnome-terminal/alacritty/kitty 启动），但从未端到端跑过。Unix 支持视为未经验证。
- **`cancel_codex_job` 对卡住的 job 不可靠**。cancel 路径在 stream monitor 上等，monitor 本身可能 hang。Window 模式的 job 是 detach 的，没法通过这个工具取消 —— 关 viewer 窗口或手动 kill PID。
- **非 ASCII cwd**。Codex CLI 把工作目录塞 HTTP header，非 ASCII 字节触发上游 retry 循环。用 `CCB_CWD_REMAPS` 把路径映射到 ASCII junction（Windows：`mklink /J C:\ascii-alias D:\real-path`）。
- **Stream 同时有 JSONL 和 stderr**。Codex 内部 `tracing` 日志（时间戳 / retry / Wall-time 摘要）和 JSONL 事件流交织。viewer 默认吞这些行；设 `CCB_SHOW_TRACE=1` 才显示。
- **Window 模式需要终端模拟器**。Windows 上：wezterm、Windows Terminal (`wt`)，最后兜底用裸新 console。优先 wezterm，UTF-8 / ANSI 处理更稳。

---

## 工作原理

```
                     +-- ~/.ai-bridge/jobs/<id>.json
                     |     job 元数据
                     |
Claude Code  <----+  +-- ~/.ai-bridge/streams/<id>.jsonl
   |               \      JSONL 事件流 (codex --json)
   | MCP            \
   v                 +-- ~/.ai-bridge/jobs/<id>.last.md
spawn_codex_window         最终 assistant 消息（完成信号）
   |
   v
codex.js exec --json --output-last-message ...
   (DETACHED_PROCESS, 父进程不持 handle)
   |
   v
wezterm new-window:  python tail_viewer.py <stream>
   (把 JSONL 渲染成 ANSI-colored ASCII art)
```

`peek_codex` 读 stream 文件的 mtime + 末尾事件。`wait_for_codex` 轮询 `last.md` 是否出现。两者都不跟 codex 进程通讯。

---

## 开发

```bash
git clone https://github.com/uuz495/claude-codex-bridge
cd claude-codex-bridge
pip install -e .[dev]
pytest                       # (Phase 2: 测试还在写)
ruff check .
```

---

## License

MIT —— 见 [LICENSE](LICENSE)。

---

## Roadmap

- Phase 1（当前）：把单文件重构成 package，把硬编码值移到 config，MIT 协议，基础 README。
- Phase 2：smoke tests、GitHub Actions CI、examples 文件夹、README 加截图和 demo 录屏。
