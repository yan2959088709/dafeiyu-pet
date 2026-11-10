# ONES — dafeiyu-pet 修复轨道

> 审查报告（2026-11-06，4 代理并行 + 主会话复核）即本轨道契约源。
> P0 轮范围 = 8 项 P0 + 2 项一行级 P1，已全部交付并提交（commit 76e928c）。
> 测试入口：`tools/smoke_check.py`（104 → 111（P0 轮）→ 119（Harness 轮，含 8 条 harness 用例）→ 153（心跳轮 TASK-010，含 1 项已知偶发 flaky 见教训 L8）→ 157（睡眠窗轮 TASK-011）→ 162（token 入账轮 TASK-012）→ 168（Phase 2 工具轮 TASK-009）全绿）。

### TASK-001: P0 一键修复组（退出崩溃/切皮肤冻死/首启漂移/置顶抹穿透/APPDATA）
- **Executor**: claude
- **Status**: [x]
- **Dependencies**: none
- **Contract**: 审查报告 H2/H4/H8/M2/M8
- **Acceptance**:
  1. `quit_app` 在 tray 为 None 时不抛异常 ✓
  2. `_bind_skin` 后 `_work_anim/_work_sig/_work_text` 已清空 ✓（冒烟用例覆盖）
  3. 首启落盘的 config.json 不含 `pets` 键 ✓（冒烟用例覆盖）
  4. `set_topmost` 后穿透状态保留 ✓
  5. `set_autostart` 不缺 APPDATA 时崩菜单回调 ✓（顺带改为失败不落盘）
- **Handoff log**:
  > claude [x] 2026-11-06: done. commit 76e928c. 另按复审意见将 clear_work() 移到 load_skin 之前（is_turn_anim 用旧皮肤判定）。

### TASK-002: H3 凭据解密链路修复
- **Executor**: claude
- **Status**: [x]
- **Dependencies**: none
- **Contract**: 审查报告 H3
- **Acceptance**:
  1. `_api_reload` 后 `cfg["ds_api_key"]` 为明文 ✓
  2. `protect()` 对已加密文本幂等 ✓（冒烟用例覆盖）
  3. PetService 的 cfg_provider 返回解密后配置 ✓
  4.（门禁追加）config_get 脱敏 + config_put 跳过掩码值 ✓（线上 API 实测 + 冒烟用例）
- **Handoff log**:
  > claude [x] 2026-11-06: done. commit 76e928c。线上实测：PUT 探针 Key→GET 返回 ***→落盘 dpapi: 密文→清除无残留。

### TASK-003: H5 DELETE /chat 空 ts 清空防护
- **Executor**: claude
- **Status**: [x]
- **Dependencies**: none
- **Contract**: 审查报告 H5
- **Acceptance**:
  1. `_api_chat_delete(stamps=None)` 返回 ok=False 且不动会话数据 ✓（线上实测 + 冒烟用例）
  2. 本地对话框路径（chat_ui，恒有 stamps）行为不变 ✓
- **Handoff log**:
  > claude [x] 2026-11-06: done. commit 76e928c。

### TASK-004: H6 碎碎念移出 Qt 主线程
- **Executor**: claude
- **Status**: [x]
- **Dependencies**: none
- **Contract**: 审查报告 H6
- **Acceptance**:
  1. `_whisper_tick` 不再于 GUI 线程同步请求（工作线程 + busy 闸门）✓
  2. 显示动作（say/表情/气泡）回落 GUI 线程执行 ✓
  3. `_whisper_generate` 钩子路径（HTTP 线程调用）同样正确 ✓
  4.（门禁追加）工作线程异常入 pet_log、Thread.start 失败复位 busy ✓
- **Handoff log**:
  > claude [x] 2026-11-06: done. commit 76e928c。冒烟 whisper 用例改异步驱动（泵事件+等 busy）。

### TASK-005: H7 睡眠态闸门 + dur 防御
- **Executor**: claude
- **Status**: [x]
- **Dependencies**: none
- **Contract**: 审查报告 H7 / 动作系统 M5
- **Acceptance**:
  1. `_sleeping` 时低于点击级动作只排队不覆盖睡眠 ✓（冒烟用例覆盖，含判别力构造）
  2. 点击级动作先唤醒再播放 ✓
  3. `_start_emote` 对 dur<=0/非数字有防御 ✓
- **Handoff log**:
  > claude [x] 2026-11-06: done. commit 76e928c。复审发现的用例判别力缺口已补（_end_emote 构造 _sleeping=True/emote=None 危险态）。

### TASK-006: H1 HTTP 钩子 GUI 线程封送
- **Executor**: claude
- **Status**: [x]
- **Dependencies**: none
- **Contract**: 审查报告 H1
- **Acceptance**:
  1. 新增 `_ui_call` 封送：非 GUI 线程的窗口操作回主线程执行并带回返回值 ✓（冒烟用例三向断言）
  2. `_api_hooks` 中 Qt 触达钩子全部经封送（13 个，含复审补的 roster）；whisper 不封送 ✓
  3. `_throw` 经接口触发后飞行定时器在 GUI 线程正常走时 ✓（线上实测：无 startTimer 告警、抛出后正常落位）
  4.（门禁追加）超时 cancelled 迟滞取消、Exception 不再吞 BaseException、桥缺失 warn ✓
- **Handoff log**:
  > claude [x] 2026-11-06: done. commit 76e928c。ADR: docs/decisions/2026-11-06-http-hooks-gui-dispatch.md。

