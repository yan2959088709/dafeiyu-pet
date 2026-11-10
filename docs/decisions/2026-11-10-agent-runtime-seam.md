# ADR-2026-11-10-01: 可替换执行内核（AgentRuntime seam + DSH headless sidecar）

- 状态：已接受
- 日期：2026-11-10
- 关联任务：ONES TASK-015（承接 TASK-009 Phase 3）

## Context

用户提出把 DSH / OpenClaw 内化到电子生命中，省掉 harness、工具调用、MCP、
记忆管理与任务规划的自研。前序讨论的结论是「内化能力，不内化目标」：执行层
可以借，生命核心（drives/意图/记忆 schema/人格/预算/治理）必须自有。为验证
这个判断，用户在 git 备份后批准先做实测，再决定生产接线。

实测环境：Windows 11，项目 Python venv，系统 Node 22.20.0；通过 npx 取得
Node 24.22.2。上游版本：

- DSH `@deepseek-ai/dsh` 0.2.2-rc.1，MIT，`node >=24.9.0`。
- OpenClaw `openclaw` 2026.11.2，MIT，`node >=24.16.0 <25 || >=26.1.0`。

## Options Considered

**A. 继续纯自研，不借外部 runtime。** 放弃已成熟的执行/沙箱/审批/事件层，
重复造轮子，且短期看不到真实对比数据。否决。

**B. 整包内化 DSH 或 OpenClaw 作为内核。** 目标漂移（coding/assistant 的
目标函数）、9GB+ 依赖、Node 24 前置、双记忆/双人格/双权限、与 PySide6 壳的
进程模型冲突。否决作为内核。

**C. LifeCore 自有 + AgentRuntime seam + DSH headless sidecar。**（已选）
执行层可替换，生命核心不被 runtime 拥有；DSH 只在重任务时启用。

**D. Fork DSH 改造。** 上游仓库约 360MB、pnpm/TypeScript monorepo、654 包
安装后 9GB；维护与许可成本高。暂不采用。

## Decision

采用 C。新增两个模块，零改动既有 chat/heartbeat：

- `pet/agent_runtime.py`
  - `RuntimeResult`：text/usage/events/error/exit_code/latency/meta。
  - `NativeHarnessRuntime`：包既有 `harness.run_turn`，行为与原路径一致。
  - `DshHeadlessRuntime`：驱动 `dsh --profile headless --json`，聚合 NDJSON
    事件（`session/status/text/thinking/tool_call/tool_result/final/error`），
    累加 `step_end.usage`，处理超时与错误，stderr/异常统一脱敏。
  - `CommandRuntime`：给 OpenClaw 或任意外部 sidecar 留的窄缝。
- `tools/runtime_probe.py`
  - 同一 goal 可跑 `native` / `dsh` / `command`，输出 JSONL 事件与 JSON 汇总。
  - 密钥只从环境变量读取（默认 `DEEPSEEK_API_KEY`），不读 `config.json`、
    不落盘、输出脱敏。

LifeCore（drives/意图/记忆/人格/预算）继续拥有唯一真源；runtime 只执行，
不拥有「她是谁」。DSH 默认的 coding persona 是 profile patch，可在后续用
`--patch` 替换；本次不接生产链路。

## Measured Evidence

- DSH 安装：654 包，4 分 58 秒，9.0 GB。
- DSH headless 真任务（`Reply with exactly: OK`）：
  - 事件：`session → turn_start → step_start → thinking → text → step_end → turn_end → final`
  - usage：input 8,541 + cache read 5,632 = 14,175 tokens；output 2。
  - 延迟：52.9 秒（冷启动，含 profile 装载与模型列表）。
- DSH 工具任务（`pwd`）：
  - `tool_call {tool: "pwsh", input: {command: "pwd"}}` → `tool_result {status: "completed"}`
  - 两步合计约 28,624 tokens。
- OpenClaw：npm 包 unpacked 274.8 MB；首次安装因 PATH 中 Node 22.20 被
  preinstall 检查中止（已下载到约 5.6 GB）；Node 24 置顶 PATH 的重试在本机
  未生效（`which node` 仍解析到系统 22.20），本次未完成端到端验证。架构是
  Gateway + built-in agent runtime + channels，不是可直接嵌入的库。

## Impact

- 冒烟 168 → 171，171/171 全绿。
- 生产主链路无行为变化；seam 是新增层，可随时拔掉。
- 可以继续把 DSH 用作重任务/编码/复杂网页任务的 sidecar，不进入生命循环。
- 为后续 OpenClaw CLI 适配、事件总线、预算映射、DSH persona patch 留出接口。

## Risks

- **成本**：DSH 单次基线约 14k input tokens，工具任务约 28k；桌宠日常对话
  绝不能走这条路径，必须只在重任务启用并加预算门禁。
- **体积**：9GB 完整安装不适合随桌宠分发；生产需要裁剪 profile 或按需安装。
- **Node 24 前置**：本机系统 Node 22.20，需要随包/引导安装并确保 lifecycle
  脚本也使用 Node 24（PATH 顺序）。
- **安全**：外部进程、密钥传递、工具结果回灌是新增攻击面；DSH 自身的
  sandbox/permission 不能替代我们的治理。
- **OpenClaw**：headless 入口与安装尚未验证完成；暂不写入生产路径。

## Deferred

- OpenClaw CLI 适配（等安装与 `--help` 验证）。
- DSH profile patch：把 coding persona 换成桌宠人格，裁剪工具集。
- 事件总线：把 `tool_call/tool_result` 映射到 workstatus/tooldetail/notify。
- 预算映射：把 runtime usage 接到 economy/energy。
- 生产开关：默认关闭，设置页显式启用并设置日预算。
