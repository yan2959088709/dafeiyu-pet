# 实测报告：DSH headless / OpenClaw 作为桌宠执行内核

日期：2026-11-10
关联：ONES TASK-015，ADR-2026-11-10-01

## 目的

回答一个问题：把 DSH 或 OpenClaw 内化成电子生命的执行内核，是否比纯自研
更好。判断标准不是「能不能跑」，而是：

1. 能否 headless 驱动，拿到结构化事件？
2. 事件面是否覆盖 tool call / cost / error？
3. 单次自主行动的成本与延迟是多少？
4. 是否可替换、可裁剪、可治理？
5. 许可证与依赖是否可接受？

## 环境与备份

- Windows 11，项目 Python venv。
- 系统 Node 22.20.0；npx 取得 Node 24.22.2。
- git 备份：`tag backup/pre-runtime-spike-20261110` @ `dd202fa`（工作区干净）。
- 上游：DSH `@deepseek-ai/dsh@0.2.2-rc.1`（MIT）；OpenClaw `openclaw@2026.11.2`（MIT）。

## DSH 实测

### 安装

```bash
npm install --prefix dsh @deepseek-ai/dsh@0.2.2-rc.1
```

- 654 个包，4 分 58 秒。
- 安装体积 **9.0 GB**（其中 `@deepseek-ai/*` 6.4GB；`openai` 447MB、
  `@anthropic-ai` 220MB、`@aws-sdk`/`@smithy` 约 370MB）。

### headless 入口

```bash
dsh --profile headless --help
```

关键选项：单任务、`--json`（NDJSON 事件）、`--session-id`（恢复会话）、
`--permission-mode`、`--approval-policy`、`--add-dir`。文档明确：无 GUI、
无服务器、无浏览器、无监听端口，一次调用一个任务。

### 事件契约

真实任务 `Reply with exactly: OK` 的 stdout：

```jsonl
{"type":"session","sessionId":"session-...","cwd":"..."}
{"type":"status","phase":"turn_start","turn":1}
{"type":"status","phase":"step_start","turn":1,"step":1}
{"type":"thinking","text":""}
{"type":"text","text":"OK"}
{"type":"status","phase":"step_end","turn":1,"step":1,"usage":{"inputTokens":8541,"outputTokens":2,"cacheReadTokens":5632,"cacheWriteTokens":0,"totalTokens":14175}}
{"type":"status","phase":"turn_end","turn":1,"reason":{"kind":"completed"}}
{"type":"final","sessionId":"session-...","reason":{"kind":"completed"},"text":"OK"}
```

### 成本与延迟

| 任务 | 事件 | tokens | 延迟 |
|---|---|---|---|
| `Reply with exactly: OK` | 8 条，无工具 | 14,175（input 8,541 + cache read 5,632） | 52.9s |
| `pwd`（工具） | 含 `tool_call`/`tool_result` | 两步合计约 28,624 | 未单独计时 |

工具事件样例：

```jsonl
{"type":"tool_call","callId":"call_...","tool":"pwsh","input":{"description":"Print current working directory","command":"pwd"}}
{"type":"tool_result","callId":"call_...","status":"completed","result":"...path..."}
```

### 关键结论

- DSH 的 headless 是**真正的可嵌入执行内核**：单任务、结构化事件、会话恢复、
  权限/沙箱、无宿主 UI。这是本次实测最有价值的发现。
- 但它的基线成本是 coding agent 的成本：**一个 trivial 任务 ~14k input
  tokens，冷启动 ~53 秒**。这不能进生命循环，只能做「重任务 sidecar」。
- 默认 persona 是 `You are a coding agent...`，可通过 profile patch 替换；
  工具集同样可裁剪。
- 9GB 安装不适合随桌宠分发；生产需要裁剪 profile 或按需安装。
- 系统 Node 22.20 不够，必须 Node 24+；npm lifecycle 脚本也要用 Node 24
  （PATH 顺序），否则安装会被 preinstall 检查中止。

## OpenClaw 实测

- npm 包 `openclaw@2026.11.2`，unpacked 274.8MB。
- 首次安装：依赖已下到约 5.6GB，但 preinstall 检查发现 PATH 里是 Node
  22.20，安装中止并清理。
- 第二次把 Node 24 的 bin 置顶 PATH 重试，Git Bash 的 `which node` 仍解析到
  系统 Node 22.20，重试被停止；OpenClaw 的 CLI/headless 入口本次未完成
  端到端验证。
- 架构文档显示：Gateway（会话/工具/事件/通道控制面）+ built-in agent
  runtime + channels（Discord/Telegram/Slack/WhatsApp 等 20+）+ 插件/Skills。
  它不是可以 `import` 的库，而是一个常驻网关。
- 结论：OpenClaw 更像「另一个宿主」，适合做独立常驻助理；不适合作为桌宠
  进程内的执行内核。若要接，应通过它的 Gateway/CLI 做 sidecar，而不是内化
  它的会话/记忆/人格。

## 对本项目的意义

1. **验证了「内化能力，不内化目标」**：DSH 的执行层可以借，但它的目标函数
   是 coding；直接把它当核心会把桌宠变成 coding assistant 的前端。
2. **成本门禁是硬前提**：DSH 单次 ~14k tokens/53s，必须在 LifeCore 里按
   「重任务」显式启用，并设置日预算；日常说话/小动作继续走原生 harness。
3. **事件契约可直接复用**：`tool_call/tool_result/usage/final` 足以驱动
   workstatus、tool-detail、通知和 token 账本。
4. **可替换性成立**：`AgentRuntime` seam 已落地，DSH 可随时拔掉，不改变
   记忆/人格/预算的唯一真源。

## 安全说明

真实探针使用了项目 `config.json` 里已有的 DeepSeek Key（DPAPI 解密），
只通过子进程环境变量传入，未打印、未落盘；探针输出统一脱敏。Redis/文件/
网络工具未在探针中启用。

## 复现

```bash
# 1) Node 24（任选其一）
npx -y node@24 --version
# 或把 Node 24 放到 PATH 最前面

# 2) 安装 DSH（隔离目录）
npm install --prefix dsh @deepseek-ai/dsh@0.2.2-rc.1

# 3) 跑探针（Key 只从环境读取）
DEEPSEEK_API_KEY=... .venv/Scripts/python.exe tools/runtime_probe.py \
  --runtime dsh \
  --node <node24.exe> \
  --dsh dsh/node_modules/@deepseek-ai/dsh/lib/bin.js \
  --home <dsh-home> \
  --goal "Reply with exactly: OK" --json
```