### TASK-007: P1 遗留项 + 复审 Low 登记
- **Executor**: claude
- **Status**: [ ]
- **Dependencies**: none
- **Contract**: 审查报告 M1/M3/M4/M5/M6/M7/M8/M9 + 低危清单 + 复审新登记
- **Acceptance**: 逐项修复后勾销
- **Handoff log**:
  > claude [ ] 2026-11-06: 登记待办（按优先级）：
  > 【设计类】M1 穿透+无托盘失联（建议全局热键或默认改余量区穿透）；M3 多宠 cfg/记忆细粒度合并（保存时读盘合并自己 id 行）；M4 JSON 原子写（memory/sessions/ledger 换 storage.write_json_atomic）；M5 会话代号（gen/sid 快照防迟到回复写错会话）；M6 错误路径用户消息入历史；M7 chatModel 接上主链路（_call_ds 用 model_select 候选 + calc_turn_cost 同源）；M8 历史写入补 ts（否则按轮删除永远命中不了）；M9 设置页校验前移（_collect 深拷贝）。
  > 【低危】print 接 pet_log（pythonw 静默失败面）；QMovie stop + PetWindow.closeEvent；死配置项清理（confineToScreen/show_balance_badge/bubble_close_sec/trayEnabled/peak_mode）；attachments LRU；pet/log.py flush 入锁 + close join；桌宠.spec 条件 datas；启动 bat 转 CRLF；README 与实现漂移；sfx_enabled 默认值统一；_UiCall cancelled 残余竞态（复审#2）；dur 归一化提前到分支前（复审#5）；whisper daemon 闭包持 self 的生命周期注释（复审#6）。
  > 【范围外】sync_default() 把含 pets 的 DEFAULT_CFG 写进 config.jsonc，而 pets_declared() 也读 jsonc——用户手动同步高级模板会被拉进多宠模式，且 save_config 会持续把 pets 带回（漂移黏性）。建议 sync_default 同样剔 pets。
  >
  > claude [~] 2026-11-06（晚间，聊天 UX 修复轮，用户口述需求驱动，未走 /full-auto）：M6 已修（worker 请求前先落 user 历史，超时/报错路径重载不丢）；M5 的 sid 快照部分修（_call_ds 开头捕获 sid + hist 原地写回，切会话竞态关闭，gen 代号仍未做）。同轮修复：消息丢失三处（_on_submit 先清后发→成功才清、busy 丢弃→_pending_msgs 排队上限 3 + tick 续发、_call_ds 返回值透传）；图文语义分流（附件→[图片]、纯表情→[表情]、AI 收到 [表情]）；附件缩略图（历史存 attachment id，_reload_panel 渲染）；表情输入改 chip（输入框不裸露原始标记，单选替换）。冒烟 111/111 + 无头 22 项 + 沙箱真实端到端（文字+表情+图片一次发送，AI 回复"收下你的OK"）全过。未提交。
  >
  > claude [~] 2026-11-06（深夜，表情包整理轮）：memes 目录断链修复——27 个 PNG 从 sprites/dsh-pet/upstream/memes/ 归拢到 <root>/memes/，config.memes 字典 6→27 条（21 条描述按实际画面编写），chatImageLimit 10→30。实测池子 27/27、真实 API 选图（"累死了"→[图:死掉了] 语义命中）。未提交。

### TASK-008: 聊天 Harness 化 Phase 1（Agent Loop + 表情 FC + 按需召回）
- **Executor**: claude
- **Status**: [x]
- **Dependencies**: none
- **Contract**: 2026-11-06 深夜设计对话（用户批准「批准并执行」）；前置实测：thinking=enabled 与 FC 在 deepseek-v4-flash 上可共存（reasoning_content + tool_calls 同返回，finish_reason=tool_calls）
- **Acceptance**:
  1. `pet/harness.py`：≤3 跳 agent loop；transport 可注入（测试用假 transport）；thinking=enabled；max_tokens 取 cfg.aiMaxTokens（默认 1024，废除 120 小参数与 90 字硬截断）✓
  2. 四工具：search_memes / send_meme / search_stickers / send_sticker；本地模糊检索（memes=name+desc，stickers=GROUPS 情绪组+标签子串）；send_* 只记意图、每类上限 1 ✓
  3. `_build_system_prompt` 移除表情目录两块（每轮 ~120 行 → 0 行，实测 557 字符）；`<sticker:>`/`[图:]` 标记协议降级为 fallback 仍生效 ✓
  4. 意图随最终回复走既有 `_chat_out` 渲染（Qt 全在主线程）；历史仍只落 user+assistant（tool 消息不落盘）✓
  5. 冒烟增补：假 transport 两跳链路断言（messages 组装/历史/意图传递）+ search 命中未命中 + fallback ✓（111 → 119，8 条 harness 用例）
- **Handoff log**:
  > claude [~] 2026-11-06: 用户约束——Harness 必须带思考（thinking=enabled 已验证）；AI 参数不再抠小（max_tokens 1024、废除 90 字截断）。范围仅 Phase 1；Phase 2（remember_fact/query_balance/play_animation 收编本地技能）与 Phase 3（mood/energy 状态机自主发起）登记 TASK-009。whisper 保持客户端随机选图不 FC（单句生成不值得两跳）。
  >
  > claude [Gate-Fail] 2026-11-06: 门禁双代理（质量+安全）各 1 High/多 Medium。H1：_persist 快照回填会复活被清空/删除的会话（新引入回归）——根因是 `_api_chat_delete`/`_chat_out` 用切片重绑破坏了「chat_history 就是会话 messages」身份；连同 M1 usage 少记、M2 HTTP 错误文案退化、M3 线程启动失败卡 busy、M4 finish_reason 丢弃、安全侧成本放大/注入通道/素材白名单/log 脱敏一并修复。
  >
  > claude [~] 2026-11-06: 修复轮完成（harness.py 重写 + chat.py/service_hooks.py 身份不变式 + memes/stickers 入口清洗 + config.validate + log 脱敏 + .gitignore）。冒烟 119/119。
  >
  > claude [Live-Fail→Fix] 2026-11-06: 沙箱真实端到端抓到 DSML 泄漏——思考模型偶尔把工具调用以文本 DSL（`<|DSML| calls> <|DSML| invoke name="send meme">…`）写进 content 而非结构化 tool_calls，原样显示给用户。且**标记内带空格**，从截图抄的无空格用例造成「测试绿、线上失守」。已修：正则容忍空白 + 解析为 tool_calls 续跳 + 残留剥离兜底；用例改用线上真实形态。三连 live 复测干净（5-7s/轮，表情包/小表情均正常上屏）。
  >
  > claude [x] 2026-11-06: 完成。冒烟 119/119（8 条 harness 新用例：两跳链路/跳数兜底/工具边界/检索三分支/全链路+fallback/清空不复活/参数边界/DSML 兜底）；线上实测 FC 全流程（并行 search→send→正文，语义命中）；ADR docs/decisions/2026-11-06-chat-harness-phase1.md。

