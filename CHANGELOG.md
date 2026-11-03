# Changelog

## [0.3.0] - 2026-11-03

「看得见、懂框架、跑得快」：交互式拓扑、领域特化、极限压测与性能自愈。

### 新特性

- **交互式 Web UI（`web_viewer/`）**：零依赖 Canvas 力导向拓扑。拖拽/缩放/点击、
  搜索、按类型筛选、隐藏死代码、导出 PNG；侧边栏展示职责、PageRank、度数、
  路由徽章、AI 推断理由、调用关系与源码片段。红色流动虚线 = `[AI推断]`，
  橙色粗箭头 = ORM 数据流。支持深链 `?select=<id>&settle=N&hide_dead=1&q=...`。
  `serve.py` 仅用标准库，源码接口限制在扫描根内（防目录穿越）。
- **HTTP 路由识别**：Flask/FastAPI 装饰器（`@app.route`、`@router.get` …）解析为
  `RouteInfo`（路径 + 方法）；Django `urls.py` 的 `path()/re_path()/url()`（含
  `Class.as_view()`）经 import 别名解析并挂到视图节点。装饰器路由优先，urlconf 不覆盖。
- **ORM 与数据流**：`__tablename__`、`Column/mapped_column/relationship`、Django
  字段构造器或 `models.Model` 继承 → `orm_model` 标签；静态可确认的 ORM 调用
  （`Model.query.get`、`session.commit`、已解析的 `save`）生成 `EdgeKind.DATA_FLOW` 边。
  pydantic 的 `Field` 明确排除，避免把 schema 误判为表。
- **文档新增两节**：`ARCHITECTURE.md` 增加「API 路由表」「数据流向图」（确定性生成，
  Mermaid 粗箭头 + 数据库节点）。开启 `--infer` 时额外输出**确定性**的
  「AI 推断补全的调用关系」虚线图，保证虚线图永远存在。
- **压测与基准工具**：`benchmarks/pipeline_profile.py`（分阶段计时 + RSS 采样）、
  `benchmarks/concurrency_sweep.py`（1/2/4/8 并发扫描 + GPU 显存采样）、
  `benchmarks/showcase.py`（三项目架构聚合 + LLM 一句话点评）、
  `benchmarks/make_demo_gif.py`（真实终端输出渲染 GIF）。
- **CLI 调参**：`doc` 新增 `--infer-max-calls`、`--infer-concurrency`、`--infer-batch-size`。
- **`doc` 同时导出图谱**：`pruned-graph.json` 与 `ARCHITECTURE.md` 并列写出，
  可直接喂给 Web UI；`graph`/`doc` 的图谱 metadata 记录项目名与扫描根。

### 性能

- **移除 numpy / scipy / networkx 依赖**，PageRank 改为纯 Python 幂迭代，
  平行边语义与旧实现逐位一致（差值 1.7e-18）。30 节点小图 graph 阶段
  0.992 s → **0.003 s**；冷启动 1.25 s → **0.15 s**；峰值 RSS 97.4 → **67.2 MB**。
- **`prune()` 不再深拷贝整图**：只复制最终保留的节点/边，复用已有 PageRank。
  django（29,857 节点）prune 峰值从 683.7 MB 降到 467–472 MB。
- **`doc` 建图后释放 scanner bundles**，LLM 阶段不再叠加扫描期内存。
- 已知缺口在文档侧截断到 200 条 + 汇总行，避免超大项目的 ARCHITECTURE.md 膨胀。

### 修复

- **推断响应归一化**：小模型常把签名写进 `src`（`login()`）或用反引号包裹，
  旧解析器逐字匹配导致 `rejected` 全灭。现去反引号/去尾部 `(...)` 后再做唯一匹配，
  歧义仍拒绝。演示项目 `merged 0 → 5`、`rejected 5 → 0`。
- **`selected` 统计修正**：改为「真正送入模型的调用数」，不再等于候选池大小。
- **虚线语义硬保证**：渲染器强制非 `inferred` 的 dashed/dotted 边为实线，
  「虚线 = AI 推断」不再被模型风格选择破坏。
- **Django 根路由**：`path("", views.home)` 的空路径归一化为 `/`，修复其触发的
  schema 校验错误中断整个扫描的问题。
