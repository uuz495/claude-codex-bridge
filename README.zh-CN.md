# claude-codex-bridge

> 在 Claude Code 里把任务派给 Codex 和 Gemini —— codex 在它自己的终端窗口里以真实交互 session 运行。

<sub>[English](README.md)</sub>

- 一个 spawn 工具（`spawn_codex`）+ 两个 flag（`wait` / `with_window`）选模式 —— 不用在 4 个长得差不多的工具里挑。
- 默认模式开一个新终端 tab（Windows Terminal / wezterm / Unix x-terminal-emulator），直接跑 `codex --yolo "<prompt>"`，TTY 是真的。你看到的就是 codex 自己的 TUI —— `apply_patch` 块、inline diff、命令输出、reasoning summary —— 全部 codex 自己渲染，不经任何中间层。
- 并行 spawn 自动堆 tab 到同一个终端窗口（Windows 优先 `wt new-tab` —— 不用 IPC 握手就能接最近的 wt 窗口；wezterm 作 fallback）。
- Bridge 通过 codex 自己的 session rollout 文件 `~/.codex/sessions/YYYY/MM/DD/rollout-<ts>-<sid>.jsonl` 跟踪完成状态。**不查 codex pid**，codex 比 MCP server 活得久。

## 快速开始

```bash
npm i -g @openai/codex                  # 或 @google/gemini-cli，或都装
git clone https://github.com/uuz495/claude-codex-bridge
```

注册到 Claude Code —— 在 `~/.claude.json` 的 `mcpServers` 下加：

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

重启 Claude Code。8 个工具以 `mcp__claude-codex-bridge__` 前缀注册进来（多账号开启后 9 个）。

## 示例

跟 Claude 说：

> 用 `spawn_codex` 让 codex 写一个二分 Fibonacci 到 `fib.py` 然后跑 10 个测试。

Claude 瞬间拿到 `job_id`。一个新终端 tab 弹出来跑 codex 的原生 TUI —— 你看到的是 codex 自己渲染的推理、tool call、文件 diff、命令输出。`peek_codex(job_id)` 从 rollout 文件解析活动状态，`wait_for_codex(job_id)` 阻塞等 codex 写出 `task_complete` 事件。两者都不跟 codex 进程通讯。

## 工具

8 个工具（多账号开启后 9 个）。接口面刻意做小 —— 一个 spawn 入口 + flag，加上正交的辅助工具。

| 工具 | 用途 |
|---|---|
| `spawn_codex(prompt, wait=False, with_window=True, session_id, account, timeout_sec)` | 跑 codex。**默认**（`wait=False, with_window=True`）：新终端 tab 里 native TUI，立刻返回 `job_id` + `rollout_file`。`wait=True, with_window=False`：走老的 `codex exec --json` 同步阻塞，返回 final message 文本。`wait=True, with_window=True`：开窗口 **同时**阻塞等 `task_complete`。`wait=False, with_window=False`：报错（没法观察也没法返回结果）。 |
| `peek_codex(job_id)` | 从 rollout（或老 --json）文件解析活动快照：tool call / 文件改动 / agent message / 完成状态 + final_message。不推断进程生死。 |
| `wait_for_codex(job_id, timeout_sec)` | 阻塞等 rollout 出现 `event_msg/task_complete`，或超时。 |
| `cancel_codex_job(job_id)` | 尽力取消。Native TUI job 是 detach 的 —— 只能改磁盘 job 状态为 `cancelled`，要真停 codex 必须关终端窗口。 |
| `list_codex_jobs(limit=20)` | 列最近 jobs，最新在前。 |
| `spawn_gemini(prompt)` | 一次性 Gemini CLI 调用，返回 stdout。 |
| `spawn_parallel(tasks)` | 并发跑 N 个 codex/gemini 任务。Codex 任务默认 `wait=True, with_window=False`，调用方拿到 final message 不用开 N 个 tab。 |
| `list_logs(n)` | 最近 N 条子进程日志路径。 |