### TASK-009: Harness Phase 2/3 backlog（自主意识路线图）
- **Executor**: claude
- **Status**: [~]
- **Dependencies**: TASK-008
- **Contract**: 2026-11-06 深夜设计对话分期
- **Acceptance**: 逐项修复后勾销
- **Handoff log**:
  > claude [ ] 2026-11-06: 登记待办：
  > 【Phase 2 工具扩容】remember_fact / query_balance / play_animation / check_time——把现有「本地技能」（balance.py:_local_skill）收编为 FC，AI 自主决定何时查余额/记事/做动作；流式 transport + reasoning_content 实时「思考中」指示；search 升级为标签分类 + embedding（库过百后）。
  > 【Phase 3 自主意识】mood/energy 状态机驱动主动搭话（whisper 调度器升级）；目标队列（待办提醒）；时段策略（夜间静默）；跨会话长期记忆检索（facts 过多时按相关度注入而非全量前 20 条）。
  >
  > claude [~] 2026-11-09: 拆分——Stage 1（drives 状态机 + 心跳门禁 + 主动说话链路）拆为 TASK-010 实施中；M4 学习睡眠窗口拆为 TASK-011 待办。
  > claude [~] 2026-11-09: Stage 1 已交付（TASK-010 [x]，commit 5e6031b，真机演示在角色）。Phase 3 剩余主菜 = Stage 2 记忆生命周期（相关度检索替代全量 facts / 每日摘要 / 巩固遗忘）；Stage 3 = 反思引擎 + Persona Evolution。Phase 2 工具扩容（remember_fact 收编等）建议与 Stage 2 合并排期。TASK-010 延期项见 TASK-012。
  > claude [~partial] 2026-11-10: Phase 2 工具扩容交付并推送（commit f4ee1b9；连同 TASK-010~012 共 7 个提交一并推上 origin/master，此前 6 个任务提交均未推送）。工具面 4→15：表情 4（既有）+ 记忆 3（remember_fact/forget_fact/search_memories）+ 文件 3（list_dir/read_file/write_file，路径沙箱限程序目录、config/会话/记忆/账本等运行期数据文件禁写）+ 联网 2（web_search/web_fetch，仅公网 http/https、内网/环回拒绝）+ 动作 2（list_actions/play_animation）+ list_capabilities；MAX_HOPS 3→4；heartbeat 主动轮同享全部工具（_chat_queue 扩展第 5 位 anim）。另附 QQ 风格聊天气泡面板（桌宠.py：头像/尖角 PNG/时间分隔/多行输入，尖角资源 tools/gen_chat_assets.py 生成）。config 增 aiFileEnabled/aiWebEnabled/aiFileRoot。冒烟 162→168 全绿。**未经 /full-auto 门禁链**——文件写沙箱/联网出口/工具返回值注入是新增攻击面，建议补独立门禁后再重度使用（重点：write_file 沙箱逃逸、web_fetch SSRF 变体、工具结果回灌注入）。Phase 2 剩余：query_balance/check_time 未收编（余额仍走 _local_skill，chat.py:174）；流式 transport 未做。

### TASK-010: Stage 1 心跳引擎 + 驱动状态机（自主循环/内在驱动/主动说话）
- **Executor**: claude
- **Status**: [x]
- **Dependencies**: TASK-008
- **Contract**: 2026-11-09 设计对话蓝图（本条 Handoff log [Blueprint]，用户经 /full-auto「试着实现一下」批准）。算法源：Shikigami-Protocol（AGPL-3.0）——**只搬算法不拷码**；dafeiyu-pet 仓库公开，严禁 AGPL 代码混入。
- **Acceptance**:
  1. `pet/drives.py`：mood/energy/affinity 三轴状态机；时间衰减/恢复/消息消耗；`pet_drives.json` 原子落盘；衰减基准 = `last_user_message_time`（判别力用例：频繁刷 `last_updated` 不阻衰减）
  2. `pet/heartbeat.py`：tick→衰减→urgency（纯 Python 0 token）→门禁链（urgency≥0.35 / 沉默≥600s / 加权冷却 / 24h≤15 / 连续≤2 / busy 闸门 / 23-06 静默）；仅全过才调 LLM
  3. 主动说话全链路：复用 `harness.run_turn`（FC 表情可用）；「（静默）」回复不存储/不显示/不计配额；历史只落 assistant 条（走既有 append+persist 路径，守住 TASK-008「chat_history 即会话 messages」身份不变式 + M5 sid 快照）；出口复用 _chat_out/say/TTS
  4. `pet/heartbeat_prompt.py`：状态段注入（情绪/能量/亲密度，指令式 header）+ 主动场景 hint + 静默选项说明；默认参数 config 化（config.py DEFAULT_CFG 增 heartbeat 段）
  5. 冒烟新增（假 transport，只桩最外层网络出口）：urgency 跳转/放行/加权冷却/24h 配额/静默回复/历史只落 assistant；现有 119 条不回归