- **urlconf 快速拒绝**：仅在 callee 末段为 `path/re_path/url` 时才渲染参数文本，
  避免对每个调用做昂贵的源码渲染。

### 变更

- 依赖：移除 `networkx`、`numpy`、`scipy`（**破坏性**：运行不再需要它们；
  如有下游脚本直接 import 请自行声明）。
- Schema：`SymbolNode`/`RawSymbol` 新增 `route`；`RawSymbolBundle` 新增 `url_patterns`；
  `EdgeKind` 新增 `DATA_FLOW`；新增 `RouteInfo`、`UrlPatternRef`。
- Prompt：新增领域图例（`r`/`o`/`df`）与 R8/R9（节点必须带 `symbol_ids` 与 `module`；
  `dashed` 仅限 `inf=1`）。

### 测试与文档

- 283 → **345** 个测试（覆盖路由/Django urlconf/ORM 数据流/推断归一化/渲染兜底/
  Web UI 源码接口/PageRank 语义与依赖隔离）。
- 新增 [docs/ultimate-benchmark.md](docs/ultimate-benchmark.md)、
  [docs/tonight_log.md](docs/tonight_log.md)、[SHOWCASE.md](SHOWCASE.md) 与
  `examples/flask_demo/`；README 换用 GIF + Web UI 截图 + v0.3 能力总览。

## [0.2.0] - 2026-10-31

动态调用死角攻克 + 大规模压力测试。

### 新特性

- **静态动态启发式（默认开启）**：scanner 记录 `self.x = ClassA()` / `setattr(self, "x", ...)`
  赋值与 `importlib.import_module` / `__import__` 字面量动态导入；grapher 据此解析
  `self.x.method()`（跨模块属性类型追踪）与 `self.method()`（本类方法分派）。工厂函数
  返回类型未知、同属性多类型歧义时保持保守不猜。
- **LLM 动态推断（`--infer` 开启）**：新增 `describer/inferencer.py`。静态层解析不了的
  悬空调用按 PageRank 排序、批量组装（源码片段 + 候选符号清单），经 `asyncio` 并发
  （`AsyncOllamaClient`，信号量限流）发送本地模型，输出 Pydantic 校验的 `InferredEdge`。
  high 置信度合入主图（`inferred=True`），medium/low 记入已知缺口。
- **渲染层适配**：推断边以虚线 `-.->` 渲染并带 `[AI推断]` 标签；含推断边的图自动追加
  图例（实线=静态确认，虚线=AI 推断）；`ARCHITECTURE.md` 新增「八、AI 动态推断」章节
  （合入边全表 + 依据 + 中低置信度明细 + token 统计）。
- **自愈降级梯子**：describer 在大图上失败时自动降级重试（`max_nodes` → 一半 → 60），
  大项目跑通率显著提升。
- **上下文修复**：同步/异步 Ollama 客户端按 prompt 长度自动设置 `num_ctx`
  （[4096, 16384]），修复大图 prompt 被 Ollama 默认 4096 上下文静默截断导致的
  schema 崩坏（flask 200 节点视图复现并确认根因）。
- **Token 用量统计**：describer 与推断阶段的 prompt/completion token 计数进入
  `ScribeResult.summary` 与压力测试报告。
- **压力测试基建**：`benchmarks/stress_test.py`（psutil 内存采样 + 分阶段计时 +
  推断边抽样审计），`pyproject.toml` 新增 `benchmark` extra。

### 变更

- R-2 约定演进：scanner 仍保持 callee 链原样不重写，但 grapher 的 `self.*` 全黑名单
  收窄为"有静态证据才解析，无证据流入 LLM 推断池"。
- `SymbolEdge` 新增 `inferred` / `confidence` / `reason` 字段；`MermaidEdge` 新增
  `inferred` 字段；`RawSymbolBundle` 新增 `attr_bindings` / `dynamic_imports`。
- prompt view 边增加 `inf` 标记（仅推断边携带），SYSTEM_PROMPT 增加推断边渲染规则。

### 测试

- 241 → 283（新增 scanner/grapher 动态启发式、inferencer 合图、AsyncOllamaClient、
  渲染图例、端到端推断管线、自愈梯子共 42 个用例；`test_call_self_skipped` 按
  v0.2 新约定更新为 `test_call_self_resolved_via_binding`）。

### 已知限制（v0.2.0）

