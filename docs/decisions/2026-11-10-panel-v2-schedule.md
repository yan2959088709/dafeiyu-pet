# ADR-2026-11-10-06: 桌宠面板 v2（快速面板 + 内容调度）

- 状态：已接受
- 日期：2026-11-10
- 关联任务：ONES TASK-019

## Context

控制台此前是 5 页（主窗口/布局/动作/设置/管理），主窗口只有形象预览与
固定按钮；用户想安排「几点说什么/提醒什么」只能手改代码或聊天记录，世界
不可编辑。用户要求：

1. 一个简易但俱全的快速面板（状态 + 快捷动作 + 今日调度）；
2. 在设计内提供详细、可编辑的内容调度。

这也是 LifeLoop 的界面雏形：用户显式安排的内容，是「她的一天」最外层
的骨架；之后自主意图再填进空隙。

## Options Considered

**A. 只做只读展示。** 简单，但用户仍无法安排任何内容。否决。

**B. 只做表格编辑，不执行。** 编辑完不生效，等于配置摆设。否决。

**C. 快速面板 + 可编辑调度 + 30s 执行器。**（已选）
主页汇总生命状态与今日调度；新增第 6 页「内容调度」；调度器真正触发
say/reminder/care，并写生活日志。

**D. 直接上完整 LifeLoop 意图引擎。** 方向正确，但调度是用户显式安排，
与自主意图是两层；先把用户可编排的一层做稳，LifeLoop 再接自主层。

## Decision

采用 C。

**主页快速面板**
- 生命状态：精力/情绪/亲密度/心跳原因/上次主动；数据来自
  `interoception.short_status`（不拼提示词、不读日志文件）。
- 快捷动作：聊天、内在状态、自我提示词、生活日志、重型任务、内容调度、
  全部设置。
- 今日调度摘要：`schedule_store.summary_text` 显示启用条数与下一条；
  每 8 秒刷新一次，避免 800ms 定时器频繁读盘。

**内容调度数据层 `pet/schedule_store.py`**
- `schedule.json`（运行期数据，file 工具禁写）：条目含
  `enabled/time/days/kind/content/last_fired/last_result`。
- 到期判定：星期命中、时间窗口 90 秒、当天未触发；每天最多一次。
- `next_entries` 给摘要；`save` 从磁盘保留 `last_fired/last_result`，
  防止编辑期间丢触发状态。

**执行器 `pet/schedule_runner.py`**
- 30 秒一拍（接在既有 `_whisper_tick` 上）；`scheduleEnabled` 总开关。
- say → 气泡；reminder → 气泡+系统通知；care → 调模型（`_chat_once`）
  生成一句关怀，太累（精力 < tired_threshold）时跳过。
- 不受心跳安睡窗/冷却限制（用户显式安排优先），但 care 仍受精力门禁。
- 每 30 分钟扫一次即将到来的条目，往会话里注入一条低优先级「日程提示」
  （assistant 条），让她知道接下来要做什么。
- 所有执行写入生活日志；`schedule.json` 入 gitignore 与写保护。

**编辑器 `pet/schedule_editor.py`**
- 表格（启用/时间/星期/类型/内容/上次执行）+ 新增/编辑/删除/启停/保存/重载。
- 节律与门禁表单：min_silent/min_interval/urgency/max_per_24h/
  max_consecutive/tired_threshold/energy cost/recovery/quiet window；
  保存后热更新 `_heartbeat.cfg` 与 `_drives.cfg`。
- 总开关 `scheduleEnabled`；右键「数据 → 内容调度…」也可以独立打开。

**控制台**
- 导航从 5 页变 6 页（主窗口/界面布局/动作绑定/内容调度/设置/管理）；
  旧 5 项 `ui_layout` nav 自动在槽位插入「内容调度」，避免页名错位。
- 调度页懒加载（与布局/动作编辑器一致），首次切过去才构造。

## Impact

- 用户可以在面板里安排「每天 08:30 说早安」「21:00 提醒休息」等，30 秒内生效。
- 节律/门禁参数可视化可编辑，保存即热更新；不再需要手改 config.json。
- 主页一眼看到精力/心跳原因与下一条日程。
- 冒烟新增 3 条 schedule 用例，更新 console/panel 页码断言；全量回归保持全绿。

## Risks

- **care 不记 token 账本**：`_chat_once` 不返回 usage；目前只扣 1 精力，
  真实 token 成本按供应商账单走。后续接 harness/usage 时补齐。
- **调度是「用户显式安排」**：不遵守安睡窗；用户排 03:00 就会在 03:00 说话。
  这是刻意取舍，避免「排了但不响」。
- **触发窗口 90 秒**：应用在计划时间后 90 秒内没启动/没 tick，会错过当天；
  这是为了防止启动后补发一堆过期消息。
- **care 与被动对话并发**：走 `_chat_queue` 既有出口；若用户正在流式对话，
  可能排队/插入，沿用现有队列语义。
- **schedule.json 并发写**：编辑器保存与执行器 mark_fired 都是小文件原子写；
  `save` 保留 last_fired 缓解编辑覆盖，但极端并发仍以最后写者为准。

## Deferred

- care 的 token 入账与预算门禁（复用 usage_ledger/energy）。
- 一次性/间隔/每月等更复杂的重复规则。
- 调度条目拖拽排序、模板库、导入导出。
- LifeLoop 自主意图接入（调度是用户层，自主层另做）。