- **Handoff log**:
  > claude [~] 2026-11-09: claimed via /full-auto。PRD.md 缺失→合同源 = TASK-009 预登记 + 2026-11-09 设计对话（用户逐节确认后批准实施）。git 注意：pet/chat.py、tools/smoke_check.py、桌宠.py 任务开始前即带未提交历史改动，Gate 5 只暂存本任务文件并显式声明。
  >
  > claude [Blueprint] 2026-11-09:
  > DATA FLOW: 输入=30s QTimer tick（既有 _whisper_timer 升级）+ 用户消息事件；变换=HeartbeatEngine.on_tick → DrivesEngine.tick（纯 Python 衰减/恢复）→ urgency 计算 → 门禁链 → [仅全过] worker 线程调 harness.run_turn（system=心跳 prompt，含驱动状态段+静默选项）→ 静默则丢弃；输出=主动发言经既有 _chat_out/say/TTS 上屏；状态变化=pet_drives.json + heartbeat_state.json 落盘、chat_history 仅追加 assistant 条、energy-2/consecutive+1。
  > MODULE: ①pet/drives.py（新）：DrivesEngine——load/save（tmp+replace 原子写 pet_drives.json）/tick(now)/apply_user_message/apply_ai_reply/urgency(st,now)/state_segments(st)；三轴 mood(str)+intensity/energy(0-100)/affinity(0-100) ②pet/heartbeat.py（新）：HeartbeatEngine——on_tick（GUI 线程，忙闸门 _hb_busy）/ _decide(now)→Decision(action,reason) 纯函数可单测 / _speak（worker 线程：run_turn→静默检测→_ui_call 封送出口）/ 持久化 heartbeat_state.json（last_speak_time/timestamps_24h/consecutive_speaks）③pet/heartbeat_prompt.py（新）：build_heartbeat_system(drives_st, persona_text, time_line, facts, cfg)→str ④pet/chat.py：_whisper_tick 委托（heartbeat.enabled 时心跳全权，关闭时回退 legacy whisper）；ChatMixin 内构造引擎（存储目录取 storage 既有路径，config 取 cfg）⑤config.py：DEFAULT_CFG 增 heartbeat 段 + validate ⑥桌宠.py：仅当 ChatMixin 构造不可行时最小 glue。
  > INTERFACE: `DrivesEngine(storage_dir, cfg=None)`；`DrivesState(mood:str, mood_intensity:float, energy:float, affinity:float, last_user_message_time:float, last_decay_time:float)`；`HeartbeatEngine(chat_mixin, drives, cfg)`；`Decision(action:Literal["speak","skip"], reason:str, urgency:float, gates:dict)`；`build_heartbeat_system(...)->str`。
  > 参数默认：min_silent=600 / min_interval=1800 / urgency_threshold=0.35 / max_per_24h=15 / max_consecutive=2 / quiet_window=(23,6) / energy_recovery=2 per 300s / energy_full_secs=14400 / mood_decay_secs=14400 / msg_energy_cost=2 / affinity_gain=0.2 per 用户消息 / affinity_decay=0.5 per hour。urgency = (0.45·clamp(silence/3600,0,1) + 0.25·time_bonus + 0.20·affinity·clamp((silence-1800)/7200,0,1)) · (1-(1-energy/100)·0.5)；cooldown_required = min_interval·(1-urgency·0.85)。
  > INTEGRATION: 既有锚点——chat.py:298 _whisper_tick / chat.py:38 _build_system_prompt（段结构复用：persona+time_line+facts）/ chat.py:194 _speak_reply / harness.py:252 run_turn（可注入 transport）/ service_hooks.py:94 _ui_call / storage.py（原子写范式）/ memory.py facts / time_context.py:170 time_line / 桌宠.py:1408-1410 _whisper_timer。历史写入必须走 chat.py 既有 append+_persist 路径（身份不变式）。
  > TEST: 单元=DrivesEngine 衰减/恢复/消耗（含 last_updated 陷阱判别力用例）/ _decide 全门禁分支（假 transport，只桩网络出口）；集成=on_tick→speak 全链路（静默回复/历史只落 assistant/energy 扣除/24h 配额）；edge=时钟回拨、drives 文件损坏、run_turn 抛异常、静默标记变体（（静默）/「（静默）」/(Silence).）。
  > ELEGANCE: 无新依赖；新逻辑全在新模块；whisper 保留为 fallback 通道可回滚；复用 harness/ui_call/原子写既有范式。
  > RISK: ①历史写错会话→sid 快照+既有 append 路径 ②token 成本→门禁全 Python+默认配额+静默不计费 ③QTimer 跨线程静默失效（教训L5）→tick 永在 GUI 线程，worker 只包 LLM 调用 ④AGPL 污染→算法重写不拷码。
  >
  > claude [Blueprint-Approved] 2026-11-09: 用户以 /full-auto（ARGUMENTS: 试着实现一下看看怎么样）批准，进入实现。
  >
  > claude [~] 2026-11-09: 实现+修正轮 1/2 完成（141/141）。Gate 0 架构门 PASS（9 项硬 criterion；AGPL 扫描最大相同 2 行样板；D1 并发流/ds_busy 守卫等 3 廉价项已修）。
  > claude [Gate-1] 2026-11-09: 质量门 PASS（有条件）。必修：M1 衰减游标时钟回拨后退→已结算时段二次结算（现有回拨用例 elapsed=0 未覆盖该分支，需 1 行 max()+补用例）；M2 多宠共享 pet_drives.json/heartbeat_state.json 互相覆盖、24h 配额 guard 打穿（与 Gate-2 M-4/Gate-3.5 HIGH 同根）。建议：S1 _api_reload 后 cfg 冻结/S3 数值无下限/S4 提示词把被动回复说成「上次你主动说过」/S5 测试脚手架重复/S6 sticker 解析重复。N1-N9 nits（含 N9：urgency 公式 (affinity/100) 归一化偏离已批准蓝图，实现侧有充分理由+判别力用例，在此登记为已批准偏离）。
  > claude [Gate-2] 2026-11-09: 安全门 FAIL。**H-1 阻塞**：_speak 异常路径不推进 last_attempt_time → not_silent/cooldown/24h/consecutive 四道门禁全失效 → 持续传输失败（过期 key/网络抖动/5xx）时 ~120 次/小时无限重试（探针 6/6 实证）。M-1 静默轮与 4 跳工具轮计费不入配额（真实上限 ~60 请求/天 vs 配置暗示 15）；M-2 运行期改 heartbeat 配置被忽略（PUT /config 无法热关）；M-3 固定 .tmp 名并发写损坏（探针 179/300）；M-4 多宠成本 ×N。L-1 time_bonus 非 dict 静默杀死特性；L-2 数值无边界；L-3 last_speak_content 无定界符升权系统提示词；L-4 .gitignore 漏两个状态文件。正面：无状态组合可绕过门禁、无路径遍历、无除零、无密钥泄漏、输出消毒全继承 harness、主动路径注入面小于被动路径。
  > claude [Gate-3.5] 2026-11-09: 对抗测试无阻塞。12 探针矩阵：门禁边界零 off-by-one、时钟滥用（10 年回拨/ year 3000/非单调）、状态文件滥用（截断/类型错/NaN/目录/只读/6 线程并发）、重入（20 连 tick 仅 1 调用）、提示词构建（无 format-string 注入）、输入滥用（100k 字符/NUL/RTL）全部幸存。MEDIUM：time_bonus 崩溃（同 L-1）、前拨时钟后回拨→时间戳只钳下限→永久静默、_speak 非事务序（_save 抛则回复存而不显/队列抛则冷却失效）。LOW：静默标记泄漏（(silence)!、全角(Ｓilence)、小写 dsml）。
  > claude [~] 2026-11-09: 修正轮 3 dispatched：P0×3（H-1 异常记账/M1 游标只前进/多宠按 pet_id 分文件）+ P1×10（time_bonus 守卫/时间戳双端钳/活配置读取/事务序/数值下限/标记泄漏/attempts 配额/last_speak_content 落盘/gitignore/死代码清理），逐项判别力用例。
  >
  > claude [Gate-2-Pass] 2026-11-09: 安全门复审 PASS——H-1 闭环并经独立探针复证（恒抛 transport 三连 tick 仅 1 次外呼）；M-1/M-2/M-4/L-1/L-4 FIXED；M-3（文件名单射）与 L-2（配额上限）残余 + N-1（log.py redact IndexError，被 H-1 新错误路径激活）转终轮微修正。
  > claude [~] 2026-11-09: 终轮微修正完成（N-1 捕获组/M-3 sha1 摘要保单射/L-2 配额上限，153/153）。门禁 3.5 新增发现经路径可达性分析确认不影响本轮范围。
  > claude [x] 2026-11-09: Stage 1 完成。commit 5e6031b（10 文件 +2477/-13；桌宠.py 未提交 WIP 未带入）。验收 5 项全达：①drives 三轴状态机+原子落盘+游标只前进 ②六门禁 decide 纯函数 ③主动说话全链路（静默语义/身份不变式/sid 快照/FC 表情可用）④状态段 prompt+config 化 ⑤冒烟 119→153。ADR: docs/decisions/2026-11-09-heartbeat-engine-stage1.md。门禁：0-ARCH PASS / 1-QUALITY PASS / 2-SEC FAIL→复审 PASS / 3-TEST 153/153（L8 已知 flaky 复跑确认）/ 3.5-ADVERSARIAL 12 探针矩阵幸存。真机沙箱演示（真实 key 一次调用，urgency 0.615，sha256 验证零污染）：「主人，今晚去亲戚家吃饭，别又加班忘了～」——融合真实记忆（亲戚聚餐/常加班/喜欢被催吃饭）在角色主动发言。延期项登记 TASK-012。Unblocks: TASK-011。

