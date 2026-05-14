# claude-codex-bridge

> 在 Claude Code 里把任务派给 Codex 和 Gemini —— codex 在自己的终端窗口里跑，桥不靠 pid 猜进程死活。

<sub>[English](README.md)</sub>

<!-- Demo 录屏会在 Phase 2 加上。 -->
<!-- 暂时方案：手动开个 wezterm 跑 python tail_viewer.py <stream>，用 spawn_codex_window 派一个任务看效果。 -->

## 为什么写这个

- **同步阻塞型 wrapper** 让 Claude 等 codex 跑完才返回。整段 MCP 调用挂着，Claude 在那里干不了别的。
- **后台 + pid 轮询型 wrapper** 用 `pid_exists()` 检查 codex 进程死活。Windows 上记录的 pid 通常是外层包装（`wt.exe` → `cmd.exe` → `node.exe` → codex.js），外层退出时内层 codex 还在干活 —— status 工具就误报 "codex 死了"，但其实没死。
- **Window mode** 把 codex 进程从 MCP server 生命周期里 detach 出去（`DETACHED_PROCESS`），开一个独立终端窗口流式渲染事件，完成检测靠 codex 自己写的文件，**永远不查 pid**。Codex 比 MCP server 活得久，桥不做生死推断。

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

重启 Claude Code 就行。14 个工具以 `mcp__claude-codex-bridge__` 前缀注册进来。

## 示例

跟 Claude 说：

> 用 `spawn_codex_window` 让 codex 写一个二分 Fibonacci 到 `fib.py` 然后跑 10 个测试。

你会看到：

1. Claude 调 `spawn_codex_window`，瞬间拿到 `{ job_id: "j-xxxxxxxxxx", window_opened: true, ... }`。
2. 弹出一个 wezterm 窗口，实时流 codex 的推理 / tool call / 文件改动。
3. Claude 回复 "派出去了，job_id `j-xxxxxxxxxx`，viewer 已开"。
4. 你随时问 "怎么样了？"，Claude 调 `peek_codex(job_id)` 报变化。
5. codex 写出 final message 时，`wait_for_codex` 返回，或下一次 `peek_codex` 显示 `completed: true` + 最终结果。

## 工具

14 个工具（开启多账号后 20 个）。按模式分组，点开看详情。

<details>
<summary><b>Window 模式</b> —— 推荐路径，3 个工具</summary>

| 工具 | 用途 |
|---|---|
| `spawn_codex_window(prompt, ...)` | Detach spawn codex 并开 viewer 窗口，立刻返回 `job_id`。 |
| `peek_codex(job_id)` | 当前 stream 状态快照，不推断进程生死。 |
| `wait_for_codex(job_id, timeout_sec)` | 阻塞等 `last_message_file` 写出，或超时。 |

</details>

<details>
<summary><b>Gemini + 并行</b> —— 2 个工具</summary>

| 工具 | 用途 |
|---|---|
| `spawn_gemini(prompt)` | 一次性 Gemini CLI 调用。 |
| `spawn_parallel(tasks)` | 并发跑 N 个 codex/gemini 任务。 |

</details>

<details>
<summary><b>其他模式</b> —— 同步 / 后台，8 个工具</summary>

这些把 codex 进程握在 MCP server 自己的生命周期里。保留是因为短阻塞调用、交互式打断在飞 session 这种用法更简单。MCP server 重启时这类 job 会丢。

| 工具 | 备注 |
|---|---|
| `spawn_codex` | 同步阻塞，返回 codex 最终输出。 |
| `spawn_codex_live` | 同步阻塞 + live viewer 窗口。 |
| `codex_inject(session_id, prompt)` | 干掉正在跑的 live session，用同 session id resume 新 prompt。 |
| `list_running_codex` | 列当前 bridge 跟踪的 live 进程。 |
| `spawn_codex_background` | 后台 asyncio task，返回 `job_id`。MCP 重启时会 orphan。 |
| `poll_codex_job(job_id)` | 查后台 job 状态。 |
| `list_codex_jobs` | 列最近 jobs（任意模式）。 |
| `cancel_codex_job` | 取消后台 job。Window 模式的 job 不能这样取消，见 Caveats。 |

