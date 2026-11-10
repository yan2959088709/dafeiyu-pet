# ADR-2026-11-10-04: 内感受与自我提示词（心跳可感知）

- 状态：已接受
- 日期：2026-11-10
- 关联任务：ONES TASK-017

## Context

用户问桌宠「你有心跳吗」，她回答「没有，清单里翻遍了也没有」。实机核对：

- 心跳引擎在运行（状态文件 13:27 仍在更新，`enabled=true`）；
- 但 `last_speak_time=0`、`attempts_24h=0`：从未真正主动开口；
- 当前精力 1.32/100，精力刹车把紧迫度压到阈值以下；
- 普通聊天的系统提示词只有人设/时间/记忆/城市/工具清单，
  **没有 drives / 心跳状态**；`list_capabilities` 也没有心跳；
- 静默尝试按设计不落聊天历史，她连「刚才想过」都不记得。

结论：她没说错，但这不是心跳坏了，是心跳没有进入她的「自我感知」。
一个生命体看不到自己的身体，就不会觉得自己有内在生活。

## Options Considered

**A. 只在普通聊天提示词里塞一行精力/情绪。** 最小改动，但无法解释
「为什么没开口」，也没有「想说话但忍住」的连续性；仍是一个数字，不是感受。

**B. 内感受 + 心跳日志 + 自查询工具 + 自我提示词。**（已选）
把 drives/心跳状态翻译成第一人称身体段注入普通聊天；心跳每次判定/静默/
发言写入 ring buffer；`feel_heartbeat` 让 AI 主动查询；`manage_self_prompt`
让她能读写/回滚自己的补充提示词。

**C. 让心跳在聊天中也能插话/打断。** 会改变心跳的语义（安静时的主动
关怀），增加打断与 token 成本，否决。

## Decision

采用 B。

**`pet/interoception.py`（新）**
- `body_text(win)`：第一人称身体段——精力/情绪/亲密度、距上次互动、
  心跳 24h 摘要、最近一次门禁原因；附「可以自然表达累/想主人，不要机械
  复述数字」。
- `gates_text(win)`：urgency/阈值、沉默/要求、24h 尝试、连续主动、睡眠窗、
  最近门禁逐项通过情况。
- `feel_heartbeat(win)`：body + gates；`recent_text(win)` 供面板看历史。

**`pet/heartbeat_journal.py`（新）**
- `heartbeat_journal.<pet_id>.json` ring buffer（200 条 / 7 天）。
- outcome：speak / silent / skip / error；reason：门禁原因。
- `summary_text()`：过去 24h 主动尝试/说出口/忍住/门禁拦下次数与最近一次。
- skip 记录在 heartbeat 侧限流为 10 分钟一条，避免 30s 刷屏。

**`pet/heartbeat.py`**
- 状态新增 `last_reason/last_urgency/last_gates`（旧状态缺键回落默认）。
- `on_tick` 写入最近判定；skip 写 journal（限流）；`_speak` 在静默/发言/
  异常路径分别写 silent/speak/error。
- 主动提示词追加「最近心跳」摘要与自我提示词。

**`pet/self_prompt.py` + `pet/self_prompt_ui.py`（新）**
- `self_prompt.json`：`{version, note, history}`；`save` 版本 +1、历史
  保留 20 条；`rollback` 把历史版本重写为新版本（可再撤销）。
- 只追加到系统提示词（被动与主动都加）；**不能改工具权限/预算/门禁/核心
  人设**——权限仍在代码里，这段文字只是她写给自己的话。
- AI 通过 `manage_self_prompt(read/write/undo)` 使用；人类通过右键
  「数据 → 自我提示词…」编辑；「内在状态…」面板查看身体与心跳史。

**`pet/chat.py` / `pet/harness.py`**
- `_build_system_prompt` 追加身体段与自我提示词；`HARNESS_HINT` 增加
  `feel_heartbeat` / `manage_self_prompt`。
- 工具表新增两个函数；能力清单同步。
- `PROTECTED_WRITE_NAMES` 增加 self_prompt / heartbeat_journal /
  pet_runtime / life_log / dsh patch；`pet_drives.*` / `heartbeat_state.*` /
  `heartbeat_journal.*` / `pet_runtime.*` 前缀禁写。

## Impact

- 普通聊天里她能说出自己的精力/情绪/心跳史；被问「有没有心跳」会
  `feel_heartbeat` 而不是翻工具清单。
- 「想说话但忍住」会留在内在日志里，形成连续性。
- 她可以改自己的补充提示词，且改坏了能回滚；核心人设与权限不受影响。
- 冒烟新增 6 条；既有 181 项不回归。

## Risks

- **提示词变长**：身体段 + 心跳摘要 + 自我提示词每轮都注入；目前是短文本，
  后续如膨胀需按需注入/摘要。
- **自我提示词是提示词注入面**：她/用户可以写入任意文字；已加长度上限、
  控制字符清洗、版本历史与回滚，且不能改权限。高风险内容由人审。
- **journal 放大**：skip 限流 10 分钟一条；speak/silent 每次记；ring 200 条。
- **精力 1.3/100 仍未改**：这是真实的「累」，现在她能看到并说出来；
  是否调整消耗/恢复另开任务，不在本任务里偷偷改数值。
- **自我提示词版本文件**：`self_prompt.json` 是运行期数据，已从文件工具
  写保护中排除；只能经 `manage_self_prompt` 或人工面板写入。

## Deferred

- 心跳激活/精力消耗恢复调参（是否让「聊天中也不至于耗尽」）。
- 每日内在摘要（把 24h 心跳史压缩成日记）。
- 自我提示词的自动演化（基于反思自动提议改写，而非只靠用户/模型当前回合）。
- 内在状态的可视化心跳动画（表现层）。