### TASK-011: M4 学习式睡眠窗口 + 时段权重
- **Executor**: claude
- **Status**: [x]
- **Dependencies**: TASK-010
- **Contract**: TASK-010 蓝图 sleep_window 算法（30 天回溯/安静小时统计/含 0-6 点约束/4-14h/默认 23-06 回退）
- **Acceptance**:
  1. `pet/sleep_window.py`：从 pet_sessions.json 用户消息时间戳学习安静时段，喂给 heartbeat G7 门禁 + time_context 抑制
  2. 判别力用例：数据不足回退默认；纯白天安静段不选；跨午夜窗口
- **Handoff log**:
  > claude [ ] 2026-11-09: 自 TASK-010 蓝图拆出。TASK-010 期间 G7 用 config 硬编码 quiet_window=(23,6)。
  > claude [Notify] 2026-11-09: 依赖 TASK-010 已 [x]（commit 5e6031b）。可开工：30 天回溯学习式睡眠窗口接入 G7 + time_context 抑制；注意沿用 TASK-010 的 pet_id 分文件命名（sha1 摘要）。
  >
  > claude [~] 2026-11-09: claimed via /full-auto（用户「逐步完成剩下的后续」）。依赖已验证 [x]。
  > claude [Blueprint] 2026-11-09:
  > DATA FLOW: 输入=pet_sessions.json 近 30 天 user 角色消息 ts；变换=compute_sleep_window 纯函数（按小时统计「有消息的天数」→安静小时=天数≤观测天数×10% →取跨越午夜的、含 0-6 点至少一小时的最长连续安静段，长度 4-14h）；输出=SleepWindow(start_hour, end_hour, learned)；状态变化=无新落盘文件（进程内缓存 TTL 1h）。接入：heartbeat decide 的 G7 在 quiet_window=="auto" 时用学习窗（数据不足/失败回退默认 23-06），quiet_window 为显式 [start,end] 时尊重用户配置覆盖 auto；time_context 若存在「回复慢/节奏」类提示则在学习窗内抑制（不存在则不造，记录即可）。
  > MODULE: pet/sleep_window.py（新 ~110 行）: `@dataclass SleepWindow(start_hour:int, end_hour:int, learned:bool)` + contains(hour)/label()；`compute_sleep_window(timestamps: Iterable[float]) -> SleepWindow`（纯函数，可单测）；`get_user_sleep_window(root: str|None = None) -> SleepWindow`（读 sessions 提取 ts、模块级 dict 缓存 TTL 3600s、任何异常返回默认窗，永不抛）。常量 LOOKBACK_DAYS=30 / MIN_OBSERVED_DAYS=7 / QUIET_DAY_FRACTION=0.10 / MIN_WINDOW_HOURS=4 / MAX_WINDOW_HOURS=14 / NIGHT_HOURS=range(0,6) / DEFAULT=(23,6)。
  > INTEGRATION: ①pet/heartbeat.py decide G7：cfg.quiet_window=="auto" → get_user_sleep_window(引擎 storage_dir) 的 contains(hour)；为 [a,b] → 原 pair 逻辑；②pet/config.py：DEFAULT_CFG heartbeat.quiet_window 默认由 [23,6] 改 "auto"；validate 接受 "auto" 或二元整数列表（越界/畸形→告警回退 "auto"）；normalize 侧 quiet_window 原样透传（"auto" 或 list）；③pet/time_context.py：仅当存在节奏/慢回复提示时加抑制，否则不改。
  > TEST: ①观测<7 天→默认(23,6,learned=False) ②纯白天安静段不选（早晚有消息、办公时段安静→仍回退默认）③跨午夜窗（23-02→start=23,end=2）④30 天真实形态（消息集中 9-23 点）→学到 23-09 且 learned=True ⑤G7 集成：学习窗覆盖当前小时→decide skip("quiet_window")；cfg 显式 [a,b] 覆盖 auto ⑥缺 ts/坏消息/非 user 角色跳过 ⑦get_user_sleep_window 对坏文件/空目录/None 永不抛
  > ELEGANCE: 算法重写自 Shikigami-Protocol sleep_window（AGPL-3.0，只搬算法不拷码；参考实现已由主会话读过，实现方不得读取 temp 下参考文件，注意行级表述差异）；无新依赖；缓存模块级 dict。
  > RISK: sessions 大时全量扫描→1h 缓存+只取 ts；多宠共享 sessions 文件→池化全部 user 消息（与 pet_memory.json 全局语义一致）。
  > claude [Blueprint-Approved] 2026-11-09: 用户「逐步完成剩下的后续」批准，进入实现。
  >
  > claude [~] 2026-11-09: 实现完成（157/157）。实现方自查自修段首判定 bug；偏差记录 5 条（__init__ auto 补丁/localtime 小时/用例②与规则 5 冲突/失败 TTL 600s/reset_cache seam）。
  > claude [Gate-1] 2026-11-09: 质量门 PASS 无必修；4 should-fix：normalize 放行空窗 [3,3] 与 validate 分歧致 G7 fail-open / 学习窗可大半在白天（15:00 起始 12h 静默覆盖用户活跃傍晚，违反模块自述意图）/ G7 集成用例只探 hour 2 无判别力 / GUI 线程全量 parse 无大小护栏。3 廉价 nit（单一事实来源分裂/纯图消息被排除/蓝图②表述）。
  > claude [~] 2026-11-09: 门禁修正轮 F1-F6 全部落地（空窗归一/起始小时约束 start∈{18-23,0-5}/hour7 判别力断言/8MB 护栏/单一事实源/纯图消息计活跃），157/157，逐项回退验伪。
  > claude [x] 2026-11-09: 完成。验收 2 项全达：①pet/sleep_window.py 学习窗接入 G7（auto 模式从近 30 天 user 消息作息学习；<7 天/坏文件/超大文件回退 23-06；显式 [a,b] 覆盖；旧配置 [23,6] 自动迁 auto）②判别力用例全覆盖（数据不足/白天段不选/跨午夜/起始小时约束/集成 discriminates/脏数据/缓存）。裁决记录：a) 蓝图用例②「仍 DEFAULT」系蓝图笔误——按规则 5 该数据应学到 23-07（与参考实现语义一致），测试改验白天段不入选；b) 03:00/14:00 场景学到 04-14 为正确夜猫子作息，非缺陷；c) heartbeat_prompt「2 小时关心」提示不做学习窗抑制——G7 门禁在安静窗内不放行到 LLM，门禁级抑制已覆盖提示词级；d) time_context.py grep 无节奏/慢回复类提示文本，无可抑制项，一行未改（「+时段权重」实指 TASK-010 已交付的 urgency time_bonus 表）；e) 已知限制：迁移后无法把 quiet_window 钉回恰好 [23,6]（旧默认与主动选择不可区分），需要固定值者可设其他 pair——影响面极小，不单开任务。

