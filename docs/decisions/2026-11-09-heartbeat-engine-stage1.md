# ADR-2026-11-09-01: Stage 1 心跳引擎 + 驱动状态机（自主循环/内在驱动/主动说话）

- 状态：已接受
- 日期：2026-11-09
- 关联任务：ONES TASK-010（TASK-009 Phase 3 第一步；TASK-011 承接）

## Context

dafeiyu-pet 此前是「LLM + prompt harness + 无管理记忆」的被动对话玩具：AI 只在
主人开口后应答，无内在状态，也没有「自己想说话」的出口。Stage 1 先交付最小可
评估切片——驱动状态机（mood/energy/affinity）+ 心跳门禁链 + 主动说话链路，
把桌宠改造为自主智能体（电子生命），是 ONES TASK-009 Phase 3 路线的第一步；
记忆生命周期与人格进化留 Stage 2+。合同源为 2026-11-09 设计对话蓝图；算法参考
Shikigami-Protocol（ASE，AGPL-3.0），仓库公开，「只搬算法不拷码」是红线。

## Options Considered

**A. Fork Shikigami-Protocol 整体换栈。** 功能最全，但 AGPL-3.0 与公开仓库
冲突，且要放弃 PySide6 壳与既有资产（harness/表情 FC/HTTP 服务）。

**B. 从通用 agent 框架（OpenClaw 等）拆模块。** 心跳/记忆长在内核、与 agent
loop 深耦合；「单窗单宠 + 本地 Qt」对不上框架假设，适配成本高、引入新依赖。

**C. 移植算法到新黑盒模块，壳与资产全复用。**（已选）状态机/门禁/提示词重写
为三个纯逻辑模块，只接既有出口：无新依赖、可单测、可回退；架构收益留 Stage 2+。

**D. 组件拼装（Headlong/jiwen/omemo 等）。** 每库解决一环，但接口/线程/
人格模型两两不合，胶水量最大，许可证逐个审计，收益不确定。

## Decision

采用 C。三个新模块全部纯逻辑（不碰 Qt、不联网），Qt 侧只借窗口既有出口：

- **`pet/drives.py`**：`DrivesEngine`——三轴状态（mood+intensity / energy 0-100 /
  affinity 0-100）的衰减/恢复/消耗 + `urgency()` 0..1 紧迫度（0.45 沉默 +
  0.25 时段 + 0.20 想念，精力刹车）；`pet_drives[.pet_id].json` 原子落盘；
  数值全钳位，非法配置 warn 不崩（手改 config 最坏只是规则失效）。
- **`pet/heartbeat.py`**：`HeartbeatEngine`——`on_tick`（GUI 线程一拍）+
  `decide()` 纯函数六门禁（安静窗 → urgency≥0.35 → 沉默≥600s → 加权冷却 →
  24h≤15 次尝试 → 连续≤2）+ `_speak`（daemon worker 只包 LLM 调用）+
  `is_silence_reply`（容引号/全角/标点变体）。
- **`pet/heartbeat_prompt.py`**：`build_heartbeat_system`——段式装配（人设/
  时间线/记忆/城市 + 指令式状态段 + 主动场景 + 静默选项说明 + 避复述 hint）。

核心经济原则：**门禁全 Python 零 token，LLM 仅在六门禁全过时调用**。关键不变量：

1. **衰减基准 = `last_user_message_time`**：`last_decay_time` 只是增量结算
   游标（只前进，回拨不二次结算）；拿 last_updated 之类当基准会让长期沉默
   被误判成刚互动、主动搭话永不触发（判别力用例守这条）。
2. **「（静默）」= 这轮没发生**：不存储/不显示/不计成功配额/不更新连续计数，
   仅推进 `last_attempt_time` 并计入 `attempts_24h`（静默轮与多跳工具轮同样
   打了请求，只记成功发言 15/天实际放到 ~60），防一拍接一拍重复触发。
