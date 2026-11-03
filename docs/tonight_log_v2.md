# 通宵日志 v2 —— v0.4.0「业务时序图 · Graph RAG 问答 · CI 架构体检」

> 2026-11-03 深夜 → 11-04 凌晨。绝对自主模式，三大跨越级任务 + TDD + 零回归。
> 基线：v0.3.0（commit ec6fe44），345 tests。收工：**444 tests 全绿**，ruff/mypy 干净，
> 版本 0.4.0，真机（本机 Ollama qwen2.5:7b / qwen2.5-coder 系）端到端验证通过。

## 总览

| 任务 | 交付物 | 新增测试 | 真机验证 |
|---|---|---|---|
| 一：业务时序图 | `grapher/tracer.py`、`describer/sequencer.py`、schema/sequence.py、渲染器、§九 | 46 | ✅ flask_demo doc 全链路，AI 时序图 degraded=False |
| 二：Graph RAG 问答 | `web_viewer/retrieval.py`、`web_viewer/chat.py`、`/api/chat`、前端问答面板 | 22 | ✅ 真机问答 + 节点芯片点击高亮 + 截图 |
| 三：GitHub Action | `grapher/diff.py`、CLI `check`、`.github/workflows/arch-check.yml`、`docs/github_action.md` | 25 | ✅（契约测试锁死 YAML 与 CLI flag 一致性） |

345 基线测试全部保留通过（3 处文档断言因章节重编号同步更新，属有意变更，见下）。

## 任务一：从拓扑图到业务时序图

**架构决策**：模型**不直接写 Mermaid**。让 LLM 输出结构化 JSON
（participants + 有序 messages），经 Pydantic 严格 schema + 语义校验器
（flow 数 = 链数、引用必须已声明）后，由引擎确定性渲染 `sequenceDiagram`
（autonumber、sync/reply/async 三种箭头、独立转义规则）。这与项目
「AI 推理 / 引擎统计」分区哲学同构：**模型给业务命名，引擎保证语法为真**。

- `grapher/tracer.py`：入口点（is_entry ∪ detect_entry_points，回退到流边源）
  按 PageRank 排序，沿 `calls`/`instantiates` 做 DFS（邻接按 -PageRank(dst) 排序），
  环按路径剪枝，深度/每入口链数/总链数三重预算，(entry, steps) 去重，全确定性。
