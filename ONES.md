# ONES — dafeiyu-pet 修复轨道

> 审查报告（2026-11-06，4 代理并行 + 主会话复核）即本轨道契约源。修复范围 = P0 全 8 项 + 2 项一行级 P1 搭载修复。P1 其余项登记为 TASK-007 后续处理。
> 测试入口：`tools/smoke_check.py`（项目自带冒烟套件）+ `py_compile` + 运行验证。

### TASK-001: P0 一键修复组（退出崩溃/切皮肤冻死/首启漂移/置顶抹穿透/APPDATA）
- **Executor**: claude
- **Status**: [ ]
- **Dependencies**: none
- **Contract**: 审查报告 H2/H4/H8/M2/M8
- **Acceptance**:
  1. `quit_app` 在 tray 为 None 时不抛异常
  2. `_bind_skin` 后 `_work_anim/_work_sig/_work_text` 已清空
  3. 首启落盘的 config.json 不含 `pets` 键
  4. `set_topmost` 后穿透状态保留
  5. `set_autostart` 不缺 APPDATA 时崩菜单回调
- **Handoff log**:
  > claude [~] 2026-11-06: 蓝图已批准，开始实现。

### TASK-002: H3 凭据解密链路修复（reload/config 读漏解密 + protect 幂等）
- **Executor**: claude
- **Status**: [ ]
- **Dependencies**: none
- **Contract**: 审查报告 H3
- **Acceptance**:
  1. `_api_reload` 后 `cfg["ds_api_key"]` 为明文（非 dpapi: 前缀）
  2. `protect()` 对已加密文本幂等（不二次加密）
  3. PetService 的 cfg_provider 返回解密后配置
- **Handoff log**:
  > claude [~] 2026-11-06: 蓝图已批准，开始实现。

### TASK-003: H5 DELETE /chat 空 ts 清空防护
- **Executor**: claude
- **Status**: [ ]
- **Dependencies**: none
- **Contract**: 审查报告 H5
- **Acceptance**:
  1. `_api_chat_delete(stamps=None)` 返回 ok=False 且不动会话数据
  2. 本地对话框路径（chat_ui，恒有 stamps）行为不变
- **Handoff log**:
  > claude [~] 2026-11-06: 蓝图已批准，开始实现。

### TASK-004: H6 碎碎念移出 Qt 主线程
- **Executor**: claude
- **Status**: [ ]
- **Dependencies**: none
- **Contract**: 审查报告 H6
- **Acceptance**:
  1. `_whisper_tick` 不再于 GUI 线程同步请求（工作线程 + busy 闸门）
  2. 显示动作（say/表情/气泡）回落 GUI 线程执行
  3. `_whisper_generate` 钩子路径（HTTP 线程调用）同样正确
- **Handoff log**:
  > claude [~] 2026-11-06: 蓝图已批准，开始实现。

### TASK-005: H7 睡眠态闸门 + dur 防御
- **Executor**: claude
- **Status**: [ ]
- **Dependencies**: none
- **Contract**: 审查报告 H7 / 动作系统 M5
- **Acceptance**:
  1. `_sleeping` 时低于点击级动作只排队不覆盖睡眠
  2. 点击级动作先唤醒再播放
  3. `_start_emote` 对 dur<=0/非数字有防御
- **Handoff log**:
  > claude [~] 2026-11-06: 蓝图已批准，开始实现。

### TASK-006: H1 HTTP 钩子 GUI 线程封送
- **Executor**: claude
- **Status**: [ ]
- **Dependencies**: none
- **Contract**: 审查报告 H1
- **Acceptance**:
  1. 新增 `_ui_call` 封送：非 GUI 线程的窗口操作回主线程执行并带回返回值
  2. `_api_hooks` 中 Qt 触达钩子全部经封送；whisper/纯数据钩子不封送
  3. `_throw` 经接口触发后飞行定时器在 GUI 线程正常走时
- **Handoff log**:
  > claude [~] 2026-11-06: 蓝图已批准，开始实现。

### TASK-007: P1 遗留项（设计类，本轮不实施）
- **Executor**: claude
- **Status**: [ ]
- **Dependencies**: TASK-001..006
- **Contract**: 审查报告 M1/M3/M4/M5/M6/M7/M8/M9 + 低危清单
- **Acceptance**: 逐项修复后勾销
- **Handoff log**:
  > claude [ ] 2026-11-06: 登记待办：穿透+无托盘失联(M1)、多宠 cfg/记忆细粒度合并(M3)、JSON 原子写(M4)、会话代号(M5)、错误路径用户消息入历史(M6)、chatModel 接上主链路(M7)、ts 补写(M8)、设置页校验前移(M9)、死配置项清理、print 接 pet_log、QMovie stop、spec/LF 行尾等低危项。

---

## Messages

> claude [Blueprint] 2026-11-06: 修复蓝图（已获用户批准「批准并执行修复」）
> DATA FLOW: 输入=审查报告 8 项 P0（均带 file:line 锚点与修复方案）；变换=按锚点最小改动修复，新逻辑仅 H1 封送器/H6 线程化两处；输出=修复后的窗口/服务/配置代码；状态变化=无协议变更（纯缺陷修复，接口签名不变）。
> MODULE: ①menus.py 守卫×3 ②桌宠.py _bind_skin 清工作态 + PetService cfg_provider 解密 + _ui_bridge 装配 ③service_hooks.py _api_reload 补解密 + chat_delete 守卫 + _ui_call/_UiBridge 封送层 ④config.py 默认文件剔 pets ⑤credentials.py protect 幂等 ⑥chat.py 碎碎念线程化（_whisper_show 回落）⑦motion.py 睡眠闸门 + dur 防御。
> INTEGRATION: 不新增文件；不改公开接口签名；黑盒隔离——新逻辑集中于 service_hooks 封送器与 chat 线程化两处，其余为一行级守卫。
> TEST: py_compile 全量 → smoke_check 套件 → 启动验证 → curl 本地 API 验证（DELETE 无 ts 拒绝 / throw 后宠物移动证明定时器归位）。
> ELEGANCE: 全部一行级或局部修复，无新模块、无新依赖；H1/H6 复用项目已有的「后台→主线程队列 + GUI 消费」范式。

> claude [Blueprint-Approved] 2026-11-06: 用户批准，进入实现。

> claude [~] 2026-11-06: 实现完成，进入门禁链。变更 7 文件 +192/-25：
> TASK-001 menus.py 三处守卫（quit tray/topmost 重放穿透/APPDATA）+ 桌宠.py _bind_skin 清工作态 + motion.py _reset 补 _work_anim + config.py 默认文件剔 pets；
> TASK-002 credentials.protect 幂等 + _api_reload 补 load_secrets + 桌宠.py PetService cfg_provider 解密；
> TASK-003 _api_chat_delete 空 stamps 拒绝；
> TASK-004 chat.py 碎碎念线程化（_whisper_show 上屏回落 + busy 闸门）；
> TASK-005 motion.py 睡眠闸门 + dur 防御；
> TASK-006 service_hooks.py UiBridge/_UiCall 封送层 + _UI_HOOKS 包装 + 桌宠.py 装配桥。
> py_compile 7 文件全过。Gate 1/2 代理审查中，Gate 3 冒烟套件+运行验证待跑。