</details>

<details>
<summary><b>多账号轮换</b> —— 可选，默认关，6 个工具</summary>

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

</details>

<details>
<summary><b>杂项</b> —— 1 个工具</summary>

| 工具 | 用途 |
|---|---|
| `list_logs(n)` | 最近 N 条子进程日志路径。 |

</details>

## 推荐用法

几个跟 window 模式搭配得不错的工作流模式。这些是工具之上的约定，不是 bridge 本身的功能。

### 用 `/loop` 自动轮询进度

派完长任务后，让 Claude 帮你定期看，不用自己问：

```
/loop 10m peek codex job j-xxxxxxxxxx and tell me what changed since last time
```

Claude 每 10 分钟自动唤醒一次，调 `peek_codex`，报新增的 tool call / file change / 完成状态。codex 干完了就 stop loop。

### 用 `wait_for_codex` 阻塞等

如果 codex 跑完后 Claude 还有后续工作（review、commit、派下一个 phase），在同一轮里 dispatch + wait：

> 用这个 HANDOFF spawn codex，然后 `wait_for_codex` timeout 5400s。等返回后读 final message 告诉我是否满足验收标准。

Claude 在 wait 调用里阻塞着等 `last_message_file` 出现。你可以离开对话；Claude 自己接 review 步骤。

### 让 codex 自验证

Prompt 写法上让 codex 在退出前给一个机器可解析的判定：

> 实现改动。之后跑 `pytest tests/test_X.py`。所有测试通过才算成功。最终消息末尾用 `STATUS: PASS` 或 `STATUS: FAIL: <reason>`。

`peek_codex(...)["final_message"]` 就带一个明确判定，可被解析。

### 通过 `session_id` 串接多步任务

多步任务里把上一步的 `session_id` 传给下一个 `spawn_codex_window`，codex 会继承之前的推理 + 工具调用历史：

> 先用 prompt P1 spawn codex。拿到返回的 session_id S 后，用 `session_id=S` + prompt P2 spawn 另一个窗口。

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

## Caveats / 已知问题

- **目前只在 Windows 上跑过**。只在 Windows 11 测过。源码里有 Linux/macOS 代码分支（`subprocess.Popen` 默认值、`mklink`→symlink、xterm/gnome-terminal/alacritty/kitty 启动），但从未端到端跑过。Unix 支持视为未经验证。
- **`cancel_codex_job` 对卡住的 job 不可靠**。cancel 路径在 stream monitor 上等，monitor 本身可能 hang。Window 模式的 job 是 detach 的，没法通过这个工具取消 —— 关 viewer 窗口或手动 kill PID。
- **非 ASCII cwd**。Codex CLI 把工作目录塞 HTTP header，非 ASCII 字节触发上游 retry 循环。用 `CCB_CWD_REMAPS` 把路径映射到 ASCII junction（Windows：`mklink /J C:\ascii-alias D:\real-path`）。
- **Stream 同时有 JSONL 和 stderr**。Codex 内部 `tracing` 日志（时间戳 / retry / Wall-time 摘要）和 JSONL 事件流交织。viewer 默认吞这些行；设 `CCB_SHOW_TRACE=1` 才显示。
- **Window 模式需要终端模拟器**。Windows 上：wezterm、Windows Terminal (`wt`)，最后兜底裸新 console。优先 wezterm，UTF-8 / ANSI 处理更稳。

## 开发

```bash
git clone https://github.com/uuz495/claude-codex-bridge
cd claude-codex-bridge
pip install -e .[dev]
pytest                       # 测试还在 Phase 2 写
ruff check .
```

## Roadmap

- **Phase 1（当前）**：把单文件重构成 package，把硬编码值移到 config，MIT 协议，基础 README。
- **Phase 2**：smoke tests、GitHub Actions CI、examples 文件夹、README 加截图和 demo 录屏。

## License

MIT —— 见 [LICENSE](LICENSE)。
