# ADR-2026-11-11-01: 工具按需加载 + 记忆只留摘要

- 状态：已接受
- 日期：2026-11-11
- 关联任务：ONES TASK-027（用户指令「工具做成按需调用，工具清单占得太多了。核心留几个常驻，其余按需加载。记忆只留摘要，细节靠搜」）
- 取代关系：部分取代 ADR-2026-11-10-03（记忆 top-K 全文注入 → 摘要注入；`_recall_facts` 下线，`memory_recall` 改供检索排序）

## Context

33 份工具 schema 每跳全量发送，光工具清单就几千 token；而一轮对话里
文件/截图/外包/窗口这类工具多数轮次根本用不到。记忆侧每轮注入相关度
top-8 事实全文，facts 涨到 200 后是固定且随库增长的成本。

用户要的方向明确：核心工具常驻，其余按需加载；记忆只留摘要，细节靠搜。
目标：每跳 token 成本大幅下降，且她自己的能力感不缩水。

## Options Considered

**A. 只压缩工具描述文本。** 改动最小，但 schema 数量没变，模型侧
「工具过多」的选择干扰还在。否决——治标。

**B. 全量工具改单个 `use_tool(name, args)` 包装工具。** schema 只剩
一份，但模型必须凭一句话目录盲填参数，错误率上升（尤其是
manage_schedule 这种多参数工具）。否决。

**C. 核心常驻 + `activate_tools` 元工具按需激活（已选）。** 目录写在
activate_tools 的 description 里（由 EXTENDED_TOOLS 描述首句自动派生，
不会与实现脱节）；模型先激活、下一跳请求才带真实 schema，参数有完整
schema 可依。激活花一跳——用热度环对冲（见下）。

**D. 记忆摘要 + search_memories 细节通道（已选）。** 替代方案是
「facts 少于 N 条时仍全文注入」的分叉逻辑，否决：两种模式让模型行为
不可预期，摘要+搜索一条规则更稳。

## Decision

### 1. 工具分两层（pet/harness.py）

- `CORE_TOOLS` 常驻 8 个：`activate_tools`（激活入口）、
  `search_memories`/`remember_fact`/`forget_fact`（记忆三件套——摘要时代
  的细节通道与写入）、表情四件套（她最常用的情绪表达面）。
- `EXTENDED_TOOLS` 按需 25 个：文件/联网/动作/定时/心跳/目标/感知/行动/
  截图/命令/自我/自查/外包/能力清单/manage_memory。
- `TOOLS` 保留为两者并集（兼容既有直接调用方）。
- `run_turn` 每跳传 `_tools_for(active)`；`execute_tool` 对未激活的
  扩展工具返回可读拒绝（「先 activate_tools 激活它，下一跳就能用」）。
  不带 `active` 键的内部调用（测试等）放行，不误伤。
- 最后一跳仍去掉全部工具逼正文（既有行为不变）。

### 2. 热度环：常用按需工具自动常驻

- `tool_usage.json`（app_dir，gitignore + 写保护名单）存最近 50 次
  按需工具调用；出现 ≥3 次且取 top-4 的工具，之后每轮自动进
  `state["active"]`——表情/日程这类常用的不必每轮花一跳激活。
- 写失败静默（热度只是优化，不影响工具可用性）；上限有界
  （核心 8 + 热度 ≤4）。

### 3. 记忆摘要（pet/memory.py）

- `summarize(facts, meta)` 纯函数：一行=总数（长期/临时分类）+
  最常想起 top3（hits>0）+ 最近记下 top3（ts）。空库返回空串。
- `_memory_summary()` mixin 方法：快照 facts+meta 前顺带跑一次
  `_expire_temp_facts`（沿用以前 `_recall_facts` 每轮一次的节奏）。
- 注入点改摘要：`chat._build_system_prompt` 与
  `heartbeat_prompt.build_heartbeat_system`（签名第 4 参
  `facts` 列表 → `memory_line` 字符串）。摘要尾注明「只是线索不是
  全文，细节用 search_memories 按关键词搜，别猜」。
- `_recall_facts`/`_recent_context_text` 下线（提示词不再全文注入）。

### 4. search_memories 升级为细节通道

- 关键词命中按 `memory_recall.score_facts` 相关度排序（不再是入库
  顺序）；返回条数走 `memoryRecallK`（设置页标签改「记忆检索条数」）。
- **命中回写 hits/last_access**：临时→长期晋升的证据源以前靠每轮
  注入白拿，现在摘要不灌全文，改由检索主动记——晋升信号不断档，
  且从「被动曝光」变成「她真的去查了」，语义更实。

### 5. 文案同步

`HARNESS_HINT`（chat.py）、`TOOLS_HINT`（heartbeat_prompt.py）、
`list_capabilities` 清单都改写为「核心常驻 + activate_tools 按需」的
说明；capabilities 第 3 项注明「提示词只放摘要，细节用 search_memories 搜」。

## Impact

- 每跳请求 schema 数：33 → 8（热度满配 12）；提示词记忆段：top-8 全文
  → 一行摘要（且不随 facts 增长而膨胀）。
- `pet_memory.json` 结构不变；`memoryRecallK` 语义从「提示词注入条数」
  变为「检索返回条数」，默认值与范围不变。
- 冒烟 242 → 246（+4 新检查：分层与激活链路、热度环、summarize、
  检索排序与回写；3 旧检查改写：top-K 注入、召回回写、heartbeat
  prompt 签名）。

## Risks

- **激活多花一跳**：MAX_HOPS=4 下「激活→用→收尾」仍宽裕；热度环把
  常用工具挡在常驻侧。若实测激活频繁，可调大 HOT_TOP 或把某个按需
  工具提核心。
- **模型不知道该激活什么**：目录在 activate_tools 描述里（一句话一个
  工具），HARNESS_HINT 也写明机制；`list_capabilities` 仍是全量清单。
- **摘要让她「忘了」不重要的事实**：摘要含最常想起+最近记下，且
  search_memories 是零激活成本的核心工具；这与用户「细节靠搜」的
  明确指令一致，若实测她频繁空搜再回调 K。
- **hits 信号变稀疏**：晋升 hits≥3 现在只来自检索与重复记录（主动），
  不再有每轮被动注入；跨 12h 内 3 次检索是合理阈值，冒烟有回写用例
  钉住。