3. **两级线程模型**：tick 永在 GUI 线程（QTimer 跨线程 start 静默失效，L5
   教训）；worker 只包 LLM，回屏走既有 `_chat_queue`（GUI 线程消费端渲染），
   禁止直触 Qt；`_hb_busy` 防重入，finally 与 start() 失败处都复位。
4. **历史身份不变式（承 TASK-008）**：`chat_history is 会话 messages` 不动——
   主动发言只追加 assistant 条、走既有 append+_persist 路径、sid 快照防
   迟到回复写错会话；主动 user 锚点（HB_NUDGE）只进请求 messages 不落盘。
5. **精力扣除唯一计费点在 `_chat_out`**：worker 不再 apply_ai_reply（否则双扣 -4）。

最大化复用既有资产：`harness.run_turn`（FC 表情可用）/ `_chat_out` 出口 /
`_whisper_timer` 30s 一拍 / whisper fallback（chat.py 委托，桌宠.py 无改动）。

## Impact

- 新增运行时状态文件 `pet_drives.<pet_id>.json` / `heartbeat_state.<pet_id>.json`
  （已入 .gitignore）。
- config 增 `heartbeat` 段（DEFAULT_CFG 单一事实来源 + validate 强布尔，非
  布尔 fail-closed 按关闭处理），默认 `enabled=true` 升级即生效，关掉即回退
  legacy whisper（原路径不动）。
- 桌宠行为变化：安静 10 分钟后可能主动搭话（默认每天最多 15 次尝试、连续
  最多 2 次、23:00-06:00 静默窗）；冒烟 119 → 153 用例，最终 153/153 通过
  （另有 1 项已知偶发 flaky 用例，与心跳域无共享路径，登记于项目教训 L8）。

## Risks

- **token 成本**：三道闸——门禁全 Python 零 token + 24h 尝试配额 + attempts
  记账；传输失败也记 attempt，否则持续 5xx/过期 key 时 ~120 次/小时重试。
- **多宠隔离**：状态按 pet_id 分文件——`state_file_name` 字符净化 + 原始 id
  的 sha1 短摘要（保单射：main/main./" main "/Main/全非 ASCII id 互不碰撞），
  取不到 id 退回单例共享；配额/冷却/连发按宠独立计，否则多宠乒乓覆盖、
  配额打穿。
- **线程模型**：tick 永在 GUI 线程、worker 只包 LLM、回屏走 `_chat_queue` 既有
  队列；异常路径必须推进 attempt 并落盘（H-1：曾致四道门禁整体失效）。
- **AGPL 出处**：算法重写不拷码，架构门扫描最大相同 2 行（样板级）。
- **已知限制**：情绪仍是关键词粗标（LLM 分类留 Stage 2+）；前拨时钟回拨后
  靠时间戳双端钳恢复；urgency 的 affinity/100 归一化为已批准偏离（N9）。

## 验证摘要

- 冒烟 150/150（假 transport 只桩网络出口）：urgency 跳转/放行/加权冷却/24h
  配额/静默回复/历史只落 assistant；现有用例不回归。
- Gate 0 架构 PASS（9 项硬 criterion；AGPL 扫描最大相同 2 行样板级）；Gate 2
  安全 FAIL → 修正轮 3 → 复审 PASS：H-1（异常路径四闸门失效）闭环，M-1 静默/
  多跳轮计费、M-2 运行期热开关、M-3 多宠覆盖同步修复。
- Gate 3.5 对抗 12 探针矩阵幸存：门禁边界零 off-by-one、时钟滥用、状态文件
  滥用、重入（20 连 tick 仅 1 次调用）、提示词构建、输入滥用全部通过；真机
  沙箱演示一句在角色主动发言经既有出口上屏。

## Deferred（Stage 2+）

主动发言 token 入账本；多宠引擎重绑；`_persist` 提取；记忆生命周期（相关度
检索/每日摘要/巩固遗忘）；LLM 情绪分类替换关键词粗标；反思引擎与人格进化；
学习式睡眠窗口（TASK-011，替换硬编码 quiet_window）。
