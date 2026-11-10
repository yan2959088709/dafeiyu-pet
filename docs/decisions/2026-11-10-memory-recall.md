# ADR-2026-11-10-03: Stage 2a 记忆相关度召回

- 状态：已接受
- 日期：2026-11-10
- 关联任务：ONES TASK-013（Stage 2 第一捻，承接 TASK-010/012）

## Context

长期记忆此前只有「存储」：每轮对话 LLM 静默抽 1 条事实，上限 20 条，
系统提示词把前 20 条**全量**注入。三个问题：

1. 事实变多后，全量注入线性推高每轮 token，且稀释当前语境；
2. 没有「最近提过什么」的权重，旧事实与新事实同权；
3. 没有访问计数，无法做后续的巩固/遗忘。

TASK-013 蓝图（2026-11-09 登记）要求：字符二元组相关度 × 时间衰减 ×
命中计数，top-K 注入，legacy 纯字符串 facts 零迁移。

## Options Considered

**A. 引入 jieba 分词或向量库。** 相关度更准，但新增依赖，facts ≤20 条
时收益不成立。冻结到 Stage 2b（facts 过百再上）。

**B. 字符二元组重叠 + exp 时间衰减 + 命中加权。**（已选）
零新依赖，中文短事实足够；纯函数可单测；改写只发生在注入前。

**C. 把 facts 结构改成对象列表（text/ts/hits）。** 语义最干净，但
`self.facts` 是纯字符串列表，被记忆面板、harness 记忆工具、测试直接
读写；改结构 = 大面积迁移 + 破坏零迁移要求。否决。

## Decision

采用 B + 旁路元数据。

**`pet/memory_recall.py`（新，纯函数）**
- `Fact(text, ts, last_access, hits)`；`score_facts(facts, context, now, k)`。
- 相关度 = 查询与 fact 的字符二元组重叠率（分母是 fact 的二元组数，
  避免长 query 稀释短 fact）。
- 衰减 = `exp(-ln2 · age / 7d)`；最终分 = 相关度 × (0.5 + 0.5·衰减)
  × (1 + 0.1·hits)。
- 空 context 或全不相关 → 按 `ts/last_access/插入顺序` recency 保底，
  不返回空（K=8 的保底比整段空掉更有用）。

**`pet/memory.py`**
- `self.facts` 仍是纯字符串列表（零迁移）；元数据存旁路字典
  `self._fact_meta: {text: {ts, last_access, hits}}`，随 `_save_mem`
  写进 `pet_memory.json` 的 `fact_meta`。
- `parse_facts(data)` 同时接受 legacy 字符串列表与未来 dict 列表；
  `_sync_fact_meta` 按当前 facts 对齐（删掉的丢弃、缺的补默认）。
- `_recall_facts(context, k)`：取最近 6 条消息 + 入参拼语境 → 召回 →
  命中回写 hits/last_access 并落盘（在 `_mem_lock` 内）。
- `_add_fact` 落盘时写 `ts`；冲突消解删旧事实时同步删其元数据。

**注入点**
- `chat._build_system_prompt(context_text="")` 用 top-K 替代 `facts[:20]`；
  `_call_ds` 传入当前 `user_msg` 作为语境。
- `heartbeat._speak` 也经 `_recall_facts("")`（空语境走 recency 保底）。
- 新增配置 `memoryRecallK`（默认 8，1-20）；设置页「全局」加输入项。

## Impact

- 每轮记忆段从「前 20 条」变为「相关 top-8」（默认），长记忆库不再线性
  推高提示词。
- `pet_memory.json` 新增 `fact_meta` 字段；旧文件缺该字段按空处理，
  下次保存自动补齐。facts 结构本身不变。
- 冒烟新增 4 条：纯函数召回、legacy 解析、命中回写落盘、提示词 top-K。

## Risks

- **可能漏掉「当前不相关但重要」的事实**：K=8 + 衰减保底 + hits 加权
  缓解；用户可在设置页调大 K（最大 20，等于原全量行为）。
- **召回改写读-改-写**：回写在 `_mem_lock` 内，与 `_add_fact`/UI 编辑
  共用同一把锁；多线程下以锁为准。
- **二元组对长事实不敏感**：facts 上限 20 条且每条 ≤30 字，暂可接受；
  超长/多语言混合留 Stage 2b。
- **recency 保底会带上旧事实**：无语境时优先新事实，但仍有概率注入旧
  事实；这是「宁多勿空」的取舍，已记录。

## Deferred

每日摘要、巩固/遗忘、夜间反思、人格演化（Stage 2b/3）；embedding 检索
（facts 过百再上）；事实抽取从「每轮 1 条」改为批量/去噪。
