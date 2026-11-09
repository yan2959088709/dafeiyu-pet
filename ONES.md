# ONES — dafeiyu-pet 修复轨道

> 审查报告（2026-11-06，4 代理并行 + 主会话复核）即本轨道契约源。
> P0 轮范围 = 8 项 P0 + 2 项一行级 P1，已全部交付并提交（commit 76e928c）。
> 测试入口：`tools/smoke_check.py`（104 → 111（P0 轮）→ 119（Harness 轮，含 8 条 harness 用例）全绿）。

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
- **Status**: [ ]
- **Dependencies**: TASK-008
- **Contract**: 2026-11-06 深夜设计对话分期
- **Acceptance**: 逐项修复后勾销
- **Handoff log**:
  > claude [ ] 2026-11-06: 登记待办：
  > 【Phase 2 工具扩容】remember_fact / query_balance / play_animation / check_time——把现有「本地技能」（balance.py:_local_skill）收编为 FC，AI 自主决定何时查余额/记事/做动作；流式 transport + reasoning_content 实时「思考中」指示；search 升级为标签分类 + embedding（库过百后）。
  > 【Phase 3 自主意识】mood/energy 状态机驱动主动搭话（whisper 调度器升级）；目标队列（待办提醒）；时段策略（夜间静默）；跨会话长期记忆检索（facts 过多时按相关度注入而非全量前 20 条）。
  >
  > claude [~] 2026-11-09: 拆分——Stage 1（drives 状态机 + 心跳门禁 + 主动说话链路）拆为 TASK-010 实施中；M4 学习睡眠窗口拆为 TASK-011 待办。

### TASK-010: Stage 1 心跳引擎 + 驱动状态机（自主循环/内在驱动/主动说话）
- **Executor**: claude
- **Status**: [~]
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

### TASK-011: M4 学习式睡眠窗口 + 时段权重
- **Executor**: claude
- **Status**: [ ]
- **Dependencies**: TASK-010
- **Contract**: TASK-010 蓝图 sleep_window 算法（30 天回溯/安静小时统计/含 0-6 点约束/4-14h/默认 23-06 回退）
- **Acceptance**:
  1. `pet/sleep_window.py`：从 pet_sessions.json 用户消息时间戳学习安静时段，喂给 heartbeat G7 门禁 + time_context 抑制
  2. 判别力用例：数据不足回退默认；纯白天安静段不选；跨午夜窗口
- **Handoff log**:
  > claude [ ] 2026-11-09: 自 TASK-010 蓝图拆出。TASK-010 期间 G7 用 config 硬编码 quiet_window=(23,6)。

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
