# ADR-2026-11-10-02: DSH 重型任务执行内核的生产接线

- 状态：已接受
- 日期：2026-11-10
- 关联任务：ONES TASK-016（承接 TASK-015）

## Context

TASK-015 实测确认：DSH 0.2.2-rc.1 的 `--profile headless --json` 是可用的
单任务执行内核，事件面（session/status/text/thinking/tool_call/tool_result/
final）足以驱动工作状态与生活日志。但实测也给出两个硬约束：

1. 成本：trivial 任务 14,175 tokens / 52.9s；含工具任务约 28,624 tokens。
2. 体积：完整安装 9.0 GB，Node 24+。

因此 DSH 不能进日常聊天/心跳主链路，只能作为用户显式发起的「重型任务」
执行内核。本 ADR 记录生产接线的边界与实现。

## Options Considered

**A. 所有聊天/心跳自动路由到 DSH。** 成本与延迟不可接受（日常一句话
~14k tokens / 50s），且 coding persona 会污染桌宠人格。否决。

**B. 先做 LifeLoop 自主调度，再把 DSH 作为意图之一。** 方向正确，但
LifeLoop 尚未落地（P0 路线），本任务先把「手动重型任务」这条生产路径
打通，作为 LifeLoop 的现成执行器。本任务不实现自动路由。

**C. 设置页/菜单手动发起重型任务 + 预算门禁 + 事件映射。**（已选）
最小可用生产路径：默认关闭，用户显式启用并配置路径/预算；运行时在
worker 线程调用 DSH，结果走既有聊天出口，事件写工作状态与生活日志。

**D. 内化 OpenClaw 作为第二个 runtime。** OpenClaw 是 Gateway + channels
形态，不是可嵌入库；本机 Node 24 PATH 问题也未解决。本版明确不接。

## Decision

采用 C。落点：

- **配置**：`DEFAULT_CFG.runtime` 新段（default 关闭）：
  `enabled / nodePath / dshBin / home / workspace / permissionMode /
  dailyBudgetTokens / maxRunsPerDay / timeoutSec`。加入 `OBJECT_KEYS`
  精确键集校验；`validate()` 对单键非法做 fail-closed 纠正。
- **门禁**：`pet/runtime_manager.py` 的 `RuntimeGate`——启用/路径检查、
  今日 token 预算（`usage_ledger.today_tokens + 15k 预估`）、24h 次数上限。
  状态文件按 pet_id 分份（`pet_runtime.<id>.json`）。
- **执行**：`build_dsh_runtime()` 生成 `dsh-runtime.patch.yml`（宠物人格
  前缀 + 禁用 coding/规划类工具），以 `--patch` 传入；Key 只走子进程
  `DEEPSEEK_API_KEY`，结果 stderr/异常统一脱敏。
- **界面**：设置页新增「执行内核」页（路径/预算/次数/超时/权限模式 +
  运行/查看日志按钮）；右键「互动」与「数据」菜单各加入口。
- **事件映射**：`RuntimeResult.events` 的 `tool_call` 转 `tooldetail.detail`
  写入生活日志；运行前/完成后写 `WorkStatusMachine.set_external`
  （working → success/error）；完成后 `_notify_reply` 发系统通知。
- **生活日志**：`pet/life_log.py` JSONL 追加，字段截断；`LifeLogDialog`
  只读查看最近 100 条。
- **成本入账**：`normalize_usage()` 把 DSH camelCase 翻译成 pricing/ledger
  的 snake_case；完成后 `_record_tokens` + `_last_turn_cost` 复用既有账本。
- **线程**：worker 只跑 subprocess；所有 Qt 操作用 `_ui_call` 回 GUI 线程；
  结果走既有 `_chat_queue` 出口。零改动 chat/heartbeat 主链路。

## Impact

- 新增 `pet/runtime_manager.py`、`pet/life_log.py`、`pet/runtime_ui.py`。
- 修改 `pet/config.py`（runtime 段）、`pet/settings_ui.py`（执行内核页）、
  `pet/menus.py`（两个入口）、`桌宠.py`（挂 RuntimeMixin）、`smoke_check.py`。
- 默认关闭；不启用时行为与之前完全一致。
- OpenClaw：本版不接。裁决理由：Gateway + channels 形态、Node 24 PATH
  阻塞、没有 headless 单任务入口；需要多平台消息通道时再评估 sidecar。

## Risks

- **DSH 首跑**：首次会建 profile，可能超过 300s；超时按失败处理，下一次
  重试。用户需在设置页填对 Node 24 与 `bin.js` 路径。
- **工具 ID 漂移**：patch 里的 `tool-*` 行随上游版本变化；未知 ID 可能被
  忽略或报错。DSH 失败会原样回到用户，不会静默成功。
- **预算预估**：15k 是 trivial 基线；带多轮工具的任务可能更高。日预算
  按「已用 + 预估」预检，事后按实际入账；不保证单次不超预算。
- **线程/密钥**：worker 禁止直触 Qt；Key 只在子进程 env，生活日志不落。
- **9GB 安装**：本版不随桌宠分发；用户按需本地安装，设置页只存路径。

## Verification

- 冒烟新增：配置段归一/非法回落、预算与 24h 次数门禁、usage 翻译、
  life_log 读写/截断、DSH patch 生成、完成后入账/日志/上屏；全量回归全绿。
- 实测：TASK-015 的 DSH headless 真任务（14,175 tokens / 52.9s）与工具
  事件已落 `docs/spikes/2026-11-10-agent-runtime-probe.md`。
- **生产 patch 复测**：同一 trivial 任务在「宠物人格 + 禁用 coding/规划
  工具」profile 下为 **1,938 tokens / 4.1s**（未裁剪 14,175 / 52.9s）。
  `--dump-config` 已确认 personaPrefix 被替换、tool-pwsh/tool-bash/tool-jobs
  被禁用、tool-web 保留。主要成本来自工具 schema，裁剪是最大杠杆。
