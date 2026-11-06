# ADR-2026-11-06-01: 本地 HTTP 钩子的 GUI 线程封送

- 状态：已接受
- 日期：2026-11-06
- 关联任务：ONES TASK-001..006（P0 修复轮）

## Context

桌宠带一个本地 HTTP 服务（`pet/apiserver.py`，`ThreadingHTTPServer` + 每请求一个
daemon 线程），外部进程经它驱动桌宠：气泡、点播动画、定位、抛掷、对话、配置重载。

`dispatch()` 在 HTTP 工作线程里同步调用窗口钩子（`pet/service_hooks.py` 的
`_api_*`），而钩子里全是 Qt 调用：`QWidget::move()`、`update()`、
`QTimer(self).start(16)`、`_start_emote` 写演出状态。Qt 控件非线程安全。

已确认的后果（审查 + 复现）：

1. `QTimer(self)` 亲和 GUI 线程，从 HTTP 线程 `start()` 触发
   `QObject::startTimer: Timers cannot be started from another thread`，
   定时器**永不触发**——`/throw` 把桌宠抛出去后它悬在半空不动；
   `/position?mode=line|spring` 的跟随定时器同理失效。
2. `move()` / `update()` / 演出状态字段与主线程 20ms tick 并发读写，数据竞争。

全项目原先没有任何 `invokeMethod` / `QueuedConnection`，即零封送。

## Options Considered

**A. 钩子改「只置标志/入队」，主线程 tick 消费。**
与项目已有的「后台→主线程队列」范式一致；但每个钩子要各写一套队列 + 消费点，
返回值（`ok/removed/playbackId` 等）全部丢失，调用方语义被迫重设计。改动面最大。

**B. `QMetaObject.invokeMethod` + BlockingQueuedConnection 直调。**
最直观；但 PySide6 下经 `Q_RETURN_ARG` 回传 Python 对象脆弱，异常与超时都要自己包，
且 `invokeMethod` 依赖槽注册，mixin 方法易踩注册问题。

**C. Signal 桥：GUI 线程建 `QObject` 子类，持一个 `Signal(object)`；
工作线程 emit 后阻塞等结果事件。**（已选）

Qt 官方跨线程范式：跨线程 emit 会被自动排成队列事件在接收者线程执行。
信号载荷直接带可调用对象，返回值/异常经 `_UiCall` 搬回调用线程。

## Decision

采用 C。落点：

- `pet/service_hooks.py`：`UiBridge(QObject)` + `_UiCall`（含 15s 超时与
  `cancelled` 迟滞取消）；`ServiceHooksMixin._ui_call(fn, *a, **kw)`——
  **已在 GUI 线程则短路直调**，否则经桥封送并阻塞等结果。
- `_api_hooks()` 用 `_UI_HOOKS` 白名单把触达 Qt 的 13 个钩子统一包装；
  `whisper`（30–60s 网络）不封送——其上屏动作在 `chat.py` 内单独 `_ui_call`。
- `桌宠.py` `PetWindow.__init__` 建桥（一行装配）。

关键不变量：

1. 调用方线程阻塞等待，GUI 线程执行——**没有反向等待边**，不会死锁；
2. GUI 线程内调用短路直调，本地技能/菜单路径零开销；
3. 15s 超时后 `_UiCall` 置 `cancelled`，GUI 线程恢复执行时跳过过期动作，
   避免「接口已报 500，动作几十秒后照样发生」。

## Impact

- 修复：`/throw`、`/position` 的定时器归位；跨线程 Qt 访问全部消除。
- 新增 7 条冒烟用例（含跨线程封送的返回值/执行线程/异常回抛三向断言）。
- 性能：HTTP 调用方多等一次事件循环往返（微秒级）；`/anim` 等返回
  `playbackId` 的语义不变。

## Risks

- **残余竞态（Low）**：GUI 线程已过 `cancelled` 检查进入 `fn` 时调用方恰好在
  15s 边界超时——动作仍会执行而接口报 500。取消令牌无协作检查点的固有局限，
  窗口极窄，登记 TASK-007。
- **桥缺失退化（已缓解）**：`_ui_bridge` 缺失时 `_ui_call` 退回直调并
  `pet_log.warn` 留痕（生产路径 `__init__` 恒建桥，不可达）。
- **模态对话框**：GUI 线程进嵌套事件循环时封送仍可被投递（Qt 模态循环会处理
  队列事件），但耗时操作会拉长 HTTP 等待——最坏触发 15s 超时取消。