<details>
<summary><b>多账号轮换</b> —— 可选，默认关，1 个工具</summary>

设 `CCB_ENABLE_ROTATION=1` 才暴露 `manage_codex_accounts(action, ...)`：

| Action | 用途 |
|---|---|
| `list` | 返回轮换次序 + 每个账号状态。 |
| `get_login_cmd(name)` | 返回 `CODEX_HOME=... codex login` 命令串。 |
| `add(name, overwrite=False)` | 注册 `name`（要求你已经先 pre-login 到它的 `CODEX_HOME`）。 |
| `reset(name, status)` | 设置 `name` 状态（`active` / `quota_exhausted` / `banned` / `auth_invalid` / `dead`）。 |
| `probe(timeout_sec=45)` | 每个账号跑一次 trivial `codex exec` 探活。 |
| `remove(name, delete_files=False)` | 从轮换里删除 `name`，可选同时清 `accounts/<name>/`。 |

> Window 模式的自动轮换有限：TUI 接到终端后，任务中途换账号不现实。需要指定账号就传 `account=...` 到 `spawn_codex`，否则用当前 `CODEX_HOME`（默认账号）。Legacy 同步模式（`wait=True, with_window=False`）仍然完整轮换。

> 注意：用多个 ChatGPT Plus/Pro 账号绕开速率限制可能违反 OpenAI 服务条款。启用前请阅读你所在服务商的条款。

</details>

## 推荐用法

### 用 `/loop` 自动轮询

```
/loop 10m peek codex job j-xxxxxxxxxx and tell me what changed since last time
```

Claude 每 10 分钟唤醒一次，调 `peek_codex`，报新 tool call / file change / 完成状态。

### 阻塞等完成

Codex 跑完后 Claude 还有后续工作（review、commit、派下一个 phase）时，同一轮里 dispatch + wait：

> 用这个 HANDOFF spawn codex，然后 `wait_for_codex` timeout 5400s。等返回后读 final message 告诉我是否满足验收标准。

Claude 阻塞等 rollout 出现 `event_msg/task_complete`，然后接 review 步骤。

### 让 codex 自验证

让 codex 在 final message 末尾输出可解析的判定：

> 实现改动。之后跑 `pytest tests/test_X.py`。最终消息末尾用 `STATUS: PASS` 或 `STATUS: FAIL: <reason>`。

`peek_codex(...)["final_message"]` 直接带这一行（从 `task_complete.last_agent_message` 提取）。

### 通过 `session_id` 串接多步

把上一步的 `session_id` 传给下一个 `spawn_codex`，继承之前的推理 + 工具调用历史：

> 先用 prompt P1 spawn codex。拿到返回的 session_id S 后，用 `session_id=S` + prompt P2 spawn 另一个窗口。

## 配置

通过环境变量或 `~/.ai-bridge/config.json` 覆盖默认值。优先级：env > config 文件 > 默认。

| 设置 | 环境变量 | 默认 | 用途 |
|---|---|---|---|
| Codex 模型 | `CCB_CODEX_MODEL` | `gpt-5.5` | `-m` flag |
| 推理强度 | `CCB_REASONING_EFFORT` | `high` | `-c model_reasoning_effort=...` |
| 推理摘要 | `CCB_REASONING_SUMMARY` | `auto` | `-c model_reasoning_summary=...` —— `none` / `auto` / `concise` / `detailed`。每次 spawn 强制覆盖，确保 rollout 有可见的"我接下来要做什么"块，不被你 `~/.codex/config.toml` 的 `summary=none` 静音。 |
| Fast mode | `CCB_FAST_MODE` | `1` | `--enable fast_mode` |
| 默认超时 | `CCB_DEFAULT_TIMEOUT` | `1800` | 秒，仅同步模式生效 |
| 多账号 | `CCB_ENABLE_ROTATION` | `0` | 暴露轮换工具 |
| Summary tail | `CCB_SUMMARY_TAIL` | `1` | 自动在每条 prompt 末尾追加"总结你这次做了什么"指令 |
| 显示 codex 内部日志 | `CCB_SHOW_TRACE` | `0` | 仅 legacy viewer（同步/后台）显示 codex 自己的 tracing 噪音 |
| Quota TTL | `CCB_QUOTA_TTL_HOURS` | `5` | 没解析出 "try again in X" 时的兜底冻结时长 |
| Ban TTL | `CCB_BAN_TTL_HOURS` | `24` | `402 / deactivated_workspace` 冻结时长 |
| 非 ASCII cwd 重映射 | `CCB_CWD_REMAPS` | `""` | `src1=dst1,src2=dst2` 绕过 codex 的 HTTP-header 编码 bug |