### TASK-012: TASK-010 延期项 backlog（成本可见性/多宠精化/重构）
- **Executor**: claude
- **Status**: [x]
- **Dependencies**: TASK-010
- **Contract**: TASK-010 门禁 deferred 清单（Gate 1 S 项 / Gate 2 M-2 数值热重载 / Gate 3.5 Low）
- **Acceptance**: 逐项修复后勾销
- **Handoff log**:
  > claude [ ] 2026-11-09: 登记待办：
  > 【成本可见性】主动发言 token 用量入账本/悬浮窗（heartbeat `_usage` 被丢弃，被动路有记账）；错误通知（queue-full/no-key/_fail）走 _chat_out 也扣 2 能量的边界裁定。
  > 【多宠精化】`apply_pet` 改 pet_id 时重绑引擎（桌宠.py:1837，当前改 id 不重绑）；旧版无摘要状态文件迁移（`pet_drives.main.json`→摘要名，本环境无实机数据未做，将来有实机数据要补改名迁移）。
  > 【配置】heartbeat 数值参数热重载单向陈旧（运行期调小 max_per_24h 要重启生效，fail-closed 方向安全）。
  > 【重构】_persist 内联副本提取 mixin 方法；sticker 解析块与 chat.py 共享（S6）；smoke hb_fixture() 脚手架抽取（S5，约 100 行）；N1 门禁改名（quiet_window→outside_quiet_window 等）。
  >
  > claude [~] 2026-11-09: claimed via /full-auto（用户「逐步完成剩下的后续」）。本捻先做【成本可见性】第一项：主动发言 token 入账。口径：与被动轮完全同源——harness.run_turn 的 usage 跨跳累加值经既有记账路径落 usage_ledger.json 并刷新 Token 统计悬浮窗；worker 线程触达 GUI/账本必须走既有封送/队列范式（_ui_call 或 _chat_queue 携带），不得自造写入口；不得改被动路行为。
  > claude [~] 2026-11-09: 入账项完成（162/162）。`_speak` 解构 run_turn 三元组取 usage → 复用被动轮同一 `chat._record_tokens`+`calc_turn_cost`（ledger 有锁，worker 直调安全）→ `_token_stats_dirty`/`_queue_say` 走既有范式刷悬浮窗与气泡；静默轮同账（真花钱）；异常轮无 usage 不记不编造；chat.py 一行未改（_chat_queue 消费方在禁改范围，走契约 fallback）。5 新用例回退验伪。
  > claude [x] 2026-11-09: 剩余项逐条裁决——①错误通知扣 2 能量：**接受现状**（失败轮付小代价主题上合理，恢复 +2/300s 影响可忽略，不值得为它拆 _chat_out 单点）；②apply_pet 改 id 不重绑引擎：**接受为已知限制**（运行期改宠物 id 罕见路径）；③heartbeat 数值热重载单向陈旧：**接受**（fail-closed 方向安全，重启解决；live 重归一反而危险——Gate 2 复审结论）；④重构类：**移交 TASK-014**。

