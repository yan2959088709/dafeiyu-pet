# ADR-2026-11-10-life-loop-memory：电子生命総合批（记忆自主进化 + LifeLoop + 自启/自恢复 + 外包/感知/窗口）

- 日期：2026-11-10
- 状态：已接受（用户随 /full-auto 指令预批准「考虑的全面一点…可自主进化…记忆管理也需要考虑…批准并执行」）
- 任务：ONES TASK-020 ~ TASK-025（commit 335bf49 / 12dd994 / b3e3aaf / ab2e04d / ea9be36）

## Context

前五批把桌宠从「被动对话壳」推进到「有身体（代谢/内感受）、有节奏（心跳）、
有记忆（facts/episodic/goals）、有出口（shell/剪贴板/截图/文件）、有设置页」
的形态。2026-11-10 用户定调「搬入新家、像器灵一样完全接管电脑」，要求
**可自主进化**，并明确记忆管理要在考虑之内。差距分析（代码核实）确认六个
缺口：记忆只有状态没有生命周期管理、心跳只会开口/静默没有行动选择、重启
后不能自己活过来、AI 不能把重活外包、没有文件系统事件感知、窗口只能读。

## Options Considered

1. **接 OpenClaw（Gateway+channels）**：再次否决。它的形态是「多渠道常驻
   助理」，而目标是「它在这台电脑上活着」；apiserver 只绑 127.0.0.1 是刻意
   的安全边界。其真正基础的功能（心跳开关/记忆层）已自有。
2. **接 DSH 作为大脑**：否决。DSH 是 coding agent，其心跳/记忆与内核深耦合；
   只把「重任务执行」当 sidecar 用（TASK-016 已接线），本批补的是 AI 自主
   派活的入口。
3. **记忆用向量库/embedding**：否决（YAGNI）。facts ≤200 条、字符二元组
   Dice 对短中文事实够用；向量检索留到 facts 过百再评估（TASK-013 同款判断）。
4. **LifeLoop 用代码决策意图**：否决。违反 feedback-autonomy-over-gates；
   最终采用「身体化建议 + 模型声明意图」——select_intention 只把身体状态
   换算成一句「你身体此刻更想X」，选择权永远在 AI。
5. **filewatch 用 watchdog 库**：否决。轮询对桌面场景足够，且零新依赖；
   快照不落盘，重启重新播种。

## Decision

### TASK-020 记忆自主进化
- 新纯函数模块 `pet/memory_maintain.py`：bigram_sim（Dice）、merge_similar
  （近重复/精确重复融合，hits 相加、双临时才保持临时）、consolidate、
  soft_forget/archive_records/append_archive、review_rows。
- 临时→长期的晋升信号：**hits≥3 且 (last_access−ts)≥12h**（跨时段的反复
  想起才构成持久性证据；一晚内的重复不算）。创建后很久才集中提一串的边界
  情形会被算作跨时段——可接受（晋升错的后果轻，recall 衰减 + manage_memory
  可事后纠正），换 first_hit 跟踪的复杂度不值得。
- 软遗忘：AI 自主生命周期的全部移除路径（过期/超限/冲突消解/巩固合并/AI
  自忘）都写 `memory_archive.jsonl`，不硬删；**归档失败整体放弃淘汰**
  （数据不丢优先于存储干净）。主人显式命令（聊天说「忘记…」/GUI 编辑器）
  直接删——数据是主人的。
- `manage_memory` 工具（review/consolidate/forget）给 AI 自省入口。

### TASK-021 LifeLoop v1
- 七意图：休息/学习/整理/玩耍/关心/开口/静默。心跳醒来 → 提示词给意图菜单
  （含具身建议）→ 模型以 `【意图：X】` 中英文标签声明 → 代码路由：
  speak/play/care 上屏、silent/rest 静默、learn/organize 只进内在日志。
- 无标签或缺省 → speak，**旧行为逐字节兼容**。
- `life_state.json` 记连续休息与各意图上次时间，让建议有记忆。
- 顺带收编 query_balance/check_time（TASK-009 剩余项）。

### TASK-022 开机自启 + 崩溃自恢复
- Scout 发现自启（menus .lnk）与进程内重拉（guard.CrashGuard）已存在，
  范围缩小为：.lnk 构建提取为 `pet/autostart.py`（设置页面化）+ **硬崩溃
  看门狗** `pet-watchdog.bat`（CrashGuard 的 excepthook 抓不到 Qt 致命
  abort/外力杀进程，看门狗补这一层）：start /wait + quit.flag 判定，
  主动退出不重拉、异常消失重拉、单轮最多 5 次。
- quit.flag 由 petservice.quit_app 退出前写；看门狗每轮启动前清残留。

### TASK-023 DSH 重任务外包
- `runtime_manager.start_task`：门禁→构建→记账→daemon 线程→回调封送的
  核心抽取；UI 改薄，AI 的 `delegate_task` 与设置页/右键同一条路。
- 三层门禁：aiDshEnabled（默认关）+ 既有 RuntimeGate（日 50k/单次 15k/
  24h 3 次）+ 运行中拒第二次。runtime.run 抛桩也包装成 Result 送 on_finish，
  不卡 busy。

### TASK-024 文件系统事件感知
- `pet/filewatch.py` 轮询快照 diff（10s，挂既有 _tick_background_polls），
  事件进 `_watch_events`（上限 50），心跳提示词以【你观察到的变化】呈报并
  即读即消费。只呈报，管不管她自决。
- `manage_watch` 工具（add/remove/list）路径过 `_inside_any_root` 沙盒。
- `path` 加入 ARG_LONG_KEYS：绝对路径动辄超 64 字符，截断即毁参数。

### TASK-025 窗口管理
- sysmon Win32 助手：enum_windows/find_window/window_op；`manage_window`
  工具按标题子串或前台窗做 minimize/maximize/restore/activate；刻意不做
  close（防丢未保存文件）；抢焦点被系统拒绝时如实报告。

## Impact

- 新增文件：pet/memory_maintain.py、pet/life_loop.py、pet/autostart.py、
  pet/filewatch.py。
- 接线：memory.py / heartbeat.py / harness.py / 桌宠.py / settings_ui.py /
  config.py / runtime_manager.py / runtime_ui.py / menus.py / petservice.py /
  sysmon.py / chat.py / storage.py。
- 配置键（全部进 DEFAULT_CFG 防重启还原）：autoRecoverEnabled、aiDshEnabled、
  watchDirs。
- AI 工具面 24→29：+manage_memory / delegate_task / manage_watch /
  manage_window / query_balance / check_time（manage_memory 与生命循环为
  提示词协议而非新工具名的部分除外）。
- 冒烟 223 → 240（+17 检查），offscreen 与 windows 双平台全绿。

## Risks

- 看门狗与 CrashGuard 可能双双重拉（单实例锁仲裁，最坏多几次进程拉起，
  看门狗 5 次预算后自停；硬崩溃场景看门狗是唯一兜底）。
- 文件名/路径进心跳提示词 = 进 LLM API 的数据面（安全审计范围，见 Gate 2）。
- LifeLoop 意图标签是模型自述，模型不守约定时退化为 speak（安全方向）。
- 记忆晋升的信号是弱信号（见上），误晋升的后果由 recall 衰减兜底。