- `describer/sequencer.py`：单请求 JSON 模式 + 语义校验；自愈阶梯
  [n, max(2,n//2), max(1,n//3)]，失败链路收缩并携带 correction pressure；
  全败时**确定性降级** `engine_sequence_flow`（模块即泳道、消息即调用边，
  `source="engine"`），章节挂【引擎统计】标签——文档永远不缺时序图、永不撒谎。
- 渲染：`## 九、核心业务时序图`；原九/十章顺延为十/十一。`doc` 导出
  `sequence-NN.mmd`；摘要新增 `sequences_count` / `sequences_degraded`。

**踩坑**：
1. `int(3*0.5)=1` 让阶梯预算从 [3,2,1] 塌成 [3,1]——改用显式 `n//2`+floor-2 规则。
2. 旧测试 `test_pipeline_doc_deterministic` 两条 doc run 共享一个 fake 响应队列，
   时序阶段把第二条 run 的 describer 响应吃掉 → 打桩 `describe_sequences`
   （模块级缝，与 `run_inference_sync` 同款）。
3. tracer 曾引用未导入的 `SymbolGraph`（惰性注解侥幸不炸）+ B023 循环闭包告警
   → 重构为 `_trace_entry` helper。**教训：新增模块必须当场过 ruff，不要攒。**

**真机**：`moray-scribe doc examples/flask_demo`（qwen2.5:7b）→
`sequences: 2 flow(s), degraded=False`，`sequence-00.mmd`：
`Api->>Auth: register()` / `Auth->>Model: User()`，中文泳道标签正确。
flask_demo 只有 2 条**静态可确认**的根到叶链路——login/logout 等路由的调用
（`user.check_password()` 等对象参数方法）按 R-2 保守原则不伪造边。
「至少 3 个流程」由示例工程（恰好 3 条链）证明能力；数量永远由图谱真实性决定。

## 任务二：图谱 RAG 智能问答

**检索管线**（`retrieval.py`，纯标准库）：中英分词（CJK bigram）→ 词法打分
（id/名 3 分、模块/路由 2 分、文档/签名 1 分，**种子判定只看词法**）→
反向 BFS（影响面）+ 单跳正向 → PageRank 决定优先级 → 预算未满按直接邻居补足
→ 源码切片（`is_relative_to` 防穿越）。

**后端**（`chat.py`）：`OllamaChatBackend` 用 urllib 调 `/api/chat`——
协议白名单 http/https、**禁重定向**（防重定向改写固定路径）、JSON 回答 +
引用节点过滤（⊆ 检索子图、≤8、去重）；非 JSON 回答降级透传（`degraded: true`）。
`serve.py` 新增 `do_POST /api/chat`（64KB body 上限、question ≤2000 字符、
501/502/500 语义分明）。Mimosa 安全扫描曾拦截测试文件的 urllib SSRF 模式——
改为环回 `http.client` 直连，真实防护落在后端本身。

**前端**：💬 FAB + 抽屉式问答面板（`C` 快捷键、`Esc` 关闭）、回答气泡 +
节点芯片（点击 → `selectNode`+`focusNode` 画布高亮，全局 `[data-node]` 事件委托）、
上下文统计行、`?chat=1` 深链。

**踩坑（含一个真 bug）**：
1. **`[hidden]` 被 CSS 覆盖**：`.chat-panel { display:flex }` 击穿了 UA 的
   `[hidden]{display:none}`——面板「隐形地」盖住右下角，FAB 点不到。
   修复：`.chat-panel[hidden] { display:none }`。这是浏览器实测抓到的，
   单元测试测不出（无 JS 测试设施）。
2. 检索初版 PageRank 加权让所有节点得分>0 全成种子 → 种子判定与排序分离。
3. BFS 每跳 cap 会饿死预算 → `min(per_hop, 剩余预算)` + 邻居补足；补足方向
   必须尊重 `reverse_hops=0`（否则影响面查询把调用方漏进来）。
4. IAB 内嵌浏览器的合成指针点不到 fixed 元素（locator/cua 全超时、DOM click
   正常）——判定为自动化环境怪癖而非产品缺陷，用 DOM 事件 + headless Edge
   截图完成验证。

**真机**：`/api/chat`（qwen2.5:7b）问「如果我要修改 login 登录逻辑，会影响到
哪些模块？」→ 中文回答 + 引用 `flask_demo.auth.login`/`flask_demo.auth`
（24 节点/33 边/4 切片子图）；UI 芯片点击 → 侧边栏选中 login ✓。截图见
`C:\Users\莫\AppData\Local\Temp\chat-ui.png / chat-ui-open.png`。

## 任务三：GitHub Action 架构体检

- `grapher/diff.py`：`diff_graphs`（死代码两侧现场重算——旧基线可比；
  枢纽 = 两侧 top-N 并集上的出局/相对腰斩/新进榜）+ `render_diff_markdown`
  （PR 评论体）。CLI `moray-scribe check`：`--fail-on dead|hub|any|none`
  （默认 dead 挡红灯）、`--report`、退出码 0/1/2 语义。
- 工作流：PR 时 merge-base 现算基线 + head 现算当前 → diff → `gh pr comment`
  → 退出码落盘 → `if: always()` 终审。权限最小化（contents:read +
  pull-requests:write），不碰 `pull_request_target`，无 Secret。
- **契约测试**（`test_workflow_config.py`，8 条）：YAML 触发器（PyYAML 把裸
  `on:` 解析成 `True`——两边都要查）、权限、fetch-depth 0、CLI flag 一致性、
  评论步骤 `if: always()`、退出码传播、报告 artifact。workflow 与 CLI 永不失联。
- `docs/github_action.md`：接入、参数语义、基线策略（现算/提交/Release 三档）、
  降噪、安全、局限，全写透。

## 测试与质量门

- 345 → **444**（+99：tracer 13、schema 9、sequence 渲染 9、sequencer 12、
  markdown 时序 6、管线集成 3、检索 13、chat/endpoint 11、diff 10、check CLI 7、
  workflow 8 — 含基线断言微调 3 处）。
- 章节重编号的**有意变更**：`test_markdown_has_all_sections`、
  `test_pipeline_doc`（九→十）、`test_pipeline_inference`（十→十一）同步更新。
- ruff 全绿（32→0，含 tracer 重构）；mypy strict（schema+describer）全绿。
- `dev` extras 新增 `pyyaml`；版本 0.3.0 → 0.4.0（test_cli 断言同步）。

## 遗留与建议

- 时序图/问答依赖静态边：动态分发（反射、字符串路由）不在链上，仍走「已知缺口」
  或 `--infer`；这是设计选择而非缺陷。
- 检索是词法+图结构（无向量库）；建议 v0.5 若引入 embedding，可复用
  `RetrievalIndex` 的子图骨架。
- `?chat=1` 深链与 IAB 截图超时的组合未再深究（headless Edge 路径已够用）。
- 未提交（沿用 v0.3 惯例）：等创始人醒来验收后一起 commit/push。