- `getattr(obj, name)` 反射调用、跨文件 monkey-patch、字符串拼接的动态 import 仍不解析
  （留在已知缺口）。
- LLM 推断只覆盖「图内有同名候选目标」的悬空调用；纯外部位标的调用不消耗 token。
- 推断有误报率（flask 实测 13 条合并边中 1 条 dict.pop 同名误匹配 ≈ 92% 精度），
  一切推断边以虚线 + `[AI推断]` 明示，不合入"引擎统计"。

## [0.1.0] - 2026-10-28

首个公开版本。

### 新特性

- **四大模式**：`scan` / `graph` / `diagram` / `doc`——从符号扫描到完整架构文档。
- **五层分析**：libcst 静态扫描 → 图论分析（PageRank 枢纽 / 死代码 / 入口点）→
  Token 预算裁剪 → 本地大模型语义生成 → Mermaid + Markdown 渲染。
- **严格契约**：大模型只能输出经 Pydantic 强校验的 JSON，不写任何代码——零幻觉。
- **AI / 引擎分区**：`ARCHITECTURE.md` 显式区分"模型推断"与"引擎确定性统计"，可复现、可信任。
- **MCP 接入**：6 个 tool（`scribe_health` / `scribe_scan` / `scribe_graph` /
  `scribe_doc` / `scribe_read_artifact` / `scribe_list_symbols`）可挂到 Cursor 等宿主。
- **本地优先**：Ollama + 你的模型，代码不出本机；7B 模型即可跑通。

### 已知限制（v0.1.0）

0. **硬件依赖明显**——doc 阶段真实生成需要 7B 模型在合理速度下运行。纯 CPU 推理
   下，1500 行项目约需 1-3 分钟，6000 行项目约需 5-15 分钟。若机器负载高
   （如 IDE 已占满 CPU），耗时可能翻倍。**这不是缺陷，是任务本身的推理成本。**
   推荐配置：GPU 或 M 系列芯片，或空闲的高性能 CPU。详见
   [README 已知限制](README.md#已知限制v010)。
1. **仅支持 Python**——扫描基于 libcst，是 Python 专用。JS / TS / Go 需等未来版本的
   tree-sitter 后端。*踩到场景*：扫描非 Python 目录会找不到可解析文件。
2. **动态调用不解析**——`getattr` / `eval` / `importlib.import_module` 构造的调用边
   会被漏掉。*踩到场景*：插件式 / 反射重度的代码库，`ARCHITECTURE.md` 的"已知缺口"
   章节会提示这些位置，需人工确认。
3. **多继承 `super()` 歧义跳过**——`class A(B, C)` 里 `super().foo()` 若 B、C 都有
   `foo`，边会被跳过（宁缺毋滥，不猜）。*踩到场景*：多继承 + 同名方法的框架代码。
4. **大项目需调参**——符号数 > 5000 的项目，默认 `max_nodes=200` 会裁掉关键模块。
   *踩到场景*：单体巨石仓库。建议 `--max-nodes 400` 并调大
   `MORAY_SCRIBE_MAX_OUTPUT_TOKENS`。
5. **CI / 自动化里跑 doc 需预留时间预算**——doc 阶段生成不可中断且依赖本机推理
   速度，流水线里应显式设 `MORAY_SCRIBE_REQUEST_TIMEOUT_S`（或 `--timeout`）并预期
   2-15 分钟。*踩到场景*：CI 默认 5 分钟超时直接杀掉任务。（第 0 条讲硬件选择，
   本条讲自动化超时预算。）
6. **类图优先级低于架构图**——首图强制 `flowchart`（架构图），`classDiagram` 只能作
   `diagrams[1:]` 的可选细节图。*踩到场景*：想直接要一张 UML 类图时需手动调整。
7. **R5-2 粒度检测可能误伤**——若项目真由 > 60% 的独立子系统组成（罕见），会被误判为
   "1:1 映射"并要求合并。*踩到场景*：高度解耦的微内核 / 插件集合。当前无 override 参数。

### 路线图（v0.2.0，planned）

- two-phase 生成（模块级摘要 → 全局架构），降低大项目单次生成压力
- JS / TS 支持（tree-sitter 后端）
- HTTP / SSE MCP transport
- 自动质量评分

[0.1.0]: https://github.com/moray/moray-scribe/releases/tag/v0.1.0