### TASK-013: Stage 2a 记忆相关度召回（替代 facts 全量注入）
- **Executor**: claude
- **Status**: [ ]
- **Dependencies**: TASK-010
- **Contract**: 本块 [Blueprint]（2026-11-09 主会话设计，Stage 2 第一捻）
- **Acceptance**:
  1. `pet/memory_recall.py`：score_facts 纯函数（关键词相关度×时间衰减×访问计数），legacy 纯字符串 fact 零迁移兼容
  2. `_build_system_prompt` 的【你对主人的记忆】段由「前 20 条全量」改为「top-K 相关度注入」（K 默认 8，cfg 可配）
  3. fact 元数据（ts/last_access/hits）落盘与命中回写；冒烟新增相关度/衰减/legacy/K 截断/回写用例，既有不回归
- **Handoff log**:
  > claude [ ] 2026-11-09: 登记自 Stage 2 拆分。蓝图：
  > DATA FLOW: 输入=当前会话近期消息文本+pet_memory.json facts（带元数据）；变换=memory_recall.score_facts 纯函数（字符二元组相关度 × exp 时间衰减半衰期 7 天 × 访问计数加权）→ top-K；输出=系统提示词记忆段由全量前 20 条改为相关度 top-K（默认 K=8，cfg.memoryRecallK）；状态变化=fact 元数据 ts/last_access/hits 读写，legacy 纯字符串 fact 默认值兼容（零迁移）。
  > MODULE: ①pet/memory_recall.py（新 ~130 行）: `@dataclass Fact(text:str, ts:float=0.0, last_access:float=0.0, hits:int=0)`；`score_facts(facts, context_text:str, now:float, k:int=8) -> list[Fact]`（纯函数可单测）；相关度=查询与 fact 的字符二元组重叠率（中文免分词，无新依赖）；衰减=exp(-ln2·(now-ts)/7d)；最终分=相关度×(0.5+0.5·衰减)×(1+0.1·hits)；空 context 时退化为按 recency ②pet/chat.py:55-57 注入点改 recall top-K ③pet/memory.py: _fact_extract 落盘带 ts；recall 命中后 hits/last_access 回写（一处、加锁、原子写）。
  > TEST: ①相关度排序（上下文提「加班」→加班 fact 进、无关的出列）②时间衰减（同相关度新 fact 胜旧 fact）③legacy 纯字符串不崩且可排序 ④K 截断/空 facts/全不相关 ⑤命中回写落盘 ⑥既有 prompt 构建与 _call_ds 链路用例不回归。
  > ELEGANCE: 无新依赖（不引 jieba/向量库）；二元组重叠对 ≤30 字短 fact 足够；embedding/向量检索留 Stage 2b（facts 过百再上，TASK-009 同款判断）。
  > RISK: 全量→相关注入是行为变化，可能漏「当前不相关但重要」的事实——K=8+衰减保底+hits 加权缓解；legacy 兼容必须零迁移；回写并发走既有 _mem_lock。

### TASK-014: TASK-010/012 清理与重构 backlog
- **Executor**: claude
- **Status**: [ ]
- **Dependencies**: TASK-010, TASK-012
- **Contract**: TASK-012 移交清单
- **Acceptance**: 逐项修复后勾销
- **Handoff log**:
  > claude [ ] 2026-11-09: 登记自 TASK-012 移交：①`_persist` 内联副本（heartbeat.py）提取为 chat mixin 共享方法（守住 TASK-008 身份不变式，被动路行为零变化）②sticker 解析块（heartbeat.py 与 chat.py 重复 6 行）下沉共享 ③smoke hb_fixture() 上下文管理器抽取（约 100 行重复夹具）④门禁改名（gates["quiet_window"]→outside_quiet_window 等，纯可读性）⑤TASK-012 入账接线镜像：出现第三个调用方时提取 `ChatMixin._record_turn_usage(usage)`。

### TASK-015: AgentRuntime 内化探针（DSH/OpenClaw 可替换执行内核）
- **Executor**: claude
- **Status**: [x]
- **Dependencies**: TASK-009, TASK-010, TASK-012（均已 [x]）
- **Contract**: 2026-11-10 用户批准的设计对话（「git 备份后，批准并执行，理论不如实践」）+ 本文件 TASK-009 Phase 3 自主路线；无 PRD，沿用 TASK-010 的先例（ONEs + 用户逐节确认的设计对话为合同源）
- **Acceptance**:
  1. `pet/agent_runtime.py`：AgentRuntime 协议 + RuntimeResult + NativeHarnessRuntime + DshHeadlessRuntime；NDJSON 事件聚合、用量求和、错误与超时处理；不改变既有 chat/heartbeat 行为
  2. `tools/runtime_probe.py`：同一 goal 可跑 native/dsh/openclaw/command，输出 JSONL 事件与 JSON 汇总；密钥只从环境读取且全程脱敏
  3. 实测记录：DSH 0.2.2-rc.1 headless 真实任务（含 tool_call/tool_result）事件、token、延迟；OpenClaw 安装/入口探测
  4. smoke 新增 runtime 用例，既有 168 项不回归