## 工作原理（window 模式）

```
                     +-- ~/.ai-bridge/jobs/<id>.json
                     |     job 元数据（job_id、session_id、rollout 路径）
                     |
Claude Code  <----+  +-- ~/.codex/sessions/YYYY/MM/DD/rollout-<ts>-<sid>.jsonl
   |               \      codex 自己的 session 日志
   | MCP            \     (event_msg/task_complete = 完成信号,
   v                       patch_apply_end 带 unified_diff,
spawn_codex                response_item/function_call_output 带完整 stdout)
(with_window=True)
   |
   v
wt new-tab / wezterm tab / new-console:
   node <codex>/bin/codex.js --yolo -m gpt-5.5 -c ... "<prompt>"
   (真 PTY, codex native TUI 前台运行)
```

`peek_codex` 解析 rollout 文件。`wait_for_codex` 轮询 `task_complete` 事件。Bridge 不持有 codex 进程 handle —— 关终端窗口就杀 codex，但 bridge 永远不查 codex pid。

## Caveats / 已知问题

- **目前只在 Windows 上跑过**。只在 Windows 11 测过。源码里有 Linux/macOS 代码分支（`subprocess.Popen` 默认值、x-terminal-emulator / gnome-terminal / xterm / alacritty / kitty 启动），但从未端到端跑过。Unix 支持视为未经验证。
- **Codex TUI 跑完不会自动退出**。任务完成后 TUI 停在等下一条用户输入的状态 —— `wait_for_codex` 一看到 rollout 里的 `task_complete` 就立刻返回，但窗口还开着。手动关（Ctrl+C / 关窗口）。
- **`cancel_codex_job` 对 window 模式 job 只改 metadata**。codex 进程是 detach 的 bridge 没 handle —— 真要停 codex 就关终端窗口。
- **非 ASCII cwd**。Codex CLI 把工作目录塞 HTTP header，非 ASCII 字节触发上游 retry 循环。用 `CCB_CWD_REMAPS` 把路径映射到 ASCII junction（Windows：`mklink /J C:\ascii-alias D:\real-path`）。
- **需要终端模拟器在 PATH**（`with_window=True` 时）。Windows 检测顺序：**Windows Terminal (`wt`)** → wezterm → 裸新 console。优先 `wt` 是因为 `new-tab` 不用 IPC 就能接最近的窗口，并行 spawn 干净地共享一个窗口；wezterm 的 tab attach 要 `wezterm-mux-server` + 已注册的 GUI，standalone wezterm GUI（常见情况）只能开新窗。Unix 检测：`x-terminal-emulator` / `gnome-terminal` / `xterm` / `alacritty` / `kitty` / `wezterm`。
- **Legacy 同步模式（`wait=True, with_window=False`）仍走 `codex exec --json`**，继承它的局限（没 inline diff，codex 0.130 真 bug —— 并发 `command_execution` 事件可能丢 `item.completed`）。非平凡任务用默认 window 模式。

## 开发

```bash
git clone https://github.com/uuz495/claude-codex-bridge
cd claude-codex-bridge
pip install -e .[dev]
ruff check .
```

## License

MIT —— 见 [LICENSE](LICENSE)。
