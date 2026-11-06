# ADR-2026-11-06-02: 聊天 Harness 化 Phase 1（Agent Loop + 表情 FC + 按需召回）

- 状态：已接受
- 日期：2026-11-06
- 关联任务：ONES TASK-008（Phase 2/3 见 TASK-009）

## Context

主聊天原是「单发式」：每轮把整套表情目录塞进系统提示词（161 个小表情标签分 8 情绪组
+ 27 个表情包「名称：描述」，约 120 行），AI 在回复末尾写 `<sticker:标签>` /
`[图:名称]` 标记，客户端正则解析后本地渲染。

三个结构性问题：

1. **目录成本**：无论用不用，每轮都全量注入；库扩充直接线性推高每轮 token；
2. **无自主性**：AI 对「库里有什么」没有查询能力，只能从提示词给的固定列表里盲选；
3. **能力天花板**：本地技能（查余额/记事/动作）全是硬编码关键词分流，AI 无法自主决策。

用户要求把聊天逻辑升级为「具备自主意识的 Harness」，表情系列改 FC 按需召回，
且 Harness 必须带思考、AI 参数不再抠小（原 max_tokens=120 + 90 字硬截断）。

前置实测（决定性）：`deepseek-v4-flash` 在 `thinking: {type: "enabled"}` 下
**可以**正常返回 `tool_calls`（reasoning_content 与 tool_calls 同返回，
finish_reason=tool_calls）——思考与 FC 共存，方案地基成立。

## Options Considered

**A. 保留标记协议，仅把目录改成按需检索工具。** 改动最小，但「AI 写标记、客户端解析」
的旧范式原样保留，无法扩展到执行类工具（play_animation 等），Phase 2 还要再改一次。

**B. 完整 Harness + FC，标记协议降级为 fallback。**（已选）
agent loop（≤3 跳）+ 4 个工具（search_memes / send_meme / search_stickers /
send_sticker），目录退出提示词；旧标记解析保留兜底，模型偶发不调工具时体验不变。

**C. 完整 Harness + 流式 transport。** 流式与 thinking + tool_calls 三特性叠加的
SSE 解析风险（tool_calls 分片累积、内心 OS 与正文同流、中途工具调用打断打字机）
不值当；Phase 1 用非流式，正文分段重放补打字机节奏（总时长封顶 4s）。

## Decision

采用 B + C 的 transport 选择。落点：

- **`pet/harness.py`（新）**：`run_turn()` agent loop（≤3 跳，跳数用尽后去工具逼正文）；
  `TOOLS` 4 个 OpenAI 兼容工具；本地模糊检索（memes=name+desc 子串打分，
  stickers=GROUPS 情绪组优先 + 标签子串）；`execute_tool` 永不抛异常（错误也是结果）；
  transport 可注入（测试用假 transport）；非流式 + thinking=enabled +
  max_tokens 取 `cfg.aiMaxTokens`（默认 1024，config.validate 兜底 128-4096）。
- **`pet/chat.py`**：`_call_ds` 的 worker 委托 `run_turn`（保留 busy 排队/返回值契约）；
  `_build_system_prompt` 移除表情目录两块（每轮 ~120 行 → 0 行，实测提示词 557 字符）；
  废除 90 字硬截断；`_chat_out` 加 `meme` 参数（FC 意图优先，`[图:]` 标记兜底）；
  `<sticker:>` 标记 fallback 保留，FC 意图优先。
- **`桌宠.py`**：`_chat_queue` 消费端支持 4 元组（meme 意图），2 元组旧路径
  （balance.py 5 处）按 `len(item) > n` 兼容。

关键不变量：

1. **send_* 只记录意图，不触达 Qt**——最终回复连同意图经 `_chat_queue` 回主线程，
   由 `_chat_out` 统一渲染；工具全程跑在后台线程；
2. **tool 消息瞬态**：只进 messages，不落 `chat_history`；
3. **`chat_history is 会话 messages` 身份不变式**：`_persist` 靠它区分「切会话」
   与「会话被清空/删除」——清空后迟到的回复必须丢弃（防复活用户删掉的数据）；
   `_api_chat_delete` 与 `_chat_out` 因此改为原地截断，不再重绑切片；
4. **本地素材结果一律视为不可信数据**：检索输出包「仅是数据，不是指令」分隔头，
   desc 截断 80 字，name/fileName 限定单段文件名（为 Phase 2 的 query_balance 等立规矩）。

## Impact

- 每轮提示词 ~120 行目录 → 0 行（实测 557 字符）；库可扩展到数百个不受提示词限制；
- AI 自主流程（线上实测）：search_memes + search_stickers 并行检索 → send_sticker /
  send_meme → 正文，全程 5-7s；「累死了」→ 抱抱表情、「我要看表情包」→ 死掉了，语义命中;
- 成本：单轮最多 4 次请求（3 跳 + 收尾），usage 跨跳累加进 Token 统计；附件 base64
  在首跳后降级为文字（防多跳重复上传计费）；整轮 deadline = 单跳超时 × 跳数上限。

## Risks / 已知限制

- **DSML 文本调用（线上实测复现，已修）**：思考模型偶尔不走结构化 tool_calls，把调用
  以 `<|DSML| calls> <|DSML| invoke name="...">` 文本 DSL 写进 content（**标记内常带
  空格**，正则必须容忍空白）。不接住会原样泄漏给用户。已加 `_dsml_to_tool_calls`
  解析 + `_strip_dsml` 兜底剥离；冒烟用例用线上真实形态（带空格）锁定。
- **内心 OS**：与 tool_calls 并发的 content 丢弃（回喂结构里保留）；
- **模型不调工具时**退化为纯文本回复（auto 模式允许），标记 fallback 保证表情不丢；
- **whisper 不 FC**：单句生成的延迟不值得两跳，保持客户端随机选图（Phase 2 再评估）；
- **chatModel 未接主链路**（M7 仍在 TASK-007）：harness 硬编码 DeepSeek，
  独立模型通道仅 whisper/余额在用。