- **Handoff log**:
  > claude [~] 2026-11-10: claimed. git backup tag `backup/pre-runtime-spike-20261110` @ dd202fa。用户批准「理论不如实践」。前置实测：DSH headless 成功（Node 24.22.2；NDJSON 事件 session/status/text/thinking/tool_call/tool_result/final；trivial 单轮 14,179 input tokens；tool 任务两步共 28,624 tokens；完整安装 9.0 GB）；OpenClaw 2026.11.2 MIT，Node>=24.16，npm 包 274.8MB unpacked（安装中）。
  > claude [Blueprint] 2026-11-10: 用户已预批准（「批准并执行」）。DATA FLOW: goal+context → AgentRuntime.run → RuntimeResult(text/usage/events/error/latency) → probe JSON。MODULE: ①pet/agent_runtime.py 新模块（协议+原生适配器+DSH sidecar 适配器）②tools/runtime_probe.py 新探针 ③tools/smoke_check.py 新增用例。INTEGRATION: 零改动 chat/heartbeat；黑盒隔离。TEST: 假 transport/假 runner 单测 + DSH 真实探针 + 全量 smoke。RISK: 外部进程/密钥脱敏/超时；DSH 成本高，仅作重任务 runtime。
  > claude [x] 2026-11-10: 交付。新增 `pet/agent_runtime.py`（AgentRuntime 协议 / RuntimeResult / NativeHarnessRuntime / DshHeadlessRuntime / CommandRuntime / NDJSON 聚合 / 脱敏）与 `tools/runtime_probe.py`（native/dsh/command 三态探针，Key 只从环境读取）。冒烟 168 → 171，171/171 全绿。实测：DSH 0.2.2-rc.1 headless 真任务成功，事件 session/status/text/thinking/tool_call/tool_result/final 完整；trivial 任务 14,175 tokens / 52.9s；工具任务约 28,624 tokens；DSH 完整安装 9.0GB；OpenClaw 2026.11.2 MIT、npm unpacked 274.8MB，本机 Node 22.20 导致 lifecycle 中止、Node 24 PATH 重试未生效，端到端未验证。ADR: docs/decisions/2026-11-10-agent-runtime-seam.md；报告: docs/spikes/2026-11-10-agent-runtime-probe.md。结论：DSH 可作重任务 sidecar，不可作生命核心；生产接线需预算门禁 + profile 裁剪 + 事件总线映射，登记 TASK-016。

### TASK-016: DSH runtime 生产接线（预算门禁/事件总线/profile 裁剪/OpenClaw 适配）
- **Executor**: claude
- **Status**: [ ]
- **Dependencies**: TASK-015
- **Contract**: ADR-2026-11-10-01 + docs/spikes/2026-11-10-agent-runtime-probe.md
- **Acceptance**:
  1. DSH runtime 默认关闭，设置页可启用并配置日预算；超预算/超时/无 Key 时 fail-closed
  2. `tool_call/tool_result/usage` 映射到 workstatus/tooldetail/notify，生活日志可见
  3. DSH profile patch 替换 coding persona（宠物人格）并裁剪工具集；最小安装/按需安装方案成文
  4. OpenClaw CLI/headless 适配（或明确不接的裁决）；既有 smoke 不回归
- **Handoff log**:
  > claude [ ] 2026-11-10: 自 TASK-015 deferred 登记。注意：DSH 单次基线 ~14k tokens/53s，只能重任务；默认关闭，预算门禁先于一切接线。

---

## Messages

> claude [Blueprint] 2026-11-06（TASK-008，Harness Phase 1）：已获用户批准「批准并执行」。
> DATA FLOW: 输入=用户消息(+表情 chip/附件)；变换=Harness loop（组上下文→带工具请求→执行工具回喂→≤3 跳→最终回复），表情目录从每轮提示词移除改为工具按需召回；输出=最终回复+表情意图（meme/sticker 各 ≤1），经既有 _chat_out 渲染；状态变化=chat_history 仍只落 user+assistant，tool 消息瞬态不落盘。
> MODULE: ①pet/harness.py（新）：loop + 工具注册表 + 本地检索 + 可注入 transport ②pet/chat.py：_call_ds 委托 harness（保留 busy 排队/返回值），_build_system_prompt 移除目录两块，废除 90 字截断 ③桌宠.py：_chat_queue 元组扩展携带 meme 意图 ④tools/smoke_check.py：假 transport 两跳链路用例。
> TEST: py_compile → smoke_check（含新增用例）→ 线上实测（思考+FC+表情上屏+历史正确）。
> ELEGANCE: 无新依赖；复用 UiBridge/_ui_call/_chat_out/motion tick 既有范式；标记协议保留为 fallback 保证可回滚；whisper 不 FC。
> RISK: thinking+FC+流式三特性叠加有风险→transport 用非流式（确定性优先，打字机改为末跳分段重放）；tool_calls 与 content 并发返回时 content 视为内心 OS 丢弃。

> claude [Blueprint] 2026-11-06: 修复蓝图（已获用户批准「批准并执行修复」）
> DATA FLOW: 输入=审查报告 8 项 P0；变换=按锚点最小改动，新逻辑仅封送层/线程化两处；输出=修复后的窗口/服务/配置代码；状态变化=无协议变更。
> MODULE: ①menus.py 守卫×3 ②桌宠.py 清工作态+cfg_provider 解密+桥装配 ③service_hooks.py reload 补解密+delete 守卫+封送层 ④config.py 默认文件剔 pets ⑤credentials.py protect 幂等 ⑥chat.py 线程化 ⑦motion.py 睡眠闸门+dur 防御。
> TEST: py_compile → smoke_check → 启动验证 → curl 本地 API 实测。
> ELEGANCE: 一行级或局部修复，无新模块、无新依赖；复用项目已有「后台→主线程队列」范式。

> claude [Blueprint-Approved] 2026-11-06: 用户批准，进入实现。

> claude [~] 2026-11-06: 实现完成（7 文件 +192/-25），py_compile 全过。

> claude [Gate-Fail] 2026-11-06: Gate 1 质量 FAIL（whisper 冒烟用例确定性红 103/104；封送/守卫/幂等零覆盖）。Gate 2 安全 FAIL（F1 阻断：_cfg_for_service 解密 + GET /config 免令牌 = 明文 Key 外泄）。进入修复轮 2。

> claude [~] 2026-11-06: 修复轮 2 完成：F1 脱敏+掩码跳过；H1 测试改异步驱动；M2 补 7 条用例；M3 cancelled 取消+异常入日志；M4 roster 入白名单；F3c 改捕 Exception；L5-L10 注释/顺序/初始化修正。冒烟 104→111 全绿；线上 API 实测 F1/H5/H1 三点全过。

> claude [Gate-Fail→PASS] 2026-11-06: 复审代理核对 Round-2：11 CLOSED / 1 PARTIAL-Low / 无 OPEN / 无新 High → Gate 1+2 判 PASS。遗留 PARTIAL（_whisper_busy 初值）与用例判别力缺口已当场修复并复跑 111/111。

> claude [x] 2026-11-06: 全部完成。commit 76e928c（11 文件 +607/-31，含 ADR 与本 ONES）。提交态启动验证：自检 7/7、API 7340 就绪、动作引擎运转、无跨线程告警。
